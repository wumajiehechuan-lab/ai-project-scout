# AI 项目侦察员 (ai-project-scout)

> 从 **Product Hunt** 和 **Indie Hackers** 自动抓取 AI / Agent 创业项目，翻译为**中文日报**的 WorkBuddy / Agent 技能。

每天（或任意时刻）一句话就能拿到一份干净的 AI 创业动态简报：哪些新产品在 Product Hunt 上榜、哪些 Indie Hackers 上的独立开发者在做有收入的 AI 产品、他们的商业模式和收入信号是什么——全部翻译成中文，保留原意，附带链接与关注者 / 收入等信号。

---

## 它解决什么问题

- 想追踪 AI 创业风向，但 Product Hunt / Indie Hackers 全是英文、信息散落、刷不过来。
- 想要「今天有什么新 AI 产品 / 有什么能赚钱的 AI 项目」这种**可消费的中文摘要**，而不是原始 RSS。
- 想建立一份**累计去重的项目库**（避免重复推送同一批项目）。

这个技能把「抓取 → 去重 → agent 翻译 → 按模板生成日报」串成一条可复用的流水线。

---

## 特性

- **双源抓取**：Product Hunt（RSS feed + 详情页补全简介）、Indie Hackers（Algolia 搜索 API，按关注者过滤）。
- **AI 关键词过滤**：内置 30+ 条正则，只保留真正和 AI / LLM / Agent / 自动化相关的项目，过滤噪声。
- **累计去重**：`seen_ids.json` 记住已推送过的项目，多跑不重复。
- **信号化**：Indie Hackers 项目附带关注者数、收入、商业模式标签，一眼看清「谁在赚钱」。
- **中文日报**：agent 按统一模板翻译与排版，标签自动映射为中文（如 `bootstrapped→自筹资金`）。
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
│   └── scout.py                  # 纯数据抓取脚本（输出 JSON）
└── examples/
    └── sample-report.md          # 一份示例日报，展示输出格式
```

> 运行时产生的原始数据、日报、去重记录默认存放在 `~/.ai-project-scout/output`（可用 `AI_SCOUT_OUTPUT_DIR` 环境变量覆盖），不会污染 skill 目录本身。

---

## 安装

需要 Python 3.10+。

```bash
pip install -r requirements.txt
```

依赖只有 [`httpx`](https://github.com/encode/httpx)（用于网络请求），无重型依赖。

---

## 配置（可选）

复制 `.env.example` 为 `.env` 按需修改：

```ini
# Indie Hackers Algolia 只读搜索凭据（默认值已内建于 scout.py，一般无需改）
IH_ALGOLIA_APP_ID=N86T1R3OWZ
IH_ALGOLIA_API_KEY=5140dac5e87f47346abbda1a34ee70c3

# 输出目录（可选，默认 ~/.ai-project-scout/output）
# AI_SCOUT_OUTPUT_DIR=/path/to/your/output
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
| “只要高关注度” | `python scripts/scout.py --ph-min-followers 50` |
| “只看 PH” | `python scripts/scout.py -i 0` |

Agent 会自动：运行抓取 → 读取 `raw_latest.json` → 逐条翻译为中文 → 按模板写入 `YYYY-MM-DD.md`。

### 方式二：命令行单独使用

```bash
# 默认：Product Hunt 30 条、Indie Hackers 20 条
python scripts/scout.py

# 自定义数量与过滤
python scripts/scout.py -p 20 -i 30 --ph-min-followers 30

# 跳过 PH 详情页（更快，但简介更短）
python scripts/scout.py --no-detail
```

参数说明：

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `-p, --ph-count` | Product Hunt 抓取上限 | 30 |
| `-i, --ih-count` | Indie Hackers 抓取条数（填 0 可只看 PH） | 20 |
| `--ph-min-followers` | IH 最低关注者数 | 10 |
| `--no-detail` | 跳过 PH 详情页抓取 | 关闭 |

运行后会在输出目录生成 `raw_latest.json`（原始数据）和 `seen_ids.json`（去重记录）。日报由 agent 或人工按模板翻译生成。

---

## 输出示例

抓取后的中文日报长这样（完整示例见 [`examples/sample-report.md`](examples/sample-report.md)）：

```markdown
# AI 创业项目日报 — 2026-06-15

> 自动抓取自 Product Hunt、Indie Hackers，agent 翻译为中文
> 本次新增 42 个项目

## 一、Product Hunt 新 AI 产品

### 1. Slashy
- 链接：https://www.producthunt.com/products/slashy-3
- 上架日期：2026-06-13
- 简介：AI 原生的邮件客户端和助手 —— 用你的语气起草回复、筛选重要邮件……

## 二、Indie Hackers 有收入的 AI 项目

### 1. 产品名
- 链接：URL
- 标语：英文 tagline
- 关注者：N 人 | 收入：$XXX
- 简介：中文翻译
- 标签：全职、单人创业、自筹资金、订阅制...
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

完整映射表见 `scripts/scout.py` 内的 `TAG_CN`。

---

## 注意事项

- 抓取依赖 Product Hunt 公开 RSS 与 Indie Hackers 搜索 API，若对方改版可能需要调整 `scout.py` 中的解析逻辑。
- 首次运行会产生网络请求，请遵守目标站点的使用条款，勿高频刷取。
- 输出目录包含历史数据，已通过 `.gitignore` 排除，不会进入版本库。

---

## 许可证

MIT —— 自由使用、修改与分发。
