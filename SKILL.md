---
name: ai-project-scout
description: 从 Product Hunt 和 Indie Hackers 抓取 AI/agent 创业项目，agent 翻译为中文日报。触发词：AI日报、AI创业项目、今天有什么新项目、抓取AI产品、AI工具推荐。
---

# AI 项目侦察员

从 Product Hunt、Indie Hackers 抓取 AI 创业项目，翻译为中文日报。

## 工作流

1. 运行 `python scripts/scout.py [参数]` 抓取数据（需先 `pip install -r requirements.txt`）。
   - **退出码 1 表示两个来源全部失败**：此时不要生成日报，把 `sources` 里的错误原样报给用户。
   - 退出码 0 但 `sources` 里某来源 `ok=false`：该来源本节用 `callout warn` 写明原因。
   - 抓到的条目此时只是**挂起**在 `raw_latest.json` 的 `new_ids` 里，**还没进去重库**。
2. 读取输出目录下的 `raw_latest.json`，先看 `sources` 判断每个来源是「0 条」还是「抓取失败」。
3. agent 逐条翻译 `description` 为流畅中文（**每一条都要翻译，不得跳过**）。
4. 按「HTML 日报模板」组装成 am 扩展 Markdown，写入 `$OUTPUT_DIR/YYYY-MM-DD.md`。
5. 渲染为可视化 HTML：
   `bash scripts/render_html.sh "$OUTPUT_DIR/YYYY-MM-DD.md" "$OUTPUT_DIR/YYYY-MM-DD.html"`
   - 默认用**内置渲染器**（`scripts/render_report.py`，纯标准库，只要有 Python 就能出 HTML）。
   - 用户明确要 answer-me-with-html 的版式时加 `--am`（需要 Node 20+ 与该技能）。
6. **校验条数（强制，不可跳过）**：
   `python scripts/check_report.py "$OUTPUT_DIR/YYYY-MM-DD.md"`
   - 退出码 1 说明有漏条或表格行数不符，**按输出的缺失清单补齐后重跑本步**，不许带着漏条交付。
   - 退出码 0 时本脚本会把命中条目**提交进 `seen_ids.json`**（去重的唯一提交口）。
7. 用 `present_files` 打开该 `.html`。

> **别在报告之前手工提交去重**。翻译 / 渲染 / 校验任一步失败时，条目还留在候选池里，
> 下次抓取会重新出现；一旦提前标成「已见」，它们会静默消失 30 天。

> 最终交付物是 HTML，`.md` 是可编辑的中间稿，两者都保留。
> 本技能**不依赖** answer-me-with-html：默认渲染走内置的 `render_report.py`（纯标准库）。
> 日报 Markdown 用的 `kv` / `callout` 语法两个引擎都认，同一份草稿两边渲染结果一致。

## 自然语言 → 参数

| 用户意图 | 命令 |
|----------|------|
| "AI 日报" / "今天有什么新项目" | `python scripts/scout.py` |
| "少一点" / "只看 10 条" | `python scripts/scout.py -p 10 -i 10` |
| "快速预览" / "不用太详细" | `python scripts/scout.py --no-detail` |
| "只要高关注度" | `python scripts/scout.py --ih-min-followers 50` |
| "只用近 30 天的新项目" | `python scripts/scout.py --ih-recency-days 30` |
| "PH 放宽到近 30 天" | `python scripts/scout.py --ph-max-age-days 30` |
| "去重别太久，7 天就重推" | `python scripts/scout.py --seen-ttl-days 7` |
| "只看 PH" | `python scripts/scout.py -i 0` |
| "只要数据、不写日报" | `python scripts/scout.py --commit-now`（抓完即提交去重） |
| "按北京时间出日报" | `AI_SCOUT_TZ=+08:00 python scripts/scout.py` |

## 输出路径

所有输出文件统一存放到输出目录下（默认 `~/.ai-project-scout/output`，可用环境变量 `AI_SCOUT_OUTPUT_DIR` 覆盖）：
- 原始 JSON：`$OUTPUT_DIR/raw_latest.json`（含 `sources` 状态块与 `new_ids` 待提交清单）
- 运行状态：`$OUTPUT_DIR/last_run.json`（每次运行都写，含退出码、各来源成败与 `seen_pending`）
- 可视化 HTML 日报：`$OUTPUT_DIR/YYYY-MM-DD.html`（最终交付物）
- Markdown 草稿：`$OUTPUT_DIR/YYYY-MM-DD.md`（可编辑源）
- 已见去重：`$OUTPUT_DIR/seen_ids.json`（由 `check_report.py` 校验通过后写入，`scout.py` 不写）

输出目录在 `scripts/scout.py` 中定义（读取 `AI_SCOUT_OUTPUT_DIR` 环境变量，缺省回退到用户主目录），不要放在 skill 目录内。

## 自检（改脚本时必跑）

`python scripts/selftest.py` —— 23 个离线用例，约 2 秒，覆盖时区解析、AI 关键词过滤、
网络重试、两阶段去重、报告校验与两个渲染引擎（内置渲染器 + `--am` 定位）。
加 `--network` 追加真实抓取冒烟；`-k 关键字` 只跑子集。

改完 `scout.py` / `check_report.py` / `seen_store.py` 各跑一次，退出码 1 就别交付。

## 时区、条数与重试

- 日期跟随**系统本地时区**（`AI_SCOUT_TZ` 可覆盖：`+08:00` / `UTC` / `Asia/Shanghai`）。不要写死时区：日报日期会与用户日历差一天。
- PH feed 单页约 50 条，AI 命中通常 25–35 条。脚本打印「feed 已到末尾」时，说明 `-p` 想要的数量超过候选池，要更多只能放宽 `--ph-max-age-days`。
- 网络重试默认 3 次（`AI_SCOUT_RETRIES` 可调），429 / 5xx / 连接类异常重试，4xx 不重试。两个来源都失败才退出码 1。

## 翻译要求

- 保留原意，一句话讲清「它做什么、给谁用」，不要压成一个名词短语
- 标签用中文映射：`commitment-full-time→全职`、`founders-solo→单人创业`、`funding-bootstrapped→自筹资金`、`revenue-model-subscription→订阅制` 等
- 映射表见 `scripts/scout.py` 内 `TAG_CN`（未知标签保留原文，agent 可再润色）

## AI 过滤规则（`scout.py::is_ai_related`）

两层判断，原则是召回优先：

- **强信号**：`ai`、`llm`、`gpt`、`claude`、`agentic`、`multimodal`、模型品牌名（Grok / Ideogram / Midjourney / Qwen / Ollama 等）等 40+ 条正则，出现即命中。
- **弱信号**：单复数 `agent`、大写 `RAG`。`agent` 先看有无「房产 / 旅行 / 保险 / 招聘 / 用户代理」等非 AI 语境，没有就按 AI 收。

用户反馈「某产品应该出现但没出现」时，先在 `AI_PATTERNS` 里补词再重跑；不要手工往 `raw_latest.json` 里塞条目（会绕过校验口径）。

## 日报硬性规则

这两节就是日报本体，不允许省略：

1. **必须列全**：`raw_latest.json` 里每个条目都要进表。禁止写「节选」「仅列部分」「等」，禁止只取前 N 条充数。
2. **交付前必须过 `check_report.py`**：退出码 1 时按缺失清单补齐再重跑，不允许带着漏条交付。
3. **两个来源各一节**：Product Hunt 与 Indie Hackers 分开列，标题写明本条数。
4. **每条都要有中文简介**：翻译原意，讲清产品做什么；不要只留英文原名或空着。
5. **不做收入对比图**：收入只作为 IH 表格里的一列，不要另开 `chart` 面板。用户明确要求图表时才加。
6. **某来源 0 条时保留该节**：用 `callout warn` 写明原因（如接口不可达），不要静默删掉整节。
7. **条目多时用 `doc` 模板**：超过 12 条按节线性排列；不要塞进 `sheet` 网格。
8. **只写真实抓到的日期**：PH 已按 `--ph-max-age-days`（默认 7 天）过滤，表里不要出现超出窗口的旧帖。

## 日报模板

把翻译后的内容组装成下面的 Markdown（`kv` / `callout` 是本技能约定的轻量扩展，
内置渲染器与 answer-me-with-html 都支持）。完整可跑样例见
`examples/report-template.md`。组件分工：概览用 `kv`，两个来源各用一张表，
亮点用 `callout`。

````markdown
---
template: doc
theme: auto
title: AI 创业项目日报 — YYYY-MM-DD
subtitle: Product Hunt M 个 · Indie Hackers K 个
source: ai-project-scout
---
Lead: 一句话核心结论（本期新增 N 个项目，最值得注意的 1-2 点）。

## A 今日速览

```kv cols=3
日期: YYYY-MM-DD
来源: Product Hunt + Indie Hackers
本期新增: N
累计追踪: T
PH 上榜区间: 最早 至 最新
IH 最高月收入: $N
```

## B Product Hunt 新 AI 产品（M 个）

按上架时间从新到旧排列，全部列出。

| 产品 | 中文简介 | 上架 | 链接 |
|---|---|---|---|
| 产品名 | 一句话中文简介 | YYYY-MM-DD | [打开](URL) |

## C Indie Hackers AI 项目（K 个）

有 Stripe 验证收入的按收入从高到低排在前面，未披露的排在后面，全部列出。

| 项目 | 中文简介 | 关注者与月收入 | 标签 |
|---|---|---|---|
| 项目名 | 一句话中文简介 | 65 人 · $65,000/月 | 全职、订阅制、B2B |

## D 观察

```callout info 今日看点
- 一句话观察（趋势、共性、值得跟的方向）。
```
````

> 关注者或标签缺失时写「—」，不要留空单元格；某来源 0 条时把该节的表换成
> `callout warn`，说明原因并保留标题。
