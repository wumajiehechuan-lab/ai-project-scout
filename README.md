# AI 项目侦察员 (ai-project-scout)

> 从 **Product Hunt** 和 **Indie Hackers** 自动抓取 AI / Agent 创业项目，翻译为**中文日报**的 WorkBuddy / Agent 技能。

每天（或任意时刻）一句话就能拿到一份干净的 AI 创业动态简报：哪些新产品在 Product Hunt 上榜、哪些 Indie Hackers 上的独立开发者在做有收入的 AI 产品、他们的商业模式和收入信号是什么——全部翻译成中文，保留原意，附带链接与关注者 / 收入等信号，最终输出一份可视化 HTML 日报（同时保留 Markdown 草稿）。

---

## 它解决什么问题

- 想追踪 AI 创业风向，但 Product Hunt / Indie Hackers 全是英文、信息散落、刷不过来。
- 想要「今天有什么新 AI 产品 / 有什么能赚钱的 AI 项目」这种**可消费的中文摘要**，而不是原始 RSS。
- 想建立一份**累计去重的项目库**（避免重复推送同一批项目）。

这个技能把「抓取 → 去重 → agent 翻译 → 生成可视化 HTML 日报」串成一条可复用的流水线。

---

## 特性

- **双源抓取**：Product Hunt（RSS feed + 详情页补全简介，默认只取近 7 天发布的帖子）、Indie Hackers（Algolia 搜索 API，按关注者过滤，并按创建时间取近 90 天新项目）。
- **时区跟随系统**：日报日期、`published_at`、去重有效期统一用**本地时区**（`AI_SCOUT_TZ` 可覆盖）。此前写死 UTC+8，本机在 PST 时日报日期会早一天。
- **瞬时故障重试**：SSL EOF / 429 / 5xx 自动指数退避重试（默认 3 次），4xx 直接失败、不做无用重试。
- **AI 关键词过滤（两层判断）**：强信号（`ai`/`llm`/`gpt`/`agentic`/模型品牌名等 40+ 条正则）出现即命中；弱信号 `agent(s)`、大写 `RAG` 先排除房产/旅行/保险/招聘等非 AI 语境，再按 AI 收。原则是**召回优先**——漏掉一个真产品比多收一个噪声更糟（实测误杀从 8/24 降到 0）。
- **累计去重（带有效期，两阶段提交）**：`seen_ids.json` 记住已推送过的项目，多跑不重复；默认 30 天后自动回池，避免「跑一次就永久空白」。抓取阶段只把新条目挂进 `raw_latest.json` 的 `new_ids`，**只有日报校验通过后**才由 `check_report.py` 提交去重——翻译或渲染失败时条目不会被提前吃掉。
- **信号化**：Indie Hackers 项目附带关注者数、收入、商业模式标签，一眼看清「谁在赚钱」。
- **可视化 HTML 日报（零硬依赖）**：agent 翻译后组装成日报 Markdown，默认由**内置渲染器**（纯 Python 标准库）输出单文件 HTML——速览卡 + Product Hunt 全量表 + Indie Hackers 全量表 + 今日看点；标签按 `TAG_CN` 自动汉化。同时保留 Markdown 草稿。装有 answer-me-with-html 技能时可加 `--am` 换用它的版式，同一份草稿两个引擎都认。
- **内容不省略**：两个来源的每个条目都进表，逐条给中文简介、上架时间与链接，不写「节选」。收入只作为表格字段，不额外画对比图。
- **漏条会被拦住**：`check_report.py` 拿报告与 `raw_latest.json` 逐条比对，少一条就退出码 1。
- **失败会显式失败**：两个来源全抓不到时退出码 1 且不覆盖上次快照；`raw_latest.json` 的 `sources` 块与 `last_run.json` 记录每个来源的成败与原因。
- **自带离线自检**：`python scripts/selftest.py` 用 21 个用例盯住时区、关键词过滤、重试、两阶段去重、报告校验与渲染脚本定位，不联网、约 2 秒。加 `--network` 再跑真实抓取冒烟。
- **零密钥即可跑**：Indie Hackers 的只读搜索 key 已内建默认值，新手开箱即用。

---

## 目录结构

```
ai-project-scout/
├── SKILL.md                      # 技能定义（触发词、工作流、模板）
├── README.md                     # 本文件
├── requirements.txt              # Python 依赖
├── .env.example                  # 可选的环境变量示例
├── references/
│   └── openai-agent.yaml         # 接口 / 调用策略定义
├── scripts/
│   ├── scout.py                  # 纯数据抓取脚本（输出 JSON）
│   ├── check_report.py           # 日报条数校验 + 去重提交（漏条即退出码 1）
│   ├── seen_store.py             # seen_ids.json 的唯一写入实现（两阶段提交）
│   ├── selftest.py               # 离线自检（23 个用例，约 2 秒）
│   ├── render_report.py          # 内置 HTML 渲染器（纯标准库，零依赖）
│   └── render_html.sh            # 渲染入口：默认内置引擎，--am 换 answer-me-with-html
└── examples/
    └── report-template.md        # 日报模板（可跑样例，两个渲染引擎通用）
```

> 运行时产生的原始数据、日报、去重记录默认存放在 `~/.ai-project-scout/output`（可用 `AI_SCOUT_OUTPUT_DIR` 环境变量覆盖），不会污染 skill 目录本身。

---

## 安装

需要 Python 3.10+。

```bash
pip install -r requirements.txt
```

依赖只有 [`httpx`](https://github.com/encode/httpx)（用于网络请求），无重型依赖。

> 出 HTML **不需要额外安装任何东西**：默认引擎 `render_report.py` 是纯标准库实现。
> 想用 answer-me-with-html 的版式时：`bash scripts/render_html.sh --am 报告.md 报告.html`
> （需 Node.js 20+ 与该技能）。`AM_CLI` 可指 `am.mjs` 或技能目录，定位顺序：
> `AM_CLI` → 并排安装的技能 → `~/.workbuddy/skills` → PATH 里的 `am`，不写死本机路径。

---

## 配置（可选）

复制 `.env.example` 为 `.env` 按需修改：

```ini
# Indie Hackers Algolia 只读搜索凭据（默认值已内建于 scout.py，一般无需改）
IH_ALGOLIA_APP_ID=N86T1R3OWZ
IH_ALGOLIA_API_KEY=5140dac5e87f47346abbda1a34ee70c3

# 输出目录（可选，默认 ~/.ai-project-scout/output）
# AI_SCOUT_OUTPUT_DIR=/path/to/your/output

# 时区（可选，默认跟随系统本地时区）
# AI_SCOUT_TZ=+08:00

# 网络重试次数（可选，默认 3）
# AI_SCOUT_RETRIES=3
```

> 说明：这两个 IH 凭据是 Indie Hackers 前端**公开的 search-only key**，仅用于只读搜索，硬编码默认值即可正常工作，配置它们只是为了在 key 失效时能快速替换。

---

## 使用

### 方式一：作为 Agent 技能（推荐）

把本目录放到 WorkBuddy 的技能目录后，直接用自然语言触发：

| 用户意图 | 实际执行 |
|----------|----------|
| “AI 日报” / “今天有什么新项目” | `python scripts/scout.py` |
| “少一点” / “只看 10 条” | `python scripts/scout.py -p 10 -i 10` |
| “快速预览” / “不用太详细” | `python scripts/scout.py --no-detail` |
| “只要高关注度” | `python scripts/scout.py --ih-min-followers 50` |
| “PH 放宽到近 30 天” | `python scripts/scout.py --ph-max-age-days 30` |
| “只看 PH” | `python scripts/scout.py -i 0` |

Agent 会自动：运行抓取 → 读取 `raw_latest.json` → 逐条翻译为中文 → 组装 am Markdown 并渲染成 `YYYY-MM-DD.html` → **跑 `check_report.py` 校验条数（通过即提交去重）** → 打开页面（同时保留 `YYYY-MM-DD.md` 草稿）。

### 方式二：命令行单独使用

```bash
# 默认：Product Hunt 30 条（仅近 7 天）、Indie Hackers 20 条
python scripts/scout.py

# 自定义数量与过滤
python scripts/scout.py -p 20 -i 30 --ih-min-followers 30

# 跳过 PH 详情页（更快，但简介更短）
python scripts/scout.py --no-detail

# 生成日报后校验条数（漏条会退出码 1）
python scripts/check_report.py ~/.ai-project-scout/output/2026-11-03.md

# 改完脚本先自检（离线，约 2 秒；加 --network 追加真实抓取）
python scripts/selftest.py
```

参数说明：

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `-p, --ph-count` | Product Hunt 抓取上限 | 30 |
| `-i, --ih-count` | Indie Hackers 抓取条数（填 0 可只看 PH） | 20 |
| `--ih-min-followers` | IH 最低关注者数（旧名 `--ph-min-followers` 仍可用） | 10 |
| `--ih-recency-days` | IH 只取近 N 天创建的项目 | 90 |
| `--ph-max-age-days` | PH 只取近 N 天发布的帖子，`0` 表示不过滤 | 7 |
| `--seen-ttl-days` | 去重记录有效期（天），过期重新允许出现 | 30 |
| `--no-detail` | 跳过 PH 详情页抓取 | 关闭 |
| `--commit-now` | 抓完立即提交去重（默认延后到日报校验通过） | 关闭 |

> `-p` 的上限受 PH 单页 feed 限制：单页约 50 条，AI 命中通常 25–35 条。要更多只能放宽
> `--ph-max-age-days`。feed 耗尽时脚本会打印「feed 已到末尾」，不会假装抓满。

运行后会在输出目录生成 `raw_latest.json`（原始数据 + `sources` 来源状态 + `new_ids` 待提交清单）、`last_run.json`（每次运行的状态与退出码）和 `seen_ids.json`（去重记录，由 `check_report.py` 校验通过后写入）。日报由 agent 或人工按模板翻译生成。

**退出码约定**：`0` = 至少一个来源成功；`1` = 两个来源全部失败（此时 `raw_latest.json` 保持上次快照，不会被空结果覆盖）。

---

## 输出示例

最终产物是一页 HTML（可跑样例见 [`examples/report-template.md`](examples/report-template.md)），固定四节：

| 节 | 内容 |
|---|---|
| 今日速览 | 日期、来源、新增数、累计追踪、PH 上榜区间、IH 最高月收入 |
| Product Hunt 新 AI 产品 | 全部条目：产品 / 中文简介 / 上架日期 / 链接 |
| Indie Hackers AI 项目 | 全部条目：项目 / 中文简介 / 关注者与月收入 / 标签（有验证收入的排前面，未披露写「未披露」） |
| 观察 | 1–3 条趋势判断 |

两节都列全，不写「节选」。Markdown 草稿与 HTML 同目录，改完草稿重跑
`render_html.sh` 即可原地覆盖页面。

---

## 质量闸门

日报的表格是 agent 手写的，写 6 行还是 26 行本来没有任何环节会发现。三道闸门兜住：

| 闸门 | 拦住什么 | 怎么拦 |
|---|---|---|
| `--ph-max-age-days`（默认 7） | PH feed 混入几个月前的旧帖，被当成「今日新增」 | 按 `published` 过滤，超窗口直接丢弃 |
| `raw_latest.json` 的 `sources` 块 | 分不清某来源是「0 条」还是「抓取失败」 | 记录 `ok`/`error`/`kept`/`new`；两源全失败则退出码 1 且不覆盖旧快照 |
| `check_report.py` | 报告漏条、只取前 N 条充数 | 与 raw 逐条比对 + 分节表格行数比对，不一致退出码 1 |
| 两阶段去重（`seen_store.py`） | 报告没写出来、条目却被标成「已见」，静默消失 30 天 | 抓取只写 `new_ids`；`check_report.py` 通过后才提交，失败则下次重推 |
| `selftest.py` | 改脚本时把上面的闸门改坏 | 21 个离线用例覆盖时区 / 过滤 / 重试 / 去重 / 校验 / 渲染定位，不联网 2 秒跑完 |

```bash
# 交付前必跑；退出码 0 才允许打开页面
python scripts/check_report.py ~/.ai-project-scout/output/2026-11-03.md
```

输出示例（漏条时）：

```
[1] 逐条命中: 10/17 ✗
    缺失: ernd
    缺失: AC Bridge
[2] 分节行数: Product Hunt 10/17 ✗
结果: 未通过
[3] 去重提交: 未提交（校验未通过，这批条目下次抓取还会出现，不会丢）
```

---

## 标签映射

Indie Hackers 的英文标签会映射为中文，例如：

| 原始标签 | 中文 |
|----------|------|
| `commitment-full-time` | 全职 |
| `founders-solo` | 单人创业 |
| `funding-bootstrapped` | 自筹资金 |
| `revenue-model-subscription` | 订阅制 |

完整映射表见 `scripts/scout.py` 内的 `TAG_CN`（未知标签保留原文）。

---

## 注意事项

- 抓取依赖 Product Hunt 公开 RSS 与 Indie Hackers 搜索 API，若对方改版可能需要调整 `scout.py` 中的解析逻辑。
- AI 过滤是启发式的：`agent` 按「非房产/旅行/保险/招聘语境」即收，属于召回优先的取舍。若发现真产品被漏，往 `AI_PATTERNS` 补词即可（`is_ai_related` 会立即生效）。
- 只取数据、不产日报时（例如拿 `raw_latest.json` 自己加工），用 `--commit-now` 才会推进去重；否则同一批条目每天都会以「新增」出现——这是刻意的取舍：宁肯重推，也不丢。
- 时区默认跟随系统。用 `AI_SCOUT_TZ` 指定 IANA 名（如 `Asia/Shanghai`）时，Windows 需要 `pip install tzdata`；不想装就直接写偏移量 `+08:00`。
- 首次运行会产生网络请求，请遵守目标站点的使用条款，勿高频刷取。
- 输出目录包含历史数据，已通过 `.gitignore` 排除，不会进入版本库。

