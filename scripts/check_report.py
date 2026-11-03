# -*- coding: utf-8 -*-
"""check_report.py — 校验日报是否把 raw_latest.json 里的项目写全了。

背景：日报的表格是 agent 手写的，写 6 行还是 26 行没有任何环节会发现。
本脚本把「raw 里有多少条」和「报告里出现多少条」硬性比对，不一致就退出码 1。

它同时是去重的**唯一提交口**：scout.py 抓取时只把新条目写进 raw 的 `new_ids`，
不写 seen_ids.json。只有本脚本校验通过（= 日报真的写出来了）才把条目提交进
raw 同目录的 seen_ids.json。这样翻译 / 渲染失败时，条目不会被提前标成「已见」
并从榜单上消失 30 天。

用法:
    python scripts/check_report.py ~/.ai-project-scout/output/2026-11-03.md
    python scripts/check_report.py 报告.html
    python scripts/check_report.py 报告.md --raw 别的/raw_latest.json
    python scripts/check_report.py 报告.md --allow-missing 2   # 允许漏 2 条
    python scripts/check_report.py 报告.md --no-commit         # 只校验、不提交去重

退出码:
    0 = 通过（全部命中且分节行数一致，去重已提交）
    1 = 未通过（漏条 / 分节行数不符 / 文件读不到，去重未提交）
"""

import argparse
import html as html_mod
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from seen_store import commit as commit_seen_ids

SEEN_FILE_NAME = "seen_ids.json"

SOURCE_LABELS = {
    "product_hunt": "Product Hunt",
    "indie_hackers": "Indie Hackers",
}
# 分节标题里识别来源用的关键词
SOURCE_SECTION_RE = {
    "product_hunt": re.compile(r"product\s*hunt", re.I),
    "indie_hackers": re.compile(r"indie\s*hackers", re.I),
}

DEFAULT_RAW = Path(os.getenv("AI_SCOUT_OUTPUT_DIR",
                             str(Path.home() / ".ai-project-scout" / "output"))) / "raw_latest.json"


def normalize(s: str) -> str:
    """把文本压成「去空白、去标点壳、小写」的形式，用于宽松包含比对。"""
    s = html_mod.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)                 # HTML 标签
    s = re.sub(r"\]\([^)]*\)", "]", s)             # markdown 链接的 URL 部分
    s = re.sub(r"https?://\S+", " ", s)            # 裸 URL
    s = re.sub(r"[*_`#>\[\]()【】「」『』:：,，.。!！?？|·\-—–]", " ", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def name_candidates(name: str):
    """生成用于比对的候选串，从严格到宽松。

    报告里常把产品名截短（如原名 "Usage for Claude + ChatGPT 4.0"
    写成 "Usage for Claude + ChatGPT"）。逐级去掉末尾词再比对，
    既能容忍缩写，又不会把完全没写的条目蒙混过去。
    """
    yield normalize(name)
    tokens = [t for t in re.split(r"\s+", (name or "").strip()) if t]
    while len(tokens) > 1:
        tokens = tokens[:-1]
        cand = normalize(" ".join(tokens))
        if len(cand) >= 8:
            yield cand


def read_report(path: Path) -> tuple[str, bool]:
    """返回 (报告纯文本, 是否 HTML)。读不到直接抛错。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    is_html = path.suffix.lower() in (".html", ".htm")
    if is_html or "<body" in raw[:2000].lower():
        # 去掉 script/style，再去标签
        body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        text = re.sub(r"(?s)<[^>]+>", "\n", body)
        return html_mod.unescape(text), True
    return raw, False


def count_section_rows(md_text: str) -> dict:
    """按「分节标题 → 表格数据行数」统计 markdown。

    只统计标题下、下一个标题前的表格行；表头与分隔行不计。
    """
    rows = {k: 0 for k in SOURCE_LABELS}
    current = None
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current = None
            for key, pat in SOURCE_SECTION_RE.items():
                if pat.search(stripped):
                    current = key
                    break
            continue
        if current and stripped.startswith("|"):
            # 跳过 markdown 表格的分隔行 |---|---|
            if re.fullmatch(r"\|[\s:|-]+\|?", stripped):
                continue
            rows[current] += 1
    # 每节减去表头一行
    for k in rows:
        if rows[k] > 0:
            rows[k] -= 1
    return rows


def commit_seen(raw: dict, hit_ids: set, raw_path: Path) -> tuple[int, int, Path]:
    """把「已确认写进报告」的条目提交进去重库。

    只提交真正命中的 ID：`--allow-missing` 放过的漏条不提交，下次还能重推。
    stale（上次快照沿用）条目本来就在库里，也不重复提交。
    返回 (新增条数, 库内总数, 文件路径)。
    """
    seen_path = raw_path.parent / SEEN_FILE_NAME
    date_str = str(raw.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        date_str = datetime.now().strftime("%Y-%m-%d")
    added, total = commit_seen_ids(seen_path, hit_ids, date_str)
    return added, total, seen_path


def main():
    ap = argparse.ArgumentParser(description="校验日报条数是否与 raw_latest.json 一致")
    ap.add_argument("report", help="日报文件路径（.md 或 .html）")
    ap.add_argument("--raw", default=None,
                    help="raw_latest.json 路径（默认取报告同目录下那份，否则用全局输出目录）")
    ap.add_argument("--allow-missing", type=int, default=0,
                    help="允许漏掉的条数（默认 0，即一条都不许漏）")
    ap.add_argument("--no-rows", action="store_true",
                    help="只做逐条命中校验，不比对分节表格行数")
    ap.add_argument("--no-commit", action="store_true",
                    help="只校验，不把条目提交进去重库（排查时用）")
    args = ap.parse_args()

    report_path = Path(args.report).expanduser()
    # 默认优先用报告同目录的 raw（多目录并存时最不容易配错）
    if args.raw:
        raw_path = Path(args.raw).expanduser()
    else:
        sibling = report_path.parent / "raw_latest.json"
        raw_path = sibling if sibling.exists() else DEFAULT_RAW

    print("=== 日报条数校验 ===")
    print(f"报告: {report_path}")
    print(f"原始: {raw_path}")

    try:
        report_text, is_html = read_report(report_path)
    except Exception as e:
        print(f"\n[✗] 读不到报告文件: {type(e).__name__}: {e}")
        return 1

    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"\n[✗] 读不到 raw_latest.json: {type(e).__name__}: {e}")
        return 1

    items = raw.get("items") or []
    if not items:
        print("\n[!] raw 里没有条目（total=0），无需校验。")
        print("[3] 去重提交: 无条目，跳过")
        return 0

    by_source = {}
    for it in items:
        by_source[it.get("source", "unknown")] = by_source.get(it.get("source", "unknown"), 0) + 1
    dist = " / ".join(f"{SOURCE_LABELS.get(k, k)} {v}" for k, v in by_source.items())
    print(f"raw 共 {len(items)} 条（{dist}），日期 {raw.get('date', '-')}"
          + ("，⚠ 含上次快照（stale）" if any(i.get("stale") for i in items) else ""))

    # ---- 校验 1：逐条命中（报告正文里是否出现该条目名）----
    haystack = normalize(report_text)
    missing = []
    loose = []
    hit_ids = set()
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        cands = list(name_candidates(name))
        if not any(c and c in haystack for c in cands):
            missing.append(name)
            continue
        hit_ids.add(str(it.get("id") or ""))
        if cands[0] not in haystack:
            loose.append(name)  # 只匹配到截短后的名字

    hit = len(items) - len(missing)
    mark = "✓" if not missing else "✗"
    print(f"\n[1] 逐条命中: {hit}/{len(items)} {mark}")
    if loose:
        print(f"    （其中 {len(loose)} 条为截短名称命中，视为已写入）")
        for n in loose[:5]:
            print(f"      缩略: {n}")
        if len(loose) > 5:
            print(f"      …… 另有 {len(loose) - 5} 条")
    if missing:
        for n in missing[:30]:
            print(f"    缺失: {n}")
        if len(missing) > 30:
            print(f"    …… 另有 {len(missing) - 30} 条未列出")

    # ---- 校验 2：分节表格行数（仅 markdown）----
    row_fail = []
    if args.no_rows:
        print("\n[2] 分节行数: 已跳过（--no-rows）")
    elif is_html:
        print("\n[2] 分节行数: HTML 无法可靠归属到来源，跳过（用 chrome 人工看版）")
    else:
        rows = count_section_rows(report_text)
        parts = []
        for key, expected in by_source.items():
            label = SOURCE_LABELS.get(key, key)
            got = rows.get(key, 0)
            ok = got == expected
            parts.append(f"{label} {got}/{expected} {'✓' if ok else '✗'}")
            if not ok:
                row_fail.append(f"{label} 表格 {got} 行，应为 {expected} 行")
        print(f"\n[2] 分节行数: " + " | ".join(parts))

    # ---- 结论 ----
    ok = (len(missing) <= args.allow_missing) and not row_fail
    if ok:
        if missing:
            print(f"\n结果: 通过（放行 {len(missing)} 条漏条，只提交命中的 {len(items) - len(missing)} 条）")
        else:
            print("\n结果: 通过（条数与 raw 一致）")
        # 通过 = 日报确实写出来了 → 这时才提交去重（scout.py 抓取时不写 seen_ids）
        if args.no_commit:
            print("[3] 去重提交: 已跳过（--no-commit）")
        else:
            stale_ids = {str(it.get("id")) for it in items if it.get("stale")}
            if raw.get("new_ids") is None:   # 老版本 raw 不带 new_ids，退化为「所有命中且非 stale」
                commit_ids = {i for i in hit_ids if i not in stale_ids}
            else:
                pending = {str(i) for i in raw.get("new_ids") or []}
                commit_ids = (hit_ids & pending) - stale_ids
            if not commit_ids:
                print("[3] 去重提交: 无新条目（raw.new_ids 为空或已提交过）")
            else:
                added, total, seen_path = commit_seen(raw, commit_ids, raw_path)
                print(f"[3] 去重提交: 新增 {added} 个 ID，库内共 {total} 个 → {seen_path}")
                print("    这些条目下次抓取不会再推送（有效期见 --seen-ttl-days）")
        return 0

    print("\n结果: 未通过")
    if len(missing) > args.allow_missing:
        print(f"  - 漏条 {len(missing)} 条（允许 {args.allow_missing} 条）")
    for f in row_fail:
        print(f"  - {f}")
    print("  修法: 按 raw_latest.json 补齐缺失条目后重跑本脚本；不要写「节选」或只取前 N 条。")
    print("[3] 去重提交: 未提交（校验未通过，这批条目下次抓取还会出现，不会丢）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
