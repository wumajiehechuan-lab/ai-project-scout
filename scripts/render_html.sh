#!/usr/bin/env bash
# 把 answer-me-with-html 的扩展 Markdown 渲染成 HTML 页面。
#
# 用法:
#   bash scripts/render_html.sh <input.md> <output.html>
#   AM_CLI=/path/to/am.mjs bash scripts/render_html.sh report.md report.html
#
# am.mjs 定位顺序（不写死任何本机路径）:
#   1. AM_CLI —— 可以是 am.mjs 文件，也可以是它所在的技能目录
#   2. 与 skill 并排安装的 answer-me-with-html/scripts/am.mjs
#   3. ~/.workbuddy/skills/answer-me-with-html/scripts/am.mjs
#   4. PATH 里的 am 命令（全局安装时）
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "用法: render_html.sh <input.md> <output.html>" >&2
  exit 2
fi

MD="$1"
OUT="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_am() {
  local c="${AM_CLI:-}"
  if [ -n "${c}" ]; then
    # AM_CLI 可以直接指文件，也可以指技能目录
    if [ -f "${c}" ]; then printf '%s\n' "${c}"; return 0; fi
    if [ -f "${c}/scripts/am.mjs" ]; then printf '%s\n' "${c}/scripts/am.mjs"; return 0; fi
    echo "[warn] AM_CLI=${c} 下没有 am.mjs，继续按默认顺序查找" >&2
  fi
  for c in \
    "${SCRIPT_DIR}/../../answer-me-with-html/scripts/am.mjs" \
    "${HOME:-/.nonexistent}/.workbuddy/skills/answer-me-with-html/scripts/am.mjs" \
    "${USERPROFILE:-/.nonexistent}/.workbuddy/skills/answer-me-with-html/scripts/am.mjs"; do
    if [ -f "${c}" ]; then printf '%s\n' "${c}"; return 0; fi
  done
  # 全局安装：am 在 PATH 里
  if command -v am >/dev/null 2>&1; then printf '%s\n' "$(command -v am)"; return 0; fi
  return 1
}

AM="$(resolve_am || true)"

if [ -z "${AM}" ]; then
  cat >&2 <<'MSG'
找不到 answer-me-with-html 的 am.mjs。
  - 装技能：把 answer-me-with-html 放到 ~/.workbuddy/skills/ 下
  - 或指定：export AM_CLI=/绝对路径/am.mjs（也可以给技能目录）
MSG
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "未找到 node：渲染 HTML 需要 Node.js 20+（命令行里 node -v 可自查）" >&2
  exit 1
fi

if [ -n "${AM_VERBOSE:-}" ]; then
  echo "am: ${AM}" >&2
fi

mkdir -p "$(dirname "${OUT}")"
node "${AM}" render - -o "${OUT}" < "${MD}"
