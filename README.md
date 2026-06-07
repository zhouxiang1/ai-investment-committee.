# AI投委会

基于 `AI 投委会开发文档.pdf` 实现的本地网页端 MVP。应用包含：

- 公司输入、市场识别与证券消歧
- 内置公司基础库，支持美股、港股、A 股、中概股映射
- A 股、港股、美股证券级实时行情刷新，支持多地上市公司分开报价
- 40 位真实人物专家库与后台管理
- 专家材料上传、联网公开材料补强、AI 蒸馏预览、手动画像修正
- 专家推荐算法、5 位委员选择、主席推荐/手动更换/随机选择
- 四个资料采集研究员的结构化资料包
- 多源真实数据采集：东方财富证券搜索/行情/港股 F10、腾讯实时行情、HKEXnews 港交所公告 PDF、Yahoo Finance 新闻/历史行情、SEC EDGAR、巨潮资讯、东方财富股吧/研报
- AICS 四分评分体系：数据可信度 DQS、公司质量 CQS、估值吸引力 VAS、投资行动 IAS
- 2.0 重点 300 公司评级：美股 111 家、A 股 120 家、港股 69 家，每日自动刷新行情并重建四象限图
- 评分项落库，可展开到模块、指标、扣分/加分理由和 evidence_ids
- 五轮递进投委会状态机，支持后端托管连续生成；页面关闭后仍会生成最终报告并进入历史记录
- 中文深度报告、历史报告归档、PDF 导出
- OpenAI-compatible LLM Provider 驱动五轮会议；默认不再使用预设专家话术

## 启动

```bash
npm install
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
```

分别启动后端和前端：

```bash
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
npm run dev
```

打开：

```text
http://127.0.0.1:5173/
```

## MiniMax M2.7

复制 `.env.example` 为 `.env`，设置：

```bash
MINIMAX_API_KEY=你的后端密钥
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_MODEL=MiniMax-M2.7
AI_COMMITTEE_USE_LLM=true
AI_COMMITTEE_LLM_AGENT_MAX_TOKENS=8000
AI_COMMITTEE_LLM_REPAIR_MAX_TOKENS=6000
AI_COMMITTEE_LLM_JSON_RETRY_MAX_TOKENS=9000
AI_COMMITTEE_LIVE_QUOTES=true
AI_COMMITTEE_V2_DAILY_REFRESH=true
```

密钥只由 FastAPI 后端读取，不会进入前端构建产物。五轮会议默认要求真实 LLM 调用；如果未配置 key，后端会拒绝运行轮次，避免生成预设假会议。
如果轮次状态出现 `JSON 不完整` 或 `max_tokens 截断`，优先调高上述三个 LLM token 参数，后端也会自动尝试 JSON 修复和精简重试。

只有离线开发调试时才建议启用兜底：

```bash
AI_COMMITTEE_ALLOW_FALLBACK=true
```

## 数据源策略

资料包生成会按市场选择适配器，并把每个来源落到 `data/raw_sources/`：

- 证券识别：本地公司库、东方财富搜索、Yahoo Finance Search。中文公司名优先用东方财富解析，避免把港股/A 股误判成美股。
- 行情/估值：腾讯财经、东方财富 Push2，按 A 股、港股、美股独立证券代码刷新；A 股历史估值分位优先用 AKShare `stock_a_lg_indicator`，未返回时该指标直接标记为未评分。
- 港股：HKEXnews 抓取公告/年报 PDF，东方财富港股 F10 抓取主要财务指标、利润表、资产负债表、现金流量表，东方财富股吧补充公开讨论。
- A 股：AKShare 抓结构化财务摘要和历史估值分位，巨潮资讯抓公告/财报 PDF，东方财富研报和股吧补充研报/讨论。
- 美股：SEC EDGAR Companyfacts/Submissions 抓 XBRL 财务和 10-K/10-Q/8-K，StockTwits/Reddit/Yahoo 补充舆情与新闻。

如果某类数据源未返回，系统会明确写入 `collection_gaps`，并降低数据可信度；评分引擎不会再用 45/55/58 这类占位分兜底。缺少可追溯证据的指标会标记为 `missing_evidence`，前端结果页可展开查看缺口、所需证据、公式和已使用的 evidence。

## 专家库联网补强

专家库后台提供“联网补强”与“批量联网补强”。系统会围绕每位专家的中英文姓名、身份标签生成搜索式，抓取传记、访谈、股东信、演讲、对话和投资框架文章，把原文保存到 `data/expert_research/` 与 `expert_materials`，再由 LLM 蒸馏成更完整的 `investment_philosophy`、`core_framework`、`decision_process`、`question_template`、能力圈、盲区、风险偏好和发言风格。

可调参数：

```bash
AI_COMMITTEE_EXPERT_WEB_MAX_SOURCES=4
AI_COMMITTEE_EXPERT_WEB_SOURCE_CHARS=12000
AI_COMMITTEE_EXPERT_DISTILL_MAX_TOKENS=7000
```

## 常用命令

```bash
npm run build
python3 -m py_compile backend/app/*.py backend/app/scoring/*.py
python3 -m unittest tests/test_v2_universe.py
python3 scripts/rebuild_v2_ratings.py
python3 scripts/refresh_v2_aics_scorecards.py --limit 5
npm audit --audit-level=moderate
```

PDF 输出在 `output/reports/`，SQLite 数据库在 `data/ai_committee.sqlite`。

## 2.0 重点300评级

2.0 清单位于 `backend/app/data/v2_companies.json`，作为美股 111 / A股 120 / 港股 69 的权威输入。后端会把清单补入 `companies` 表，并在 `v2_company_ratings` 表生成可复现评级快照。应用启动后会按 `AI_COMMITTEE_V2_REFRESH_INTERVAL_SECONDS` 的间隔自动刷新重点清单行情，默认 86400 秒一次；刷新后会重建评级快照，前端四象限图随 API 数据自动更新。

```bash
python3 scripts/rebuild_v2_ratings.py
python3 scripts/refresh_v2_aics_scorecards.py --force
python3 scripts/refresh_v2_aics_scorecards.py --source-mode snapshot
python3 -m unittest tests/test_v2_universe.py
```

`--source-mode snapshot` 会使用已有公司信息、实时行情快照和本地证据结构调用第一版 AICS 评分引擎生成 scorecard；未补齐的财报、公告、新闻和同业证据会进入 DQS 与 missing_metrics，不再回退到 baseline 评级。

API：

```text
GET  /api/v2/ratings
POST /api/v2/ratings/rebuild
```

首页直接展示完整 300 家、市场分布、动作分布、行动分 Top10 和质量/估值四象限图。评级模式为 AICS first：若已有第一版 AICS scorecard，会直接复用；若缺失，可用 `refresh_v2_aics_scorecards.py` 批量采集资料包并按第一版 AICS 评分引擎生成 scorecard；仍缺少实时证据时才保留可测试、可追踪的 baseline 评级。

## 线上部署

生产环境推荐使用 Docker 单服务部署：FastAPI 同时提供 API 和前端静态页面，线上只需要暴露一个 HTTP 服务。

```bash
cp .env.production.example .env
# 填入 MINIMAX_API_KEY；如需临时加 Basic Auth，再开启 APP_BASIC_AUTH_ENABLED
docker compose up -d --build
```

容器默认监听 `8000` 端口，数据库、PDF、上传文件和原始采集材料都会落在 `/data`，请在线上平台挂载持久化磁盘。

上线前建议设置：

```bash
AI_COMMITTEE_USE_LLM=true
AI_COMMITTEE_ALLOW_FALLBACK=false
APP_BASIC_AUTH_ENABLED=false
```

不要把 `.env`、`data/`、`output/` 提交到 Git。如果生产域名已经由外层网关、反代或平台账号保护，应用内 Basic Auth 可以保持关闭；如需应用内临时登录保护，设置 `APP_BASIC_AUTH_ENABLED=true` 并填写 `APP_BASIC_AUTH_USERNAME` / `APP_BASIC_AUTH_PASSWORD`。
