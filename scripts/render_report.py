#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_report.py — 把日报 Markdown 渲染成单文件 HTML，零第三方依赖。

为什么需要它：render_html.sh 原先只会调 answer-me-with-html 的 am.mjs。
那意味着这个 skill 必须和另一个技能同时安装、且装了 Node 才能出 HTML，
少一样就整条流水线失败。渲染是「交付层」，不该成为硬依赖。

本脚本用标准库实现 am 扩展 Markdown 的一个子集（日报用到的全部语法）：
    前置元数据   --- / title / subtitle / source ---
    分节标题     ## A 今日速览
    键值面板     ```kv cols=3
    强调框       ```callout info 今日看点
    GFM 表格     含对齐行
    列表 / 引用 / 分隔线 / 行内粗体、代码、链接

用法:
    python scripts/render_report.py 报告.md 报告.html
    python scripts/render_report.py 报告.md 报告.html --dark   # 强制深色

退出码:
    0 = 写出成功
    2 = 参数错误或输入文件不存在
"""

import argparse
import html as html_mod
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 行内语法

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
STRIKE = re.compile(r"~~(.+?)~~")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def inline(text: str) -> str:
    """行内 Markdown → HTML。先转义，再替换，避免用户内容注入标签。"""
    out = html_mod.escape(text, quote=False)
    out = INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    out = STRIKE.sub(lambda m: f"<del>{m.group(1)}</del>", out)
    out = LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', out)
    return out


# ---------------------------------------------------------------- 块级解析

FENCE = re.compile(r"^```(\w+)?\s*(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")


def split_front_matter(text: str) -> tuple[dict, str]:
    """拆出 --- 包裹的元数据。没有就用空字典。"""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return meta, text
    for raw in lines[1:end]:
        if ":" in raw:
            k, v = raw.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, "\n".join(lines[end + 1:])


def parse_table(lines: list[str]) -> tuple[str, list[str]]:
    """吃掉一个 GFM 表格，返回 (html, 剩余行)。"""
    rows = []
    i = 0
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append(lines[i].strip())
        i += 1
    rest = lines[i:]

    def cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [c.strip() for c in row.split("|")]

    if len(rows) < 2 or not TABLE_SEP.match(rows[1]):
        # 没有对齐行就不是表格，按段落处理
        return f"<p>{inline(' '.join(rows))}</p>", rest

    head = cells(rows[0])
    aligns = []
    for c in cells(rows[1]):
        left, right = c.startswith(":"), c.endswith(":")
        aligns.append("center" if left and right else "right" if right else "left")

    out = ['<div class="table-wrap"><table>', "<thead><tr>"]
    for idx, c in enumerate(head):
        al = aligns[idx] if idx < len(aligns) else "left"
        out.append(f'<th style="text-align:{al}">{inline(c)}</th>')
    out.append("</tr></thead><tbody>")
    for row in rows[2:]:
        out.append("<tr>")
        for idx, c in enumerate(cells(row)):
            al = aligns[idx] if idx < len(aligns) else "left"
            out.append(f'<td style="text-align:{al}">{inline(c)}</td>')
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out), rest


def parse_kv(body: str, cols: int) -> str:
    items = []
    for line in body.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        items.append((k.strip(), v.strip()))
    if not items:
        return ""
    out = [f'<div class="kv" style="--cols:{max(1, min(cols, 4))}">']
    for k, v in items:
        out.append(f'<div class="kv-item"><div class="kv-k">{inline(k)}</div>'
                   f'<div class="kv-v">{inline(v)}</div></div>')
    out.append("</div>")
    return "".join(out)


CALLOUT_TONE = {
    "info": ("提示", "c-info"),
    "warn": ("注意", "c-warn"),
    "err": ("问题", "c-err"),
    "ok": ("结论", "c-ok"),
}


def parse_callout(body: str, info: str) -> str:
    """```callout warn 标题 → 带色块与标题的强调框。"""
    parts = info.split()
    tone = parts[0] if parts and parts[0] in CALLOUT_TONE else "info"
    default_title, cls = CALLOUT_TONE[tone]
    title = " ".join(parts[1:]) if len(parts) > 1 else default_title

    body_lines = body.splitlines()
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    inner = render_blocks(body_lines)
    return (f'<aside class="callout {cls}"><div class="callout-title">{inline(title)}</div>'
            f'<div class="callout-body">{inner}</div></aside>')


def parse_flow(body: str) -> str:
    """```flow → 用箭头串起来的流程链，纯文本也能看。"""
    steps = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        # 支持 "a -> b: 说明" 与 "a → b" 两种写法
        m = re.match(r"^(.+?)\s*(?:->|→)\s*(.+?)(?::\s*(.+))?$", line)
        if m:
            steps.append((m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()))
        else:
            steps.append((line, "", ""))
    if not steps:
        return ""
    out = ['<ol class="flow">']
    for a, b, label in steps:
        arrow = f' <span class="flow-arrow">→</span> {html_mod.escape(b)}' if b else ""
        note = f' <span class="flow-label">{inline(label)}</span>' if label else ""
        out.append(f"<li><span class=\"flow-node\">{html_mod.escape(a)}</span>{arrow}{note}</li>")
    out.append("</ol>")
    return "".join(out)


def render_list(lines: list[str]) -> str:
    ordered = bool(re.match(r"^\s*\d+[.)]\s+", lines[0])) if lines else False
    tag = "ol" if ordered else "ul"
    out = [f"<{tag}>"]
    for line in lines:
        m = LIST_ITEM.match(line)
        if m:
            out.append(f"<li>{inline(m.group(2))}</li>")
        elif line.startswith("  ") and out:
            # 续行并进上一项
            out[-1] = out[-1][:-5] + " " + inline(line.strip()) + "</li>"
    out.append(f"</{tag}>")
    return "".join(out)


def render_blocks(lines: list[str]) -> str:
    """块级渲染主循环。"""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 围栏代码块
        fm = FENCE.match(stripped)
        if fm:
            lang = (fm.group(1) or "").lower()
            info = fm.group(2) or ""
            i += 1
            body: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # 跳过收尾围栏
            raw = "\n".join(body)
            if lang == "kv":
                cols = 3
                mc = re.search(r"cols=(\d+)", info)
                if mc:
                    cols = int(mc.group(1))
                out.append(parse_kv(raw, cols))
            elif lang == "callout":
                out.append(parse_callout(raw, info))
            elif lang == "flow":
                out.append(parse_flow(raw))
            else:
                out.append(f"<pre><code>{html_mod.escape(raw)}</code></pre>")
            continue

        # 标题
        hm = HEADING.match(stripped)
        if hm:
            level = len(hm.group(1))
            text = hm.group(2)
            if level <= 2:
                # 二级标题开新分节卡片，由 build_html 按这个固定标记重组
                out.append(f'<section class="panel"><h2>{inline(text)}</h2></section>')
            else:
                out.append(f"<h{level}>{inline(text)}</h{level}>")
            i += 1
            continue

        # 表格
        if stripped.startswith("|"):
            tbl, rest = parse_table(lines[i:])
            out.append(tbl)
            i = len(lines) - len(rest)
            continue

        # 分隔线
        if re.match(r"^---+$", stripped):
            out.append("<hr>")
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote>{render_blocks(buf)}</blockquote>")
            continue

        # 列表
        if LIST_ITEM.match(line):
            buf = []
            while i < len(lines) and (LIST_ITEM.match(lines[i]) or
                                      (lines[i].startswith("  ") and lines[i].strip())):
                buf.append(lines[i])
                i += 1
            out.append(render_list(buf))
            continue

        # 段落（连续非空行合并）
        buf = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not (
            FENCE.match(lines[i].strip()) or HEADING.match(lines[i].strip())
            or lines[i].strip().startswith(("|", ">", "---")) or LIST_ITEM.match(lines[i])
        ):
            buf.append(lines[i].strip())
            i += 1
        text = " ".join(buf)
        cls = ' class="lead"' if text.startswith("Lead:") else ""
        if cls:
            text = text[len("Lead:"):].strip()
        out.append(f"<p{cls}>{inline(text)}</p>")

    return "\n".join(out)


CSS = """
:root {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1b1f24;
  --muted: #5b6673;
  --line: #e3e7ec;
  --accent: #2f6fed;
  --info-bg: #eef4ff; --info-line: #c7dafc;
  --warn-bg: #fff7e8; --warn-line: #f2d49b;
  --err-bg: #fdf0ef;  --err-line: #f3c2bd;
  --ok-bg: #eefaf2;   --ok-line: #b7e3c6;
  --code-bg: #f1f3f6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171b; --panel: #1c2027; --text: #e7ebf0; --muted: #9aa5b3;
    --line: #2b3138; --accent: #6d9bff;
    --info-bg: #1a2436; --info-line: #2c3f5e;
    --warn-bg: #2a2318; --warn-line: #5b4726;
    --err-bg: #2b1d1d;  --err-line: #5e3532;
    --ok-bg: #16281e;   --ok-line: #2c5340;
    --code-bg: #232830;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 20px 72px;
  background: var(--bg); color: var(--text);
  font: 16px/1.72 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1000px; margin: 0 auto; }
header.doc { margin-bottom: 28px; }
header.doc h1 { font-size: 30px; line-height: 1.3; margin: 0 0 8px; letter-spacing: -0.01em; }
header.doc .sub { color: var(--muted); font-size: 15px; }
header.doc .meta { color: var(--muted); font-size: 13px; margin-top: 10px; }
.lead {
  font-size: 17px; color: var(--text);
  background: var(--panel); border-left: 3px solid var(--accent);
  border-radius: 8px; padding: 14px 18px; margin: 0 0 24px;
}
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 22px 24px 8px; margin: 0 0 20px;
}
.panel h2 { font-size: 19px; margin: 0 0 14px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
h3 { font-size: 16px; margin: 22px 0 8px; }
h4, h5, h6 { font-size: 15px; margin: 18px 0 6px; }
p { margin: 0 0 12px; }
hr { border: 0; border-top: 1px solid var(--line); margin: 20px 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
  background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
  font: 13.5px/1.5 ui-monospace, SFMono-Regular, "Cascadia Code", Consolas, monospace;
}
pre {
  background: var(--code-bg); padding: 14px 16px; border-radius: 8px;
  overflow-x: auto; margin: 0 0 14px;
}
pre code { background: none; padding: 0; }
blockquote {
  margin: 0 0 14px; padding: 8px 16px; color: var(--muted);
  border-left: 3px solid var(--line);
}
ul, ol { margin: 0 0 14px; padding-left: 22px; }
li { margin: 4px 0; }
.table-wrap { overflow-x: auto; margin: 0 0 16px; -webkit-overflow-scrolling: touch; }
table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
th {
  text-align: left; font-weight: 600; color: var(--muted); font-size: 13px;
  letter-spacing: 0.02em; white-space: nowrap; border-bottom-width: 2px;
}
tbody tr:hover { background: color-mix(in srgb, var(--accent) 4%, transparent); }
.kv {
  display: grid; grid-template-columns: repeat(var(--cols, 3), minmax(0, 1fr));
  gap: 1px; background: var(--line); border: 1px solid var(--line);
  border-radius: 10px; overflow: hidden; margin: 0 0 16px;
}
.kv-item { background: var(--panel); padding: 14px 16px; }
.kv-k { font-size: 12.5px; color: var(--muted); margin-bottom: 4px; }
.kv-v { font-size: 16px; font-weight: 600; word-break: break-word; }
.callout { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin: 0 0 16px; }
.callout-title { font-weight: 600; margin-bottom: 6px; font-size: 15px; }
.callout-body > :last-child { margin-bottom: 0; }
.c-info { background: var(--info-bg); border-color: var(--info-line); }
.c-warn { background: var(--warn-bg); border-color: var(--warn-line); }
.c-err  { background: var(--err-bg);  border-color: var(--err-line); }
.c-ok   { background: var(--ok-bg);   border-color: var(--ok-line); }
.flow { list-style: none; padding-left: 0; }
.flow li { padding: 6px 0; }
.flow-node {
  background: var(--code-bg); border-radius: 6px; padding: 2px 8px;
  font: 13.5px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.flow-arrow { color: var(--muted); margin: 0 4px; }
.flow-label { color: var(--muted); font-size: 13.5px; margin-left: 6px; }
footer.doc { color: var(--muted); font-size: 12.5px; text-align: center; margin-top: 28px; }
@media (max-width: 620px) {
  body { padding: 24px 12px 48px; }
  .panel { padding: 18px 16px 4px; }
  .kv { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  header.doc h1 { font-size: 23px; }
}
"""


def build_html(md_text: str, meta_override: dict | None = None) -> str:
    meta, body = split_front_matter(md_text)
    if meta_override:
        meta = {**meta, **{k: v for k, v in meta_override.items() if v}}

    raw_html = render_blocks(body.splitlines())

    # 按 <section class="panel"><h2>…</h2></section> 的占位重组：
    # 每个 h2 之后的内容都属于该分节，直到下一个 h2。
    pieces = re.split(r'<section class="panel"><h2>(.*?)</h2>\s*</section>', raw_html, flags=re.S)
    # pieces[0] 是第一个 h2 之前的内容（lead / 段落）
    head_html = pieces[0]
    sections: list[tuple[str, str]] = []
    for idx in range(1, len(pieces), 2):
        title = pieces[idx]
        content = pieces[idx + 1] if idx + 1 < len(pieces) else ""
        sections.append((title, content))

    title = meta.get("title") or "日报"
    subtitle = meta.get("subtitle") or ""
    source = meta.get("source") or ""

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html_mod.escape(title)}</title>",
        f"<style>{CSS}</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        '<header class="doc">',
        f"<h1>{inline(title)}</h1>",
    ]
    if subtitle:
        parts.append(f'<div class="sub">{inline(subtitle)}</div>')
    if source:
        parts.append(f'<div class="meta">{inline(source)}</div>')
    parts.append("</header>")

    if head_html.strip():
        parts.append(head_html)

    for sec_title, content in sections:
        parts.append(f'<section class="panel"><h2>{sec_title}</h2>{content}</section>')

    date_part = f' · {html_mod.escape(meta["date"])}' if meta.get("date") else ""
    parts.append(f'<footer class="doc">由 ai-project-scout 内置渲染器生成{date_part}</footer>')
    parts.append("</div>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="把日报 Markdown 渲染成单文件 HTML（零依赖）")
    ap.add_argument("input", help="输入 Markdown 路径")
    ap.add_argument("output", help="输出 HTML 路径")
    ap.add_argument("--title", default="", help="覆盖 frontmatter 里的 title")
    ap.add_argument("--subtitle", default="", help="覆盖 subtitle")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"[错误] 找不到输入文件: {src}", file=sys.stderr)
        return 2

    md_text = src.read_text(encoding="utf-8")
    out_html = build_html(md_text, {"title": args.title, "subtitle": args.subtitle})

    dst = Path(args.output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="\n") as f:
        f.write(out_html)

    print(f"已渲染: {dst}  ({len(out_html):,} 字节)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
