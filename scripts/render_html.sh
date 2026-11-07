#!/usr/bin/env bash
# 把日报 Markdown 渲染成 HTML。
#
# 用法:
#   bash scripts/render_html.sh <input.md> <output.html>          # 内置渲染器（默认）
#   bash scripts/render_html.sh --am <input.md> <output.html>     # 用 answer-me-with-html
#   RENDER_ENGINE=am bash scripts/render_html.sh in.md out.html   # 同上，用环境变量
#
# 默认引擎是 scripts/render_report.py —— 纯 Python 标准库，零第三方依赖，
# 只要有 python3 就能出 HTML。这个 skill 因此**不依赖** answer-me-with-html，
# 单独安装即可完整工作；am.mjs 只是可选的增强（需要 Node 20+）。
#
# am.mjs 定位顺序（仅 --am 时需要，不写死任何本机路径）:
#   1. AM_CLI —— 可以是 am.mjs 文件，也可以是它所在的技能目录
#   2. 与 skill 并排安装的 answer-me-with-html/scripts/am.mjs
#   3. ~/.workbuddy/skills/answer-me-with-html/scripts/am.mjs
#   4. PATH 里的 am 命令（全局安装时）
set -euo pipefail

ENGINE="${RENDER_ENGINE:-builtin}"
ARGS=()
for a in "$@"; do
  if [ "$a" = "--am" ]; then ENGINE="am"; else ARGS+=("$a"); fi
done

if [ "${#ARGS[@]}" -lt 2 ]; then
  echo "用法: render_html.sh [--am] <input.md> <output.html>" >&2
  exit 2
fi

MD="${ARGS[0]}"
OUT="${ARGS[1]}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$ENGINE" = "am" ]; then
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
    if command -v am >/dev/null 2>&1; then printf '%s\n' "$(command -v am)"; return 0; fi
    return 1
  }

  AM="$(resolve_am || true)"
  if [ -z "${AM}" ]; then
    cat >&2 <<'MSG'
--am 模式找不到 answer-me-with-html 的 am.mjs。
  - 不想装它：去掉 --am，用默认的内置渲染器（零依赖）
  - 装技能：把 answer-me-with-html 放到 ~/.workbuddy/skills/ 下
  - 或指定：export AM_CLI=/绝对路径/am.mjs（也可以给技能目录）
MSG
    exit 1
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "未找到 node：--am 渲染需要 Node.js 20+。去掉 --am 可用内置渲染器。" >&2
    exit 1
  fi
  if [ -n "${AM_VERBOSE:-}" ]; then
    echo "engine: am.mjs (${AM})" >&2
  fi
  mkdir -p "$(dirname "${OUT}")"
  node "${AM}" render - -o "${OUT}" < "${MD}"
  exit 0
fi

# ---- 内置引擎（默认）----
PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "${PY}" ]; then
  echo "未找到 python3/python：内置渲染器需要 Python 3.9+。抓取本身也依赖它。" >&2
  exit 1
fi
if [ -n "${AM_VERBOSE:-}" ]; then
  echo "engine: builtin (${PY} ${SCRIPT_DIR}/render_report.py)" >&2
fi
exec "${PY}" "${SCRIPT_DIR}/render_report.py" "${MD}" "${OUT}"
