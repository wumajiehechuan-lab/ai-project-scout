# -*- coding: utf-8 -*-
"""scout.py — 纯数据抓取。从 Product Hunt + Indie Hackers 抓取 AI 项目，输出 JSON。

用法:
    python scout.py                        # 默认：PH 30 条, IH 20 条
    python scout.py -p 20 -i 30            # PH 20 条, IH 30 条
    python scout.py --ih-min-followers 30  # IH 最低关注者 30 人
    python scout.py --ih-recency-days 60   # IH 只取近 60 天创建的项目
    python scout.py --seen-ttl-days 14     # 去重 ID 14 天后重新允许出现

环境变量（可选）:
    IH_ALGOLIA_APP_ID   — Indie Hackers Algolia App ID（默认值已内建）
    IH_ALGOLIA_API_KEY   — Indie Hackers Algolia 搜索 key（默认值已内建）
    AI_SCOUT_OUTPUT_DIR  — 输出目录（默认 ~/.ai-project-scout/output）

去重是两阶段的：本脚本只把新条目记到 raw_latest.json 的 `new_ids`，
真正的 seen_ids.json 由 check_report.py 在日报校验通过后写入 ——
否则翻译或渲染一旦失败，条目已被标成「已见」，30 天内再也不会出现。
"""

import argparse, json, os, re, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import xml.etree.ElementTree as ET

# 同目录的 seen_store.py（check_report.py 也用它），显式加路径以免依赖 cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from seen_store import commit as commit_seen_ids  # noqa: E402

# 输出目录：可通过环境变量 AI_SCOUT_OUTPUT_DIR 覆盖；
# 默认放到用户主目录下，避免污染 skill 目录或依赖特定机器路径。
OUTPUT_DIR = Path(os.getenv("AI_SCOUT_OUTPUT_DIR",
                            str(Path.home() / ".ai-project-scout" / "output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SEEN_IDS_FILE = OUTPUT_DIR / "seen_ids.json"
RAW_JSON_FILE = OUTPUT_DIR / "raw_latest.json"
LAST_RUN_FILE = OUTPUT_DIR / "last_run.json"


def _resolve_tz():
    """解析时区：默认跟随系统本地时区，`AI_SCOUT_TZ` 可覆盖。

    历史教训：早期把时区写死 UTC+8，而本机在 PST，日报日期比真实日期早一天，
    标题日期、published_at、去重 TTL 全部跟着错位。
    支持 `local`（默认）、`UTC`、`+08:00` / `-8`，以及 IANA 名（如 Asia/Shanghai）。
    """
    raw = (os.getenv("AI_SCOUT_TZ") or "").strip()
    local = datetime.now().astimezone().tzinfo
    if not raw or raw.lower() in ("local", "system", "auto"):
        return local
    if raw.lower() in ("utc", "z", "gmt"):
        return timezone.utc
    m = re.fullmatch(r"(?:utc|gmt)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", raw, re.I)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(m.group(2)),
                                         minutes=int(m.group(3) or 0)))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(raw)
    except Exception:
        print(f"[warn] 无法识别 AI_SCOUT_TZ={raw!r}，改用系统本地时区")
        return local


TZ = _resolve_tz()

# 去重 ID 的有效期（天）。超过此天数的 ID 会自动从去重库剔除，
# 重新回到候选池。这是修复「跑一次就永久屏蔽 / 榜单一片空白」的关键。
SEEN_TTL_DAYS = 30

# Product Hunt 只取「近 N 天发布」的帖子。
# PH 的 RSS feed 是滚动窗口，但里面会混入几个月前的旧帖（2026-11-02 实测
# 最老一条是 2026-07-27），不过滤就会被当成「今日新增」。0 表示不过滤。
PH_MAX_AGE_DAYS = 7

# Indie Hackers 只取「近 N 天创建」的项目，保证日报露的是新项目而非万年 Top20。
IH_RECENCY_DAYS = 90

# 网络重试：SSL EOF / 连接重置 / 429 / 5xx 都是瞬时故障，重试能救回来。
# 实测 2026-11-02 连续抓取时 PH 会偶发 UNEXPECTED_EOF，一次重试即可恢复。
# 4xx（除 429）不重试 —— 对方明确拒绝，重试只是浪费时间。
RETRY_ATTEMPTS = max(1, int(os.getenv("AI_SCOUT_RETRIES", "3")))
RETRY_BACKOFF = 1.0  # 秒，指数退避：1s、2s、4s……

# AI 相关性判断分两层：强信号（出现即算）+ 弱信号（看语境）。
#
# 演进记录：
#   1) 早期把 agents / automation / rag / vectors 当泛词全量匹配，误收严重
#      （房产 agent、矢量图、抹布）。
#   2) 于是把这些词整个删掉，结果把真 AI 产品一起误杀：2026-11-02 实测 feed
#      被过滤的 24 条里有 8 条是 AI（Display、Clayzo、Agent Inbox、Embage、
#      Click、Termy、Looper、Precise Edit by Ideogram）。
#   3) 改为两层：强信号直接命中；`agent` 这类词先排除明显非 AI 语境，再按 AI 收
#      （召回优先 —— 漏掉一个真产品比多收一个噪声更糟）。
AI_PATTERNS = [
    r"\bai\b", r"\bartificial intelligence\b", r"\bmachine learning\b",
    r"\bllms?\b", r"\bgpts?\b", r"\bchatgpt\b", r"\bcopilots?\b",
    r"\bai agents?\b", r"\bagentic\b", r"\bautonomous agents?\b", r"\bmulti.agent\b",
    r"\bgenerative\b", r"\bclaude\b",
    r"\bopenai\b", r"\blangchain\b", r"\bretrieval.augmented\b",
    r"\bvector (database|db|store|search|embedding)s?\b",
    r"\brag (pipeline|chat|app|system|builder)s?\b",
    r"\bembeddings?\b", r"\bprompts?\b", r"\bfine.tun", r"\bdiffusion\b",
    r"\btransformers?\b", r"\bnlp\b", r"\bllama\b", r"\bmistral\b",
    r"\bgemini\b", r"\bchatbots?\b", r"\bdeepseek\b", r"\bdeep.learning\b",
    r"\bneural\b", r"\bhugging.face\b", r"\bstable.diffusion\b",
    r"\bai.powered\b", r"\bgenai\b", r"\btext.to.(image|video|speech)\b",
    r"\bspeech.to.text\b",
    # 常见模型 / 品牌名：词表里没有就会整条漏掉（Grok、Ideogram 实测漏过）
    r"\bgrok\b", r"\bideogram\b", r"\bmidjourney\b", r"\bperplexity\b",
    r"\bqwen\b", r"\belevenlabs\b", r"\bollama\b", r"\bcomfyui\b", r"\bvllm\b",
    # 其他明确属于 AI 的说法
    r"\bmultimodal\b", r"\bfoundation models?\b", r"\bmodel context protocol\b",
]

# 弱信号 1：agent / agents 单复数。
# 它在 Product Hunt 上绝大多数指 AI Agent，但也能指房产经纪、旅行代理。
AGENT_RE = re.compile(r"\bagents?\b")

# 明显非 AI 的 agent 语境。只在「没命中强信号」时用于排除，
# 所以这里只放天然属于非 AI 领域的词，不放 user / tour / shipping 这类通用词
# （通用词会把正常 AI 文案一起误杀）。
NON_AI_AGENT_CONTEXT = re.compile(
    r"\b(real estate|realtor|travel|flight|hotel|insurance|booking|rental|lease|"
    r"mortgage|propert(y|ies)|broker|brokerage|listing|listings|recruit\w*|"
    r"staffing|patent|customs|secret|double|literary)\b"
    r"|\buser[- ]agents?\b"
)

# 弱信号 2：RAG。缩写通常大写；小写 rag 常指「抹布」，只认大写。
RAG_RE = re.compile(r"\bRAG\b")


def is_ai_related(text: str) -> bool:
    """两层判断：强信号直接命中；弱信号看语境。

    `agent` 在「不像房产 / 旅行 / 保险经纪」时默认按 AI 收，避免整条产品被误杀。
    """
    low = text.lower()
    if any(re.search(p, low) for p in AI_PATTERNS):
        return True
    if RAG_RE.search(text):
        return True
    if AGENT_RE.search(low):
        return not NON_AI_AGENT_CONTEXT.search(low)
    return False

# Indie Hackers Algolia 搜索 API 凭据
# 这些是 IH 前端公开的 search-only key（只读搜索），已硬编码默认值。
# 可通过环境变量覆盖（见仓库根目录 .env.example）：
#   IH_ALGOLIA_APP_ID — Algolia Application ID
#   IH_ALGOLIA_API_KEY — Algolia Search-only API Key
IH_APP_ID = os.getenv("IH_ALGOLIA_APP_ID", "N86T1R3OWZ")
IH_API_KEY = os.getenv("IH_ALGOLIA_API_KEY", "5140dac5e87f47346abbda1a34ee70c3")


# Indie Hackers 英文标签 → 中文。未知标签保留原文，避免丢信息。
# 文档（README / SKILL.md）承诺「映射表见 scout.py 内 TAG_CN」，此处兑现。
TAG_CN = {
    "commitment-full-time": "全职",
    "commitment-side-project": "副业",
    "founders-solo": "单人创业",
    "founders-multiple": "多人创业",
    "funding-bootstrapped": "自筹资金",
    "funding-funded": "已融资",
    "revenue-model-subscription": "订阅制",
    "revenue-model-one-time": "一次性付费",
    "revenue-model-ads": "广告",
    "revenue-model-marketplace": "平台抽成",
    "revenue-model-affiliate": "联盟营销",
    "market-b2b": "面向企业",
    "market-b2c": "面向个人",
    "market-b2b2c": "企业+个人",
    "product-type-saas": "SaaS",
    "product-type-mobile-app": "移动应用",
    "product-type-web-app": "网页应用",
    "product-type-api": "API",
    "product-type-marketplace": "平台",
    "product-type-community": "社区",
    "platform-web": "Web",
    "platform-ios": "iOS",
    "platform-android": "Android",
    "platform-desktop": "桌面端",
}


def translate_tags(tags) -> list:
    """把 IH 英文标签映射为中文，未知标签原样保留。"""
    return [TAG_CN.get(t, t) for t in (tags or [])]


def load_seen_ids(ttl_days: int = SEEN_TTL_DAYS) -> dict:
    """读取去重库，返回 {id: "YYYY-MM-DD"}。

    兼容旧版纯列表格式（无日期），旧 ID 视作今天入库。
    自动剔除超过 ttl_days 天的记录，使其重新进入候选池。
    """
    if not SEEN_IDS_FILE.exists():
        return {}
    try:
        data = json.loads(SEEN_IDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    today = datetime.now(TZ).date()
    if isinstance(data, list):  # 旧格式：纯 ID 列表
        return {i: today.strftime("%Y-%m-%d") for i in data}
    if not isinstance(data, dict):
        return {}
    out = {}
    for i, d in data.items():
        try:
            dd = datetime.strptime(str(d), "%Y-%m-%d").date()
        except Exception:
            dd = today
        if (today - dd).days <= ttl_days:
            out[i] = d
    return out


def clean_html(text: str) -> str:
    text = text.replace("&#x27;", "'").replace("&#x2F;", "/").replace("&amp;", "&")
    text = text.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


# 站点通用营销套话标记：命中则丢弃详情页简介，回退使用 RSS 简介。
GENERIC_DESC_MARKERS = (
    "product hunt", "the best new products", "discover the best",
    "find the best new products", "your new favorite", "sign in", "log in",
)


def http_request(method: str, url: str, label: str = "", attempts: int | None = None, **kwargs):
    """带指数退避的 HTTP 请求，返回已 raise_for_status 的响应。

    只重试瞬时故障：连接类异常（SSL EOF、ConnectionReset、超时）、429、5xx。
    4xx（除 429）直接抛，不重试。
    """
    kwargs.setdefault("timeout", 15)
    tries = attempts or RETRY_ATTEMPTS
    last = None
    for i in range(tries):
        try:
            r = httpx.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            retryable = status is None or status == 429 or status >= 500
            if not retryable or i == tries - 1:
                raise
            wait = RETRY_BACKOFF * (2 ** i)
            print(f"  (重试 {i + 1}/{tries - 1} {label or method} {url[:60]} 于 {wait:.1f}s 后："
                  f"{type(e).__name__})")
            time.sleep(wait)
    raise last  # 理论不可达，兜底


def fetch_ph_detail_description(ph_url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = http_request("GET", ph_url, label="PH 详情页", attempts=2,
                         headers=headers, follow_redirects=True)
        r.raise_for_status()
        for pattern in [
            r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
            r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
        ]:
            m = re.search(pattern, r.text)
            if not m:
                continue
            desc = m.group(1)
            desc = desc.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            desc = desc.replace("&quot;", '"').replace("&#x27;", "'").replace("&#x2F;", "/").strip()
            low = desc.lower()
            # 过短或命中站点通用套话 → 无效，继续尝试下一个 meta，最终回退 RSS 简介
            if len(desc) < 20:
                continue
            if any(mk in low for mk in GENERIC_DESC_MARKERS):
                continue
            return desc
    except Exception:
        pass
    return ""


def _parse_iso_date(s: str):
    """解析 YYYY-MM-DD（取前 10 位），失败返回 None。"""
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def fetch_ph(max_items: int = 30, max_age_days: int = PH_MAX_AGE_DAYS,
             fetch_detail: bool = True) -> dict:
    """从 Product Hunt RSS 抓取，最多 max_items 条。

    max_age_days > 0 时按 published 过滤：早于 N 天发布的帖子直接丢弃。
    PH feed 会混入几个月前的旧帖，不过滤就会被当成「今日新增」。

    fetch_detail=False 时跳过详情页（`--no-detail`），不发多余请求。

    返回 {"items": [...], "matched": AI 相关总数, "dropped_old": 被时间淘汰数}。
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = http_request("GET", "https://www.producthunt.com/feed", label="PH feed", headers=headers)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    cutoff = None
    if max_age_days and max_age_days > 0:
        cutoff = datetime.now(TZ).date() - timedelta(days=max_age_days)

    results = []
    matched = 0
    dropped_old = 0
    for entry in root.findall("atom:entry", ns):
        if len(results) >= max_items:
            break

        id_el = entry.find("atom:id", ns)
        title_el = entry.find("atom:title", ns)
        link_el = entry.find("atom:link", ns)
        pub_el = entry.find("atom:published", ns)
        content_el = entry.find("atom:content", ns)

        post_id = id_el.text.split("/")[-1] if id_el is not None else ""
        title = title_el.text if title_el is not None else ""
        content = content_el.text if content_el is not None else ""
        ph_url = link_el.attrib.get("href", "") if link_el is not None else ""
        published = (pub_el.text or "")[:10] if pub_el is not None else ""

        # 用清洗后的文本判断，避免 HTML 属性（class/rel 名）里的字样干扰
        rss_desc = clean_html(content)
        rss_desc = re.sub(r"\s*Discussion\s*\|\s*Link\s*$", "", rss_desc).strip()

        if not is_ai_related(title + " " + rss_desc):
            continue

        matched += 1
        pub_date = _parse_iso_date(published)
        if cutoff and pub_date and pub_date < cutoff:
            dropped_old += 1
            continue

        results.append({
            "id": f"ph_{post_id}", "source": "product_hunt",
            "name": title.strip(), "url": ph_url,
            "description": rss_desc,
            "published_at": published,
            "signals": {},
        })

    # 逐个抓详情页描述（限速）；fetch_detail=False 时整段跳过，不再猴补丁函数
    if fetch_detail:
        for i, item in enumerate(results):
            detail_desc = fetch_ph_detail_description(item["url"])
            if detail_desc and len(detail_desc) > len(item["description"]):
                item["description"] = detail_desc
            if i < len(results) - 1:
                time.sleep(0.3)

    return {"items": results, "matched": matched, "dropped_old": dropped_old}


def fetch_ih(max_items: int = 20, min_followers: int = 10,
             recency_days: int = IH_RECENCY_DAYS) -> dict:
    """从 Indie Hackers Algolia 搜索，按关注者过滤并只取近 recency_days 天创建的项目。

    关键修复：Algolia 默认按相关度返回稳定集合，会导致「跑一次就永久屏蔽」。
    这里拉取更多候选，按 createdTimestamp 倒序排列后截取最近的，使日报真正露出新项目。

    返回 {"items": [...], "scanned": 原始命中数}。
    """
    url = f"https://{IH_APP_ID}-dsn.algolia.net/1/indexes/products/query"
    headers = {
        "X-Algolia-Application-Id": IH_APP_ID,
        "X-Algolia-API-Key": IH_API_KEY,
        "Content-Type": "application/json",
    }
    # 多拉一些候选，便于按时间排序后取最新
    fetch_n = max(max_items * 4, 100)
    r = http_request("POST", url, label="IH Algolia", headers=headers, json={
        "query": "AI",
        "hitsPerPage": fetch_n,
        "numericFilters": [f"numFollowers>{min_followers}"],
    })
    r.raise_for_status()

    hits = r.json().get("hits", [])
    # 字段存在性校验：Algolia 若改版/改字段名会静默返回 0 条，这里显式报错，避免「无声空白」。
    if hits:
        if "objectID" not in hits[0]:
            raise RuntimeError("Algolia 返回缺少 objectID 字段，接口可能已改版")
        if "numFollowers" not in hits[0]:
            raise RuntimeError("Algolia 返回缺少 numFollowers 字段，接口可能已改版"
                               "（会导致静默 0 条），请检查字段名")
    # 按创建时间倒序（最新在前）
    hits.sort(key=lambda h: int(h.get("createdTimestamp", 0) or 0), reverse=True)

    cutoff_ms = (datetime.now(TZ) - timedelta(days=recency_days)).timestamp() * 1000

    results = []
    for hit in hits:
        ts = int(hit.get("createdTimestamp", 0) or 0)
        if ts and ts < cutoff_ms:  # 已按时间倒序，越过窗口后全部更老，直接停止
            break
        rev = hit.get("revenue", 0)
        if isinstance(rev, bool):
            rev = 0
        results.append({
            "id": f"ih_{hit['objectID']}", "source": "indie_hackers",
            "name": hit.get("name", "").strip(),
            "url": hit.get("websiteUrl", ""),
            "tagline": hit.get("tagline", ""),
            "description": clean_html(hit.get("description", "") or ""),
            "published_at": _ts_to_date(hit.get("createdTimestamp")),
            "signals": {
                "followers": hit.get("numFollowers", 0),
                "revenue": rev,
                "tags": translate_tags(hit.get("_tags", [])),
            },
        })
        if len(results) >= max_items:
            break
    return {"items": results, "scanned": len(hits)}


def _ts_to_date(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=TZ).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return ""


def write_raw(items: list[dict], today: str, ttl_days: int,
              sources: dict | None = None, new_ids: list[str] | None = None):
    """落地 raw_latest.json。

    修复 1：本次 0 新增时，不覆盖上一份有内容的快照，而是保留（必要时标注 stale），
    避免「空跑把历史干货清空、整页空白」。
    修复 2：写入 sources 状态块，agent / 定时任务才能区分「0 条」和「抓取失败」。
    修复 3：新条目 ID 单独记在 new_ids 里，等报告真正写出来（check_report 通过）后
    才由 check_report.py 提交进 seen_ids.json —— 抓取本身不再「提前吃掉」条目。
    """
    sources = sources or {}
    new_ids = new_ids or []
    if items:
        output = {"date": today, "total": len(items), "sources": sources,
                  "new_ids": new_ids, "items": items}
    elif RAW_JSON_FILE.exists():
        try:
            prev = json.loads(RAW_JSON_FILE.read_text(encoding="utf-8"))
            prev_items = prev.get("items") or []
            if prev_items:
                same_day = prev.get("date") == today
                # 同一天重复跑：条目就是今天已经产出的那批，再标 stale 是误报
                shown = ([dict(it) for it in prev_items] if same_day
                         else [{**it, "stale": True} for it in prev_items])
                output = {
                    "date": today, "total": len(shown),
                    "sources": sources,
                    "new_ids": list(prev.get("new_ids") or []) if same_day else [],
                    "note": ("本次抓取无新增（同日已抓取过），沿用当日结果。"
                             if same_day else
                             f"本次抓取无新增（去重库有效期 {ttl_days} 天），"
                             f"展示上次结果并标注过期，避免整页空白。"),
                    "items": shown,
                }
            else:
                output = {"date": today, "total": 0, "sources": sources,
                          "new_ids": [], "items": []}
        except Exception:
            output = {"date": today, "total": 0, "sources": sources,
                      "new_ids": [], "items": []}
    else:
        output = {"date": today, "total": 0, "sources": sources,
                  "new_ids": [], "items": []}

    RAW_JSON_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def write_last_run(today: str, sources: dict, total_new: int, exit_code: int,
                   seen_pending: int = 0):
    """每次运行都写一份 last_run.json，记录来源状态与退出码。

    定时任务只看到「跑完了」是不够的：这份文件让失败可追溯，
    且不污染 raw_latest.json（失败时不覆盖上一次的好快照）。
    `seen_pending > 0` 表示这批条目还没进去重库，等报告落地后才提交。
    """
    payload = {
        "date": today,
        "finished_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "exit_code": exit_code,
        "total_new": total_new,
        "seen_pending": seen_pending,
        "sources": sources,
    }
    try:
        LAST_RUN_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  (警告: last_run.json 写入失败: {e})")


def main():
    parser = argparse.ArgumentParser(description="AI 创业项目侦察员 — 数据抓取")
    parser.add_argument("-p", "--ph-count", type=int, default=30,
                        help="Product Hunt 抓取上限 (默认 30)")
    parser.add_argument("-i", "--ih-count", type=int, default=20,
                        help="Indie Hackers 抓取条数 (默认 20)")
    # 两个名字指向同一目标：--ih-min-followers 是正确名，--ph-min-followers 是旧别名
    parser.add_argument("--ih-min-followers", dest="min_followers", type=int, default=10,
                        help="IH 最低关注者数 (默认 10)")
    parser.add_argument("--ph-min-followers", dest="min_followers", type=int, default=10,
                        help="（旧别名，同 --ih-min-followers）IH 最低关注者数")
    parser.add_argument("--ih-recency-days", type=int, default=IH_RECENCY_DAYS,
                        help=f"IH 只取近 N 天创建的项目 (默认 {IH_RECENCY_DAYS})")
    parser.add_argument("--ph-max-age-days", type=int, default=PH_MAX_AGE_DAYS,
                        help=f"PH 只取近 N 天发布的帖子，0 = 不过滤 (默认 {PH_MAX_AGE_DAYS})")
    parser.add_argument("--seen-ttl-days", type=int, default=SEEN_TTL_DAYS,
                        help=f"去重 ID 有效期天数，过期重新允许出现 (默认 {SEEN_TTL_DAYS})")
    parser.add_argument("--no-detail", action="store_true",
                        help="跳过 PH 详情页抓取（更快但简介更短）")
    parser.add_argument("--commit-now", action="store_true",
                        help="抓完立即提交去重（跳过「校验通过才提交」，仅用于只取数据、不产日报）")
    args = parser.parse_args()

    now_local = datetime.now(TZ)
    today = now_local.strftime("%Y-%m-%d")
    off = now_local.utcoffset() or timedelta(0)
    off_lbl = (f"UTC{'+' if off >= timedelta(0) else '-'}"
               f"{abs(off).seconds // 3600:02d}:{abs(off).seconds % 3600 // 60:02d}")
    tz_name = now_local.tzname() or ""
    tz_label = off_lbl if (not tz_name or tz_name.startswith("UTC")) else f"{tz_name} {off_lbl}"
    print(f"=== AI 项目侦察员 === {today}（时区 {tz_label}）")
    age_label = f"近{args.ph_max_age_days}天" if args.ph_max_age_days else "不限时间"
    print(f"参数: PH≤{args.ph_count}条({age_label}), IH≤{args.ih_count}条, "
          f"IH最低关注者≥{args.min_followers}人, IH近{args.ih_recency_days}天, "
          f"去重有效期{args.seen_ttl_days}天")
    if args.no_detail:
        print("PH 详情页: 跳过")
    print()

    seen_ids = load_seen_ids(ttl_days=args.seen_ttl_days)
    print(f"去重库载入 {len(seen_ids)} 个有效 ID（已剔除过期）")
    all_items = []
    sources = {}

    # Product Hunt
    detail_label = "(跳过详情页)" if args.no_detail else "(含详情页抓取，约 10-15 秒)"
    print(f"[1/2] Product Hunt {detail_label}...")
    try:
        res = fetch_ph(max_items=args.ph_count, max_age_days=args.ph_max_age_days,
                       fetch_detail=not args.no_detail)
        items = res["items"]
        new_items = [i for i in items if i["id"] not in seen_ids]
        all_items.extend(new_items)
        sources["product_hunt"] = {
            "ok": True, "error": None,
            "matched": res["matched"], "kept": len(items),
            "dropped_old": res["dropped_old"], "new": len(new_items),
        }
        age_note = (f"丢弃 {res['dropped_old']} 条超 {args.ph_max_age_days} 天的旧帖"
                    if args.ph_max_age_days else "不过滤发布时间")
        print(f"  AI 相关 {res['matched']} 条，{age_note}，"
              f"保留 {len(items)} 条 -> 新增 {len(new_items)} 条")
        if len(items) < args.ph_count:
            print(f"  (feed 已到末尾：符合条件仅 {len(items)} 条，"
                  f"PH 单页约 50 条，要更多只能放宽 --ph-max-age-days)")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        sources["product_hunt"] = {"ok": False, "error": err, "matched": 0,
                                   "kept": 0, "dropped_old": 0, "new": 0}
        print(f"  失败: {err}")

    # Indie Hackers
    print(f"[2/2] Indie Hackers (近 {args.ih_recency_days} 天新项目)...")
    try:
        res = fetch_ih(max_items=args.ih_count,
                       min_followers=args.min_followers,
                       recency_days=args.ih_recency_days)
        items = res["items"]
        new_items = [i for i in items if i["id"] not in seen_ids]
        all_items.extend(new_items)
        sources["indie_hackers"] = {
            "ok": True, "error": None,
            "scanned": res["scanned"], "kept": len(items), "new": len(new_items),
        }
        print(f"  候选 {res['scanned']} 条 -> 保留 {len(items)} 条 -> 新增 {len(new_items)} 条")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        sources["indie_hackers"] = {"ok": False, "error": err,
                                    "scanned": 0, "kept": 0, "new": 0}
        print(f"  失败: {err}")

    failed = [k for k, v in sources.items() if not v["ok"]]

    # 全部来源失败：不覆盖上一次的好快照，显式以非 0 退出码失败。
    # 否则定时任务只会看到「跑完了」，静默产出一份空报告。
    if sources and len(failed) == len(sources):
        write_last_run(today, sources, 0, exit_code=1)
        print("\n[错误] 所有数据源均抓取失败：")
        for k in failed:
            print(f"  - {k}: {sources[k]['error']}")
        print(f"raw_latest.json 保持上次快照不变（未被空结果覆盖）: {RAW_JSON_FILE}")
        print("排查建议: 网络/代理是否放行 producthunt.com 与 *.algolia.net")
        sys.exit(1)

    # 去重提交延后到这里之外：只把新 ID 记进 raw.new_ids，
    # 由 check_report.py 在报告校验通过后写进 seen_ids.json。
    # 这样翻译 / 渲染 / 校验任何一步失败，条目都不会被提前标成「已见」而静默丢失 30 天。
    new_ids = [it["id"] for it in all_items]
    output = write_raw(all_items, today, args.seen_ttl_days,
                       sources=sources, new_ids=new_ids)

    committed = None
    added_ids = 0
    if args.commit_now and new_ids:
        # 逃生门：只取数据、不产日报时用，直接恢复「抓完即去重」的旧行为
        added_ids, total = commit_seen_ids(SEEN_IDS_FILE, new_ids, today,
                                           ttl_days=args.seen_ttl_days)
        committed = total

    write_last_run(today, sources, len(all_items), exit_code=0,
                   seen_pending=0 if args.commit_now else len(new_ids))

    print(f"\n原始数据: {RAW_JSON_FILE}")
    if new_ids:
        tail = (f"已提交去重，新增 {added_ids} 个 ID" if args.commit_now
                else "去重已挂起，报告校验通过后才提交")
        print(f"本次新增 {len(new_ids)} 个（{tail}）")
    elif output.get("note"):
        print(f"本次新增 0 个；{output['note']}")
    else:
        print("本次新增 0 个")
    if committed is None:
        print(f"去重库载入 {len(seen_ids)} 个 ID（本轮不写入，交由 check_report.py 提交）")
    else:
        print(f"去重库累计 {committed} 个 ID")

    if failed:
        print(f"\n[warn] 部分来源失败: {', '.join(failed)} → 日报将缺少该来源内容")
        for k in failed:
            print(f"  - {k}: {sources[k]['error']}")

    print("下一步: agent 按 SKILL.md 模板翻译成 md → render_html.sh 渲染 → "
          "check_report.py 校验（通过即提交去重）")


if __name__ == "__main__":
    main()
