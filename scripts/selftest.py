# -*- coding: utf-8 -*-
"""selftest.py — 离线自检，把踩过的坑固化成断言。

默认不联网，约 1-2 秒跑完。覆盖：
  时区解析、AI 关键词过滤、网络重试策略、两阶段去重（seen_store / write_raw）、
  报告校验与去重提交、渲染脚本的 am.mjs 定位。

用法:
    python scripts/selftest.py              # 离线全跑
    python scripts/selftest.py --network    # 追加真实抓取冒烟（需要外网）
    python scripts/selftest.py -k dedup     # 只跑名字里含 dedup 的用例
    python scripts/selftest.py -v           # 失败时打印完整 traceback

退出码:
    0 = 全部通过
    1 = 有用例失败
"""

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

PY = sys.executable or "python"
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 工具函数

def load_scout(tmp: Path):
    """把 scout 指到临时输出目录后载入（模块级常量在 import 时求值，所以用 reload）。"""
    os.environ["AI_SCOUT_OUTPUT_DIR"] = str(tmp)
    import scout
    return importlib.reload(scout)


class Skip(Exception):
    """环境不具备（例如没有 Git Bash），不算失败。"""


def find_bash():
    """定位 Git Bash。Windows 上 PATH 里的 bash 可能是 WSL，被安全策略拦住。"""
    env = os.environ.get("AI_SCOUT_TEST_BASH")
    if env and Path(env).exists():
        return env
    cands = [shutil.which("bash")]
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                 r"C:\Program Files", r"C:\Program Files (x86)"):
        if not root:
            continue
        cands += [str(Path(root) / "Git" / "bin" / "bash.exe"),
                  str(Path(root) / "Git" / "usr" / "bin" / "bash.exe")]
    for c in cands:
        if c and Path(c).exists() and "system32" not in c.lower():
            return c
    return None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run(args, env=None, input_text=None):
    e = os.environ.copy()
    e.update(env or {})
    p = subprocess.run(args, cwd=str(ROOT), env=e,
                       input=(input_text or "").encode("utf-8") if input_text else None,
                       capture_output=True)
    return subprocess.CompletedProcess(args, p.returncode,
                                       _decode(p.stdout), _decode(p.stderr))


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fixture_raw(ids_names):
    return {
        "date": "2026-11-02", "total": len(ids_names),
        "sources": {"product_hunt": {"ok": True}},
        "new_ids": [i for i, _ in ids_names],
        "items": [{"id": i, "source": "product_hunt", "name": n, "url": "https://x/" + i,
                   "description": "d", "published_at": "2026-11-01", "signals": {}}
                  for i, n in ids_names],
    }


def fixture_report(items):
    lines = ["## B Product Hunt 新 AI 产品（%d 个）" % len(items), "",
             "| 产品 | 中文简介 | 上架 | 链接 |", "|---|---|---|---|"]
    for it in items:
        lines.append(f"| {it['name']} | 简介 | 2026-11-01 | [打开]({it['url']}) |")
    return "\n".join(lines) + "\n"


ATOM_ENTRY = """<entry><id>https://www.producthunt.com/posts/p{i}</id>
<title>Agent Product {i}</title>
<link href="https://www.producthunt.com/posts/p{i}"/>
<published>2026-11-01T00:00:00-08:00</published>
<content>An AI agent for testing number {i}</content></entry>"""

ATOM_FEED = ('<?xml version="1.0" encoding="utf-8"?>'
             '<feed xmlns="http://www.w3.org/2005/Atom">'
             + "".join(ATOM_ENTRY.format(i=i) for i in range(1, 5))
             + "</feed>")

DETAIL_HTML = ('<html><head><meta property="og:description" '
               'content="A much longer description pulled from the product page itself.">'
               '</head></html>')


# ---------------------------------------------------------------- 时区

@case("tz: 偏移量、UTC、非法值")
def _(tmp):
    sc = load_scout(tmp)
    from datetime import datetime, timedelta, timezone

    os.environ.pop("AI_SCOUT_TZ", None)
    local = sc._resolve_tz()
    now = datetime.now()
    assert local.utcoffset(now) == now.astimezone().utcoffset(), "默认应为本地时区"

    os.environ["AI_SCOUT_TZ"] = "+08:00"
    assert sc._resolve_tz().utcoffset(now) == timedelta(hours=8)

    os.environ["AI_SCOUT_TZ"] = "-8"
    assert sc._resolve_tz().utcoffset(now) == timedelta(hours=-8)

    os.environ["AI_SCOUT_TZ"] = "UTC"
    assert sc._resolve_tz().utcoffset(now) == timedelta(0)

    os.environ["AI_SCOUT_TZ"] = "Mars/Base"
    assert sc._resolve_tz().utcoffset(now) == local.utcoffset(now), "非法值应回退本地"
    os.environ.pop("AI_SCOUT_TZ", None)


# ---------------------------------------------------------------- AI 过滤

@case("filter: 真 AI 产品不被误杀")
def _(tmp):
    sc = load_scout(tmp)
    for text in ["Agent Inbox", "Voice agent for Slack", "RAG builder",
                 "Grok 4 is here", "an LLM playground", "AI powered CRM"]:
        assert sc.is_ai_related(text), text


@case("filter: 非 AI 语境不误收")
def _(tmp):
    sc = load_scout(tmp)
    for text in ["Real estate agent", "Travel agent deals", "User agent string",
                 "vector art pack", "fabric rag cloth", "double agent game"]:
        assert not sc.is_ai_related(text), text


# ---------------------------------------------------------------- 重试

def _stub_httpx(sc, behaviour):
    """替换 scout.httpx.request；返回调用计数器。"""
    import httpx
    calls = {"n": 0, "urls": []}

    def fake(method, url, **kw):
        calls["n"] += 1
        calls["urls"].append(url)
        return behaviour(calls["n"], method, url)

    sc.httpx.request = fake
    sc.time.sleep = lambda s: None
    return calls


@case("retry: 前两次断连后重试成功")
def _(tmp):
    import httpx
    sc = load_scout(tmp)

    def behave(n, method, url):
        if n <= 2:
            raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]")
        return httpx.Response(200, text="ok", request=httpx.Request(method, url))

    calls = _stub_httpx(sc, behave)
    r = sc.http_request("GET", "https://example.com/x", label="测试")
    assert r.status_code == 200 and calls["n"] == 3, calls["n"]


@case("retry: 404 不重试")
def _(tmp):
    import httpx
    sc = load_scout(tmp)

    def behave(n, method, url):
        raise httpx.HTTPStatusError("404", request=httpx.Request(method, url),
                                    response=httpx.Response(404))

    calls = _stub_httpx(sc, behave)
    try:
        sc.http_request("GET", "https://example.com/404")
    except httpx.HTTPStatusError:
        pass
    assert calls["n"] == 1, calls["n"]


@case("retry: 5xx 重试、全部失败抛最后一次")
def _(tmp):
    import httpx
    sc = load_scout(tmp)

    def fail(n, method, url):
        req = httpx.Request(method, url)
        raise httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))

    calls = _stub_httpx(sc, fail)
    try:
        sc.http_request("GET", "https://example.com/500")
    except httpx.HTTPStatusError:
        pass
    assert calls["n"] == sc.RETRY_ATTEMPTS == 3, calls["n"]


@case("retry: AI_SCOUT_RETRIES=1 只试一次")
def _(tmp):
    import httpx
    os.environ["AI_SCOUT_RETRIES"] = "1"
    try:
        sc = load_scout(tmp)

        def fail(n, method, url):
            raise httpx.ConnectTimeout("timeout")

        calls = _stub_httpx(sc, fail)
        try:
            sc.http_request("GET", "https://example.com/y")
        except httpx.ConnectTimeout:
            pass
        assert calls["n"] == 1, calls["n"]
    finally:
        os.environ.pop("AI_SCOUT_RETRIES", None)


# ---------------------------------------------------------------- PH 抓取

@case("ph: --no-detail 不发详情页请求")
def _(tmp):
    import httpx
    sc = load_scout(tmp)

    def behave(n, method, url):
        text = DETAIL_HTML if "posts/" in url else ATOM_FEED
        return httpx.Response(200, text=text, request=httpx.Request(method, url))

    calls = _stub_httpx(sc, behave)
    res = sc.fetch_ph(max_items=2, max_age_days=0, fetch_detail=False)
    assert len(res["items"]) == 2, len(res["items"])
    assert calls["n"] == 1, f"只该请求 feed，实际 {calls['n']} 次"
    assert res["items"][0]["description"].startswith("An AI agent"), res["items"][0]


@case("ph: fetch_detail=True 会补全简介")
def _(tmp):
    import httpx
    sc = load_scout(tmp)

    def behave(n, method, url):
        text = DETAIL_HTML if "posts/" in url else ATOM_FEED
        return httpx.Response(200, text=text, request=httpx.Request(method, url))

    calls = _stub_httpx(sc, behave)
    res = sc.fetch_ph(max_items=2, max_age_days=0, fetch_detail=True)
    assert calls["n"] == 3, f"1 次 feed + 2 次详情页，实际 {calls['n']}"
    assert "much longer description" in res["items"][0]["description"]


@case("ph: 旧帖按 --ph-max-age-days 丢弃")
def _(tmp):
    import httpx
    sc = load_scout(tmp)

    old = ATOM_FEED.replace("2026-11-01T00:00:00-08:00", "2026-07-27T00:00:00-07:00")

    def behave(n, method, url):
        return httpx.Response(200, text=old, request=httpx.Request(method, url))

    _stub_httpx(sc, behave)
    res = sc.fetch_ph(max_items=10, max_age_days=7, fetch_detail=False)
    assert res["items"] == [] and res["dropped_old"] == 4, res


# ---------------------------------------------------------------- 去重存储

@case("dedup: 旧版纯列表自动迁移")
def _(tmp):
    import seen_store
    p = tmp / "seen_ids.json"
    write_json(p, ["ph_1", "ph_2"])
    data = seen_store.load(p, default_date="2026-11-02")
    assert data == {"ph_1": "2026-11-02", "ph_2": "2026-11-02"}, data


@case("dedup: 提交幂等 + 剪掉过期记录")
def _(tmp):
    import seen_store
    p = tmp / "seen_ids.json"
    write_json(p, {"ph_old": "2026-09-20", "ph_keep": "2026-10-30"})

    added, total = seen_store.commit(p, ["ph_new", "ph_keep"], "2026-11-02")
    assert added == 1, added                      # ph_keep 已在库，ph_new 才是新增
    assert total == 2, total                      # 40 天前的 ph_old 被剪掉
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "ph_old" not in data and data["ph_new"] == "2026-11-02", data

    added2, total2 = seen_store.commit(p, ["ph_new"], "2026-11-02")
    assert added2 == 0 and total2 == 2, (added2, total2)


@case("dedup: 同日重跑不标 stale，跨日才标")
def _(tmp):
    sc = load_scout(tmp)
    today = "2026-11-02"
    raw = sc.RAW_JSON_FILE
    write_json(raw, fixture_raw([("ph_1", "Alpha"), ("ph_2", "Beta")]))

    out = sc.write_raw([], today, 30)             # 同一天，0 新增
    assert out["total"] == 2 and not any(i.get("stale") for i in out["items"]), out
    assert "同日已抓取" in (out.get("note") or ""), out.get("note")

    prev = fixture_raw([("ph_1", "Alpha")])
    prev["date"] = "2026-10-20"                   # 很久以前的快照
    write_json(raw, prev)
    out = sc.write_raw([], today, 30)
    assert all(i.get("stale") for i in out["items"]), out


# ---------------------------------------------------------------- 报告校验

@case("report: 全量通过才提交去重")
def _(tmp):
    items = [("ph_1", "Alpha Tool"), ("ph_2", "Beta Agent"), ("ph_3", "Gamma RAG")]
    write_json(tmp / "raw_latest.json", fixture_raw(items))
    md = tmp / "report.md"
    md.write_text(fixture_report(fixture_raw(items)["items"]), encoding="utf-8")

    r = run([PY, str(SCRIPTS / "check_report.py"), str(md)],
            env={"AI_SCOUT_OUTPUT_DIR": str(tmp)})
    assert r.returncode == 0, r.stdout + r.stderr
    seen = json.loads((tmp / "seen_ids.json").read_text(encoding="utf-8"))
    assert set(seen) == {"ph_1", "ph_2", "ph_3"}, seen


@case("report: 漏条退出码 1 且不提交")
def _(tmp):
    items = [("ph_1", "Alpha Tool"), ("ph_2", "Beta Agent"), ("ph_3", "Gamma RAG")]
    write_json(tmp / "raw_latest.json", fixture_raw(items))
    md = tmp / "report.md"
    md.write_text(fixture_report(fixture_raw(items[:2])["items"]), encoding="utf-8")

    r = run([PY, str(SCRIPTS / "check_report.py"), str(md)],
            env={"AI_SCOUT_OUTPUT_DIR": str(tmp)})
    assert r.returncode == 1, r.stdout
    assert "未提交" in r.stdout, r.stdout
    assert not (tmp / "seen_ids.json").exists(), "校验失败不该建去重库"


@case("report: --no-commit 只校验")
def _(tmp):
    items = [("ph_1", "Alpha Tool")]
    write_json(tmp / "raw_latest.json", fixture_raw(items))
    md = tmp / "report.md"
    md.write_text(fixture_report(fixture_raw(items)["items"]), encoding="utf-8")

    r = run([PY, str(SCRIPTS / "check_report.py"), str(md), "--no-commit"],
            env={"AI_SCOUT_OUTPUT_DIR": str(tmp)})
    assert r.returncode == 0, r.stdout
    assert not (tmp / "seen_ids.json").exists(), "--no-commit 不该写去重库"


@case("report: --allow-missing 只提交命中条目")
def _(tmp):
    items = [("ph_1", "Alpha Tool"), ("ph_2", "Beta Agent")]
    write_json(tmp / "raw_latest.json", fixture_raw(items))
    md = tmp / "report.md"
    md.write_text(fixture_report(fixture_raw(items[:1])["items"]), encoding="utf-8")

    r = run([PY, str(SCRIPTS / "check_report.py"), str(md),
             "--allow-missing", "1", "--no-rows"],
            env={"AI_SCOUT_OUTPUT_DIR": str(tmp)})
    assert r.returncode == 0, r.stdout
    seen = json.loads((tmp / "seen_ids.json").read_text(encoding="utf-8"))
    assert set(seen) == {"ph_1"}, seen


@case("report: 截短产品名仍算命中")
def _(tmp):
    items = [("ph_1", "Usage for Claude + ChatGPT 4.0")]
    write_json(tmp / "raw_latest.json", fixture_raw(items))
    md = tmp / "report.md"
    md.write_text(fixture_report([{"name": "Usage for Claude + ChatGPT", "url": "https://x/ph_1"}]),
                  encoding="utf-8")
    r = run([PY, str(SCRIPTS / "check_report.py"), str(md)],
            env={"AI_SCOUT_OUTPUT_DIR": str(tmp)})
    assert r.returncode == 0, r.stdout


# ---------------------------------------------------------------- 渲染脚本

def bash_or_skip():
    b = find_bash()
    if not b:
        raise Skip("找不到 Git Bash（可用 AI_SCOUT_TEST_BASH 指定）")
    return b


@case("render: 参数不足退出码 2")
def _(tmp):
    r = run([bash_or_skip(), str(SCRIPTS / "render_html.sh")])
    assert r.returncode == 2, r.returncode


@case("render: 找不到 am.mjs 时明确报错")
def _(tmp):
    bash = bash_or_skip()
    iso = tmp / "iso"
    iso.mkdir()
    script = iso / "render.sh"
    script.write_text((SCRIPTS / "render_html.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "in.md").write_text("# 标题\n", encoding="utf-8")

    r = run([bash, str(script), str(tmp / "in.md"), str(tmp / "out.html")],
            env={"AM_CLI": "/nope", "HOME": str(tmp / "nohome"),
                 "USERPROFILE": str(tmp / "nohome")})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "找不到" in r.stderr, r.stderr


@case("render: AM_CLI 指向技能目录也能用")
def _(tmp):
    bash = bash_or_skip()
    am_dir = Path(os.environ.get("AM_SKILL_DIR",
                                 str(Path.home() / ".workbuddy/skills/answer-me-with-html")))
    if not (am_dir / "scripts/am.mjs").exists():
        raise Skip(f"本机没装 answer-me-with-html：{am_dir}")
    (tmp / "in.md").write_text("---\ntemplate: doc\ntitle: 自检\n---\n\n## A 面板\n\n正文。\n",
                               encoding="utf-8")
    r = run([bash, str(SCRIPTS / "render_html.sh"), str(tmp / "in.md"), str(tmp / "out.html")],
            env={"AM_CLI": str(am_dir)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp / "out.html").exists() and (tmp / "out.html").stat().st_size > 1000


# ---------------------------------------------------------------- 联网冒烟

@case("live: 真实抓取 + 不写去重（需 --network）")
def _(tmp):
    r = run([PY, str(SCRIPTS / "scout.py"), "-p", "5", "-i", "0", "--no-detail"],
            env={"AI_SCOUT_OUTPUT_DIR": str(tmp)})
    assert r.returncode == 0, r.stdout + r.stderr
    raw = json.loads((tmp / "raw_latest.json").read_text(encoding="utf-8"))
    assert raw["total"] > 0, raw
    assert len(raw.get("new_ids") or []) == raw["total"], raw.get("new_ids")
    assert not (tmp / "seen_ids.json").exists(), "scout 不该写去重库"


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="ai-project-scout 离线自检")
    ap.add_argument("-k", "--filter", default="", help="只跑名字含该子串的用例")
    ap.add_argument("--network", action="store_true", help="追加真实抓取冒烟（需外网）")
    ap.add_argument("-v", "--verbose", action="store_true", help="失败时打印完整 traceback")
    args = ap.parse_args()

    selected = [(n, f) for n, f in CASES
                if (args.network or not n.startswith("live:"))
                and args.filter.lower() in n.lower()]

    print(f"=== ai-project-scout 自检 === {len(selected)} 个用例"
          f"{'（含联网）' if args.network else '（离线）'}")
    failed = []
    skipped = []
    started = time.time()
    for name, fn in selected:
        tmp = Path(tempfile.mkdtemp(prefix="scout-selftest-"))
        t0 = time.time()
        try:
            fn(tmp)
            print(f"  ✓ {name}  ({time.time() - t0:.2f}s)")
        except Skip as e:
            skipped.append(name)
            print(f"  - {name}  跳过：{e}")
        except Exception as e:
            failed.append((name, e))
            print(f"  ✗ {name}  {type(e).__name__}: {e}")
            if args.verbose:
                traceback.print_exc()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    ran = len(selected) - len(skipped)
    print(f"\n结果: {ran - len(failed)}/{ran} 通过"
          + (f"，跳过 {len(skipped)}" if skipped else "")
          + f"，{time.time() - started:.1f}s")
    if failed:
        print("失败用例：" + "、".join(n for n, _ in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
