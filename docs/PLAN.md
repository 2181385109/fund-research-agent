# fund-research-agent（基金投研助手）— 开发计划（PLAN）

> 版本 v2（2026-09-28），由统筹会话维护。v1 是通用企业知识库版本，本版把业务场景确定为公募基金投研，技术架构和两期计划不变。
> **执行者不得修改本文件的验收标准。** 认为某条不合理或做不到时，写进 `docs/PROGRESS.md` 的「给统筹的问题」，然后停下来。

---

## 0. 目标与边界

**一句话**：平台收录约 20 只医药医疗和科技主题公募基金的披露文件（招募说明书、基金合同、年报、季报）以及结构化数据（基本信息、净值、收益、费率、前十大持仓、基金经理）。用户提问后，由 Agent 决定是查文档、查基金数据库、做收益计算，还是查询最新净值，然后流式给出回答。回答里标注每个数据的出处，并固定附上「不构成投资建议」的提示。第二期让后端能承受并发。

**要做的**
- 第一期（RAG + Agent + 评测）：数据采集、文档入库、混合检索与重排、LangGraph Agent + MCP 工具、SSE 流式输出带出处、评测体系
- 第二期（后端高并发）：gRPC、Redis 限流/配额/语义缓存、Kafka 季报批量入库与分布式锁、压测与优化

**不做**：多租户与复杂权限、云部署、模型微调（第三期）、本地大模型、任何形式的投资推荐（荐基、择时、买卖建议）。

**合规要求（贯穿全程）**
1. 每条回答末尾的风险提示由**服务端确定性追加**，不依赖 LLM 自觉生成。文案固定为：「以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。」
2. 用户请求荐基、择时或买卖建议时，Agent 拒绝给出建议，但可以陈述客观事实。评测集中专门有一类题检验这一点。
3. 回答中的每个数据都要有出处：文档（基金、文件名、页码）、数据库（表名、数据源、快照日期）或接口（数据源、净值日期、抓取时间）。

**为第三期预留**：评测集用 JSONL，证据以原文逐字引文标注、不绑定 chunk_id；Embedder 和 Reranker 通过 env 替换；保留逐题排名列表以便挖难负例（详见 §4）。

---

## 1. 总体架构

```
                 ┌──────────── 浏览器 (Vue3) ────────────┐
                 │ 提问 / 流式回答 / 出处角标 / 风险提示   │
                 └──────────────┬────────────────────────┘
                                │ REST + SSE (fetch 流)
                 ┌──────────────▼──────────────┐
                 │  backend (Java/Spring Boot)  │ 用户·知识库·文档·会话·批次
                 │  限流/配额(S10) · outbox(S11) │ MySQL(fra_app) / Redis
                 └───┬──────────────────┬──────┘
   第一期 HTTP+SSE   │                  │ 第二期 Kafka：季报批量入库
   第二期 gRPC 流    │                  │
                 ┌───▼──────────────────▼─────────────────────┐
                 │ ai-service (Python/FastAPI + gRPC 同进程)    │
                 │ 入库: PDF解析(含表格)→切块(带基金元数据)→    │
                 │       embedding→Milvus + ES                 │
                 │ 检索: 基金实体识别过滤 + 向量/BM25 → RRF → 重排│
                 │ Agent: LangGraph ⇄ MCP Client               │
                 │ /mcp: 文档 MCP Server (search_fund_documents) │
                 │ 语义缓存(S10) · 风险提示追加 · 出处组装        │
                 └───┬───────────────────────────┬────────────┘
                     │ MCP (streamable HTTP)      │ OpenAI 兼容 API
          ┌──────────▼───────────────┐          ▼
          │ mcp-tools (Python)        │     DeepSeek（压测时换 mock LLM）
          │ get_fund_db_schema        │
          │ run_fund_sql (只读) ──────┼──► MySQL fund_data 库（快照）
          │ calc_fund_return ─────────┤
          │ get_latest_nav ───────────┼──► 外部 HTTP：东方财富净值接口
          └──────────────────────────┘
 data-pipeline (离线)：基金池 → 下载披露 PDF → 抓取结构化数据 → 质量校验 → 写入 fund_data
 存储：Milvus standalone · Elasticsearch(IK) · MySQL · Redis 8 · Kafka(KRaft，S11 起)
```

MCP 服务拆成两个的理由：文档检索依赖已加载的模型，所以挂在 ai-service 进程内；数据库、计算和净值查询都很轻，独立成 mcp-tools 服务。任何 MCP 客户端（MCP Inspector、Claude Desktop）都能直接调用这些工具。

---

## 2. 已定技术选型

| 项 | 选择 | 备注 |
|---|---|---|
| LLM | DeepSeek，OpenAI 兼容 | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 放 env；**请求模型名和响应里的 model 字段都要记录**；当前可用模型名在 S0 实测确认 |
| Embedding | 本地 `BAAI/bge-small-zh-v1.5`（512 维） | 查询指令前缀做成开关，在 dev 集上裁决 |
| Reranker | 本地 `BAAI/bge-reranker-base` | 通过 `RERANKER_PROVIDER` / `RERANKER_MODEL` 替换 |
| 模型下载 | `HF_ENDPOINT` 可配（默认 hf-mirror），缓存目录挂卷 | |
| Java | JDK 17（`D:\tools\jdk-17`）、Spring Boot 3.5.x、MyBatis-Plus、Flyway、springdoc | |
| Python | 3.12、FastAPI、LangGraph 1.x、langchain-mcp-adapters、官方 `mcp` SDK、pydantic-settings | |
| PDF 解析 | **pdfplumber**（MIT，表格提取能力强），正文可用 pypdf 辅助 | 不用 PyMuPDF（AGPL） |
| 结构化数据 | **AkShare**（MIT，封装东方财富、雪球、巨潮） | 实测情况见 §2.1 |
| 向量库 | Milvus 2.5.x standalone，内嵌 etcd + 本地存储，单容器 | |
| 关键词检索 | Elasticsearch 8.x + IK，堆内存 1g | IK 不可用时退回 smartcn 并登记 ADR |
| Redis | `redis:8.x`（社区版自带向量索引） | 不可用时用 redis-stack-server |
| Kafka | `apache/kafka`，KRaft combined 单节点，堆 512m | S11 才加入 |
| 服务间通信 | 第一期 HTTP + SSE；第二期 gRPC（server streaming） | Java 侧用 `AiServiceClient` 接口，两种实现并存 |
| 前端 | Vue3 + Vite + TS | SSE 用 fetch + ReadableStream |
| 压测 | Locust + mock LLM | |
| CI | GitHub Actions | 不下载模型、不连真实 LLM、不访问外网 |
| 仓库 | `D:\xiangmu\fund-research-agent`，公开 `github.com/2181385109/fund-research-agent`，MIT | |

### 2.1 数据源（统筹于 2026-09-28 实测）

| 数据 | 首选来源 | 实测结果 | 使用方式 |
|---|---|---|---|
| 披露 PDF（招募说明书、合同、年报、季报） | 证监会基金电子披露网站 eid.csrc.gov.cn | ❌ 本机命令行经代理访问返回 502，HTTPS 握手失败；内置浏览器打开是空白页。**需要用户用自己的浏览器确认能否访问** | 可访问时，用来人工抽查 3 份文件与下载件是否一致 |
| 同上（兜底，实际使用） | 东方财富公告 PDF：`https://pdf.dfcfw.com/pdf/H2_{报告ID}_1.pdf`；报告 ID 来自 AkShare `fund_announcement_report_em` | ✅ 110022 的 2026Q2 季报：HTTP 200，12 页，pypdf 能提取文字 | 这些文件由基金管理人发布，是法定披露文件的转载件。`SOURCES` 同时记录公告标题、发布日期和下载 URL |
| 基金列表 | AkShare `fund_name_em` | ✅ 27954 行 | 筛选基金池 |
| 基本信息（成立日、基准、投资目标…） | `fund_individual_basic_info_xq` | ✅ | |
| 净值历史 | `fund_open_fund_info_em(indicator="单位净值走势"/"累计净值走势")` | ✅ 110022 共 3895 行，最新到 2026-09-28 | 分红相关数据用 `indicator="分红送配详情"`（待执行者实测） |
| 前十大持仓 | `fund_portfolio_hold_em(date="2026")` | ✅ 返回 2026Q2 明细 | |
| 基金经理 | `fund_manager_em` | ✅（仅现任） | 任职历史从招募说明书、季报或 `fund_announcement_personnel_em` 补充（待实测） |
| 费率 | `fund_fee_em` | ⚠️ `indicator="运作费用"` 正常；`"申购费率"` 返回空表，参数名需执行者核实 | 申购/赎回费率拿不到时，从招募说明书的费率表中解析 |
| 最新净值（Agent 外部 HTTP 工具） | 东方财富 `https://api.fund.eastmoney.com/f10/lsjz?fundCode=...`（需带 Referer 头） | ✅ 返回 2026-09-28 的净值 | 这是非官方接口：必须设超时、重试和短缓存；失败时回退到快照，并明确标注「非最新」 |

**版权与合规**：披露 PDF 的版权归基金管理人，抓取的数据受来源网站条款约束。所以**原始 PDF 和数据快照都不提交到公开仓库**。仓库只提交三样东西：下载/抓取脚本、`MANIFEST`（URL、sha256、快照日期）、用于 CI 的**自造**小型 fixture。README 注明「数据仅用于学习研究」。抓取要节制：每秒不超过 1 个请求，并做本地缓存。

### 2.2 端口（避开本机 ticket-qa 已占用的 3306/6379/8080/8089/9090/5672）
MySQL 3307 · Redis 6380 · ES 9201 · Milvus 19530（管理端口 9091）· Kafka 9094 · backend 8081 · ai-service 8001/50051 · mcp-tools 8101 · frontend 8088。全部可以通过 `.env` 修改。

---

## 3. 内存预算（16GB 笔记本）

`%USERPROFILE%\.wslconfig` 中必须设置 `memory=10GB`、`swap=4GB`（写进 SETUP.md；修改后要执行 `wsl --shutdown`，再运行 `wscript.exe D:\tools\wsl-keepalive.vbs`）。跑本项目前先停掉 ticket-qa 的容器。

| 服务 | mem_limit |
|---|---|
| mysql 512m · redis 256m · elasticsearch 1792m（堆 1g，`-XX:MaxDirectMemorySize=256m`；S0 实测空载即占 1536m 上限的 95%，已调整）· milvus 2g · kafka 1g（堆 512m，S11 起） | infra ≈ 5.5g |
| ai-service 2560m · mcp-tools 256m · backend 768m（-Xmx512m）· frontend 64m | app ≈ 3.6g |
| **合计上限 ≈ 8.9g** | 实际占用在 S0 和 S7 用 `docker stats` 实测 |

---

## 4. 数据契约（跨阶段，第三期依赖）

### 4.1 基金池与快照
- `data/universe.yaml`：约 20 只基金，其中医药医疗约 10 只、科技（半导体、电子、计算机、通信等）约 10 只。每只记录：主代码、各份额代码（A/C）、简称、主题、主动或指数、入选理由。**由用户确认后冻结。**
- 入选条件：场外普通开放式基金（不收 ETF 和联接基金）；成立不少于 2 年（要有 2025 年报和足够的净值历史）；最新规模不少于 2 亿；主动与指数大约 2:1；有 A/C 份额的不少于 6 只；近两年换过基金经理的不少于 3 只；至少 10 家不同的基金公司。
  - 备注（用户 2026-09-29 决定，ADR-030）：LOF 按场外份额收录——可场外申购赎回、净值口径与普通开放式基金一致，数据一律用场外 A/C 份额；「最新规模」口径为来源方披露的截至 2026-06-30 的基金资产净值。
- `DATA_AS_OF`：结构化数据的截止日。S1 执行时确定（建议取 2026-09-30），写进 manifest 后冻结。所有评测题都以这个日期为准，「最新净值」类题除外。
- 每只基金的文档：最新一次更新的招募说明书、基金合同、2025 年年报、2026Q1 季报、2026Q2 季报，一共约 100 份 PDF。**只收能直接提取文字的 PDF**：用平均每页提取字符数和乱码率判定，不合格的剔除并登记。
- `data/MANIFEST.json`（入库）：每份 PDF 的 doc_id（由基金代码 + 文档类型 + 报告期确定性生成）、标题、发布日期、URL、sha256、页数、可提取性检查结果；结构化快照每个表的行数和 sha256。原始文件放在 `data/raw/`，快照放在 `data/snapshots/<DATA_AS_OF>/`，这两个目录都 gitignore。

### 4.2 fund_data 库（Text2SQL 对象，只读账号 `fund_reader`）
最少包含以下表（执行者可以细化，但须登记 ADR）：
`funds`（主代码、名称、类型、主题、管理人、托管人、成立日、业绩基准、跟踪指数）、`share_classes`（份额代码、主代码、A/C）、`fees`（份额代码、管理费、托管费、销售服务费）、`purchase_fee_tiers` / `redemption_fee_tiers`（按金额或持有天数分档）、`nav_daily`（份额代码、日期、单位净值、累计净值、日增长率）、`dividends`、`managers`、`fund_manager_tenures`（任职起止日期）、`holdings_top10`（报告期、排名、股票代码、名称、占净值比、持股数、市值）、`fund_scale`（报告期、净资产）、`period_returns`（来源方给出的区间收益，带截至日）。每张表带 `source`、`as_of` 字段。

### 4.3 评测集
- `eval/datasets/fund_qa_v1.jsonl`：以文档检索为主，用于检索评测和回答评测
- `eval/datasets/agent_tasks_v1.jsonl`：工具路由、Text2SQL、收益计算、最新净值、文档 + 数据库综合题、荐基请求
- 字段（至少）：
```json
{
  "id": "qa-0001", "version": "v1", "split": "dev | test",
  "question": "…（带时间锚点，如“2026年二季度末”“2025年年报中”）",
  "fund_codes": ["110022"],
  "topic": "fee | manager | holdings | performance | contract_clause | commentary | cross_doc | unanswerable | advice_request | …",
  "style": "keyword | paraphrase",
  "answerable": true,
  "answer_type": "numeric | entity | list | text | refusal",
  "gold_value": "1.20%",
  "tolerance": null,
  "reference_answer": "…",
  "answer_points": ["…"],
  "evidence": [{"doc_id": "…", "quote": "原文逐字片段", "page": 5}],
  "expected_tools": ["search_fund_documents", "run_fund_sql"],
  "gold_sql": "SELECT …",
  "volatile": false,
  "provenance": "llm_draft_human_verified", "verified_by": "…", "notes": ""
}
```
- 规则：
  - 证据命中的判定：chunk 包含 quote，或包含 quote 中不少于 50% 的连续片段（这条规则要有单测）。
  - `gold_value` / `gold_sql` 的标准答案由**独立于被测工具的参考脚本**（pandas 或直接 SQL）根据快照计算，不能用被测工具的输出当标准答案。
  - `volatile=true`（最新净值类题）的判分方式：在评测时实时抓取数值，与回答比对，并同时检查回答里是否带了净值日期。
  - dev 用于调参，test 只做最终报告；**test 永远不进第三期训练数据**。
  - 冻结后要修改，必须升版本号、写 CHANGELOG，并得到用户批准。**禁止为分数改题、删题。**
- 所有检索 run 都保存 `per_query.jsonl`（有序的 chunk_id 列表 + 各阶段分数）。

### 4.4 结果文件规范
每次评测或压测写入 `reports/<类别>/<UTC时间戳>/summary.json`，必须包含：git commit、工作区是否 dirty、数据集 sha256、`data/MANIFEST.json` 的 sha256、`DATA_AS_OF`、模型 id（LLM 的请求名和响应名都要）、全部参数、机器信息、命令行原文。文档中的每个数字都要能追溯到这样一个文件。

---

## 5. 阶段拆分

> 每个阶段结束时：CI 全绿、PROGRESS.md 已更新、代码已 push，然后执行者停下，等统筹审查。

### 第一期：RAG + Agent + 评测

#### S0 脚手架与基础设施
**交付**
- git 仓库，`.gitignore`（包含 `data/raw`、`data/snapshots`、`.env`）、`.gitattributes`（LF）、MIT LICENSE、README 骨架；创建公开 GitHub 仓库
- 目录结构与 CLAUDE.md §3 一致
- `docker-compose.yml`（默认只起 infra）：mysql 8.4、redis 8、elasticsearch 8.x + IK（`deploy/elasticsearch/Dockerfile`）、milvus standalone（内嵌 etcd）。每个服务都有 mem_limit、healthcheck、具名卷和 `restart: unless-stopped`
- MySQL 初始化：`fra_app` 和 `fund_data` 两个库，外加只读账号 `fund_reader`（在 `fund_data` 上只有 SELECT 权限）
- `.env.example`（列出全部已知变量，带注释）
- `backend/`：Spring Boot 骨架，`GET /api/health` 检查 MySQL 和 Redis；Flyway 基线；至少 1 个单测
- `ai-service/`：FastAPI 骨架，`GET /health` 检查 Milvus、ES 和 Redis；至少 1 个单测
- `data-pipeline/`、`mcp-tools/`：Python 包骨架，各有 1 个单测
- `scripts/llm_smoke.py`（一次流式请求 + 一次 tool call，打印请求模型名和响应模型名）、`scripts/security_scan.py`
- CI：backend 用 `mvn -B verify`；三个 Python 包各跑 `ruff check` + `pytest`
- `docs/SETUP.md`（前置条件、.wslconfig、经 wsl 启动的方式、端口、排障）、`docs/DECISIONS.md`（把 §2 的选型登记为 ADR）

**验收**
1. 4 个 infra 服务 healthy（贴 `docker compose ps` 原文）
2. 贴 `docker stats --no-stream` 原文，并与 §3 预算对照
3. 对「基金管理人的管理费率」调用 IK `_analyze`（ik_max_word 与 ik_smart 各一次），贴原文
4. `fund_reader` 执行 INSERT 被拒（贴原文）
5. 两个 health 接口显示依赖全部 UP
6. llm_smoke 的流式和 tool call 都成功，记录请求/响应模型名
7. 首次 CI 全绿（贴链接）；安全扫描通过

#### S1 基金池与数据采集（data-pipeline）
**交付**
- 基金池草案 `data/universe.yaml`：用脚本按 §4.1 条件从 `fund_name_em` 等接口筛出候选，再人工取舍，每只写明入选理由。**提交给用户确认后冻结**（用户操作）
- PDF 采集：`python -m fund_pipeline.docs fetch`。流程：查公告列表 → 按「文档类型 + 报告期」精确匹配 → 下载到 `data/raw/`（节流、缓存、支持断点续传）→ 可提取性检查 → 写入 MANIFEST。匹配不到或不合格的文件列成清单，不许静默跳过
- 结构化采集：`python -m fund_pipeline.structured fetch --as-of <DATA_AS_OF>`。把 AkShare 各接口的数据截断到 as_of，保存到 `data/snapshots/`；再用 `python -m fund_pipeline.load` 建 `fund_data` 的表（Flyway 或 SQL 脚本）并导入
- 费率的补全策略：接口拿不到申购/赎回分档时，从招募说明书的费率表中解析（pdfplumber 提取表格），并标注 source
- 数据质量校验 `python -m fund_pipeline.quality`，输出报告到 `reports/data_quality/<ts>/`：
  - 每只基金、每张表的行数；净值连续性（缺口按交易日计算）
  - 前十大持仓：库中数据与 2026Q2 季报 PDF 中表格逐条比对（至少 5 只基金）
  - 现任基金经理：库中数据与最新季报比对
  - `period_returns`（来源方给出）与用净值自算的结果做对比，列出偏差和可能原因（分红处理等）
- CI 用自造的 fixture：几只假基金 + 用 reportlab 生成的小 PDF，不提交真实数据

**验收**
1. 用户确认基金池（记录日期）；MANIFEST 中 PDF 数量 ≈ 基金数 × 5，每个缺口都有原因
2. `fund_reader` 能查到所有表；贴各表行数原文
3. 质量报告原文：持仓比对一致率（写明分母）；每一处不一致都给出解释，不许忽略
4. 爬取节流和缓存有测试或证据；重复执行不会重复下载
5. 单测：文档类型和报告期的匹配规则、截断到 as_of、费率表解析（用 fixture）
6. CI 全绿；安全扫描确认仓库内没有真实 PDF 或快照

#### S2 文档入库流水线（ai-service）
**交付**
- 解析：基于 pdfplumber，保留页码；**表格转成 markdown 表格作为独立块，不和正文混切**；识别页眉页脚并去除（年报每页都有重复页眉）
- 切块：结构感知（按章节标题）+ 递归切分，参数可配。每块字段：`chunk_id={doc_id}#{index:04d}`、doc_id、fund_code、fund_name、doc_type、report_period、page_start/page_end、section_path、is_table、char_start/char_end、text
- **上下文头**：embedding 和 BM25 使用 `【{基金简称}｜{文档名}｜{章节}】+正文`，对外展示时用原文。是否启用由开关控制，在 S4 的 dev 集上裁决
- `Embedder` 接口 + `BgeEmbedder` + `FakeEmbedder`；Milvus 集合 `fund_chunks`（fund_code、doc_type 可以做过滤）；ES 索引 `fund_chunks`（text 字段写入用 ik_max_word、检索用 ik_smart，元数据用 keyword）
- 流水线：parse → chunk → 批量 embed → 按 doc_id 删除两边旧数据 → 写入两边，保证两边一致；HTTP 接口 `POST /v1/documents/ingest`、`DELETE /v1/documents/{doc_id}`、`GET /v1/stats`；CLI 按 MANIFEST 批量入库

**验收**
1. 单测：表格块、页眉页脚去除、章节路径、char offset 能从原文切回 chunk 文本、空文档和超长段落（用 fixture PDF）
2. 全量入库后 Milvus 与 ES 的 chunk 数一致，并等于流水线统计；贴按 doc_type 分组的统计和实测耗时
3. 幂等：同一文档重复入库 chunk 数不变；删除后两边都为 0
4. 展示 3 个 chunk 的完整元数据：一个费率表格块、一个持仓表格块、一个季报「投资策略和运作分析」正文块
5. CI 全绿（用 FakeEmbedder）

#### S3 评测集 v1（先冻结，之后才允许做检索和 Agent 调参）
**交付**
- `fund_qa_v1.jsonl`：不少于 100 题，dev 约 30%、test 约 70%，按 topic 分层。各类最少题数：fee 15、manager 10、holdings 15、performance 10、contract_clause（投资范围、赎回规则、估值等）15、commentary（季报里的观点，全部用改写问法）15、cross_doc（跨文档或跨报告期对比）10、unanswerable 10。另外，`style=paraphrase` 的题不少于 30
- `agent_tasks_v1.jsonl`：不少于 60 题。各类最少题数：tool_sql 20（含跨基金聚合和多表 join，例如「哪些基金二季度末同时重仓 X」）、calc_return 8、latest_nav 5、**文档 + 数据库综合题 12**、advice_request 5（期望拒绝推荐，只陈述事实）、doc_only 7、无需工具 3
- 标准答案由独立参考脚本计算（`eval/reference/`），数据库类题带 `gold_sql`
- `validate_dataset.py`：检查 schema、quote 是否为逐字子串、doc_id 与基金代码是否存在，并确认 gold_sql 在快照上能执行且结果等于 gold_value
- 词面重叠报告：按 style 统计问题与 quote 的字符重叠分布
- `SCHEMA.md`（写明第三期怎么转训练对，以及 test 不进训练这条规则）、`MANIFEST.json`、`CHANGELOG.md`

**验收**
1. 校验通过（贴原文），附分布表和词面重叠报告
2. **用户抽检不少于 25 条**（两个文件都要抽到；执行者准备抽检表），结果写进 PROGRESS
3. 抽检通过后冻结，sha256 写入 MANIFEST；冻结前不许用它跑任何检索或 Agent

#### S4 混合检索 + 重排 + 检索评测
**交付**
- 基金实体识别：用 fund_data 中的简称、全称、各份额代码和常见别名建立词典，从问题中识别出 fund_code，作为检索过滤条件。是否启用由开关控制；识别不到时不过滤
- 5 种模式：`vector`、`bm25`、`hybrid`（RRF，k=60）、`vector_rerank`、`hybrid_rerank`；每路召回数、top_n 和重排候选数都可配
- `Reranker` 接口 + `CrossEncoderReranker` + `NoopReranker`
- `POST /v1/retrieve`：返回各阶段分数
- 评测 runner：输出 summary.json、per_query.jsonl、report.md。指标为 Hit@1/3/5/10、Recall@5/10、MRR@10、nDCG@10，按 topic 和 style 拆分，另报 p50/p95 延迟
- dev 上的调参项（每次尝试都记进 `docs/tuning_log.md`）：实体过滤开关、上下文头开关、查询指令前缀开关、每路召回数。这四项确定后，在 test 上跑一次最终配置
- 主对比：同一组开关下的 `vector` 与 `hybrid_rerank`，用配对 bootstrap 给出 95% CI。次要消融：实体过滤开 vs 关

**验收**
1. 单测：RRF 手算用例、实体识别（含 A/C 份额代码和别名）、reranker 可替换、证据匹配规则
2. test 上 5 种模式的结果表，每个数字都可追溯
3. 主对比结论带 CI，并按 topic 拆分；**结果不理想也如实写，并分析原因**
4. test 的每次运行都已登记；per_query.jsonl 已保留
5. CI 全绿

#### S5 MCP 工具 + LangGraph Agent + SSE（Python 端）
**交付**
- mcp-tools（FastMCP，streamable HTTP）：
  - `get_fund_db_schema`：返回表结构、字段中文含义和示例值
  - `run_fund_sql`：sqlglot 解析后只允许单条 SELECT；禁止 DML/DDL、多语句、访问系统库、INTO OUTFILE；自动加 LIMIT 200；超时 5s；配合只读账号形成双保险；返回结果时附带 `source` 和 `as_of`
  - `calc_fund_return(share_code, start, end, include_fees=false, amount=None)`：确定性计算。**正确处理分红（复权）**；遇到非交易日就近取前一个净值日，并返回实际使用的起止日期；输出区间收益、年化收益、最大回撤；`include_fees=true` 时按费率分档扣除申购费和赎回费。数字计算一律由工具完成，不让 LLM 算
  - `get_latest_nav(share_code)`：调用东方财富接口，超时 3s、重试 2 次、缓存 10 分钟；失败时回退到快照，并返回 `stale=true` 和快照日期
- ai-service `/mcp`：`search_fund_documents(query, fund_codes?, doc_types?, top_n)`，返回编号后的片段
- LangGraph：多服务器 MCP 客户端 + StateGraph（agent ⇄ tools，`max_steps` 默认 6）；SQL 报错时把错误回传，最多重试 2 次；system prompt 要求用 [n] 标出处，没有依据时拒答，遇到荐基请求时拒绝推荐
- **出处**：`citations` 事件中每项带 `kind: document | database | computation | api`。document 类带基金、文档名、页码、片段；database 类带表名、source 和 as_of；computation 类带工具入参和实际日期；api 类带数据源、净值日期和 fetched_at。回答中不存在的 [n] 编号直接丢弃并记日志
- **风险提示**：由服务端在 `done` 之前发出一个 `disclaimer` 事件（固定文案），并与答案一起落库；另加一个输出守卫，检测「建议买入」「推荐购买」「稳赚」等违规表述：检测到就打标记并记日志（第一期不改写回答）
- `POST /v1/chat/stream`，事件协议写入 `docs/API.md`：`meta`、`tool_start`、`tool_end`、`token`、`citations`、`disclaimer`、`done`（含 usage、各段耗时、请求/响应模型名）、`error`

**验收**
1. SQL 守卫单测覆盖：DROP、DELETE、UPDATE、INSERT、多语句、注释绕过、系统库、INTO OUTFILE、结果截断
2. 收益计算单测：手算用例（包括一次分红、起止日为非交易日、含费/不含费），与参考脚本结果逐位一致
3. Agent 单测用 Fake LLM 覆盖：循环上限、工具报错后恢复、引用映射、非法编号丢弃、风险提示必定出现（包括报错路径）
4. 用独立 MCP 客户端列出并调用两个服务的全部工具，贴原文
5. live 冒烟：每种工具至少 2 题、综合题 3 题、荐基请求 2 题，贴 SSE 原文（可截断）
6. `get_latest_nav` 的故障回退有测试（mock 超时）
7. CI 全绿

#### S6 Java 主后端（经 HTTP 调用 AI 服务）
**交付**
- auth：注册/登录，BCrypt + JWT，无角色
- kb：系统预置公共知识库「基金披露文件」（所有用户只读）；用户也可以建私有知识库并上传自己的 PDF/MD/TXT
- document：上传（校验魔数、上限 20MB、按 sha256 去重）、状态机 PENDING→PROCESSING→READY/FAILED、调用 Python ingest、Python 带共享密钥回调更新状态
- conversation：会话和消息持久化（答案、出处、风险提示都落库），并传最近 N 轮上下文
- chat：用 SseEmitter 代理 Python 的流；**客户端断开后取消上游请求**
- `AiServiceClient` 接口 + `HttpAiServiceClient`；Flyway、统一响应体、全局异常、参数校验、springdoc

**验收**
1. 单测：service 层、JWT、状态机、上传校验、`@WebMvcTest`；JaCoCo 报告中 service 包行覆盖率 ≥ 70%
2. Testcontainers MySQL 集成测试在 CI 运行
3. `scripts/e2e_smoke.sh`：注册 → 对公共库提问（流式，带出处和风险提示）→ 上传一份私有 PDF → READY → 对私有库提问 → 查询会话历史，贴原文
4. 断开取消的证据
5. CI 全绿

#### S7 前端 + 一键启动
**交付**
- Vue3：登录、知识库和文档管理、对话页。对话页要能流式渲染、提示工具调用状态、显示出处角标（按 kind 区分图标，点开能看到页码和片段，或数据表和快照日期），并固定显示风险提示
- Dockerfile：backend、ai-service（CPU 版 torch、模型缓存卷、非 root）、mcp-tools、frontend（nginx 对 SSE 关闭缓冲）
- `docker compose --profile app up -d` 一键起全部服务；README 写清首次需要下载模型和采集数据的步骤

**验收**
1. `down -v` 清空后按 README 一键起全部服务并 healthy（贴 ps 原文）
2. 全栈 `docker stats` 实测，总量不超过 WSL 上限的 85%
3. 截图：综合题的流式回答同时带文档出处和数据库出处，页面上有风险提示
4. 证明 nginx 没有缓冲 SSE
5. CI 增加前端 build，全绿

#### S8 端到端回答评测 + 第一期收尾
**交付**
- 回答评测：在两个数据集的 test 上跑完整 Agent，对比 `vector` 与 `hybrid_rerank` 两种检索配置
- 判分：
  - numeric、entity、list 类用**规则判分**（数值归一化、单位、容差、集合比对），不依赖 LLM
  - text 类由 LLM 裁判按 answer_points 打 0/1/2 分
  - refusal 类判断是否正确拒答
  - advice_request 类判断是否拒绝推荐、是否出现违规表述
- 其他指标：工具选择准确率、SQL 结果正确率、收益计算正确率、出处准确率（被引片段是否命中 evidence；数据库类出处的表是否正确）、风险提示覆盖率（目标 100%）、首 token 和总延迟、token 用量与费用
- 用户对 text 类盲标不少于 30 条，报一致率和 kappa（写明 n），并说明裁判与被测模型同源的局限
- 所有 run 都保留；判分失败的条数单独上报
- README（架构图、快速开始、第一期指标表，每个数字都链接到结果文件）、`docs/LIMITATIONS.md`；打 tag `v0.1-phase1`

**验收**：每个数字都可追溯；人工一致性已完成；分母和失败数已上报；成本已记录；CI 全绿；tag 已推送。

### 第二期：后端高并发

#### S9 gRPC 改造
- proto：`Chat`（server streaming，ChatEvent 用 oneof 对应全部 SSE 事件，包括 citations 和 disclaimer）、`Retrieve`、`IngestDocument`、`DeleteDocument`；定义状态码映射
- Java 端 `GrpcAiServiceClient`：复用 channel、设置 deadline、**把取消传播到 Python**；可以通过配置切换回 http。Python 端 grpc.aio 与 FastAPI 在同一进程中运行
- **验收**：两端 in-process 单测；取消传播的证据；grpc 模式下 e2e 通过；HTTP 与 gRPC 的延迟预实验（写明 n 和条件）；CI 校验 proto 能编译；CI 全绿

#### S10 Redis：限流 + 配额 + 语义缓存
- 令牌桶：Lua 原子操作，分用户和全局两个维度；超限返回 429 + Retry-After。配额：每日调用次数和 token 数，定时回写 MySQL
- 语义缓存：Redis 向量索引，按 `kb_id + kb_version + DATA_AS_OF` 隔离。**用过 `get_latest_nav` 的回答不缓存**（时效性），荐基类和出错的回答也不缓存。命中时按同一事件协议回放，并标 `cache_hit`；风险提示照常追加
- 阈值校准集 `cache_pairs_v1.jsonl`：一部分是同义改写对，另一部分是**金融场景的难负例对**，即只差一个关键要素的问题，例如「A 类管理费」与「C 类销售服务费」、「一季度」与「二季度」、易混淆的两只基金。在不同阈值下报 precision/recall，据此选阈值
- **验收**：限流并发测试（Testcontainers，放行数恰好等于容量）；配额跨天；缓存误命中率报告；命中与未命中的延迟实测；时效性排除规则有单测；CI 全绿

#### S11 Kafka：季报批量入库 + 分布式锁
- 场景：某个报告期（例如 2026Q3，基金季报在季度结束后 15 个工作日内披露）的季报发布后，一次性把基金池里所有基金的季报批量入库
- Java：`POST /api/ingest-batches {report_period}` 按 MANIFEST 创建批次和各文档任务，经 outbox 表（与业务数据同一个事务）+ relay 发送到 `doc.ingest.requested`（key=doc_id）；消费 `doc.ingest.result` 并幂等更新；提供批次进度查询（总数、成功、失败、处理中）
- Python：aiokafka 消费者组，处理完成后才手动提交 offset；分布式锁 `lock:ingest:{doc_id}`（SET NX PX + token + Lua 比对后删除 + watchdog 续期）；sha256 没变且状态已 READY 的直接跳过；重试 N 次后进入 `doc.ingest.dlq`
- 如果 2026Q3 季报在本阶段开始时已经发布，就用真实的新一期做演示；否则用已有报告期演示「整批重投」，并在文档中说明
- **验收**：锁的单测（错误 token 不能释放、过期、续期）；同一批次重复提交加上 2 个消费者实例，每份文档只处理一次（用 chunk 数和日志证明）；消费者处理到一半被 kill 后，另一实例接管并最终一致；毒消息进入 DLQ；贴批次进度接口原文；CI 全绿

#### S12 压测基线 + 瓶颈定位
- mock LLM（OpenAI 兼容，流式，首 token 延迟和 tokens/s 可配，能按脚本返回 tool call）；`get_latest_nav` 在压测中用 stub，不压外部网站
- Locust 场景：A 纯检索；B 文档问答全链路（mock LLM）；C SQL 类问答全链路；D 语义缓存命中；E（可选）真实 LLM，并发不超过 5，记录费用
- 指标：QPS、平均延迟、P50/P95/P99、错误率、TTFT；阶梯加压找拐点；每个点重复 3 次取中位数，记录环境
- 定位：py-spy 火焰图、JFR 或 arthas、`docker stats`、分段打点
- **验收**：`docs/perf/baseline.md` + 原始数据 + 火焰图；瓶颈结论有数据证据

#### S13 优化 + 前后对比 + 第二期收尾
- 基于 S12 的证据做至少 3 项优化，每次只改一个变量，前后对比；失败的尝试也要记录
- 质量回归：每项优化后重跑 S4 的 test 检索评测，指标下降超过 0.01 的优化须写明取舍理由并经用户同意
- `docs/perf/optimization.md`；README 补第二期指标；打 tag `v0.2-phase2`
- **验收**：每项都有前后数字和原始数据；有质量回归结果；CI 全绿；tag 已推送

---

## 7. 执行批次（每批开一个新执行者对话，目标上下文 ≤ 400–500k）

B1、B2 实测：一个阶段约消耗 250–350k 上下文。所以从 B3 起，**一个对话只做一个阶段**（S5 拆成两半）。

| 批次 | 内容 | 批内用户关卡 |
|---|---|---|
| B1 ✅ | S1 + S2 | 确认基金池 |
| B2 ✅ | S3 + S4 前半 | 抽检评测集 |
| B3 | S4 收尾：修复切块碎片化 → 重新入库 → dev 调参 → test 评测 | — |
| B4 | S5 前半：mcp-tools 的 4 个工具（S5 交付第 1 项；验收 1、2、6，以及验收 4 中 mcp-tools 的部分） | — |
| B5 | S5 后半：文档 MCP、LangGraph、出处、风险提示、SSE（S5 其余交付与验收） | — |
| B6 | S6 Java 后端 | — |
| B7 | S7 前端 + 一键启动 | — |
| B8 | S8 回答评测 + 第一期收尾（tag v0.1-phase1） | 盲标、确认费用 |
| B9–B13 | 依次为 S9 gRPC、S10 Redis、S11 Kafka、S12 压测基线、S13 优化（tag v0.2-phase2），每个对话一个阶段 | — |

某个批次没做完时，新对话读 HANDOFF.md，从中断处接着做，批次划分不变。

## 6. 风险与预案

| 风险 | 预案 |
|---|---|
| 证监会站点不可访问 | 用东方财富公告 PDF（已实测可用）；如用户浏览器能访问证监会站点，人工抽查 3 份文件一致 |
| AkShare 接口变动或字段缺失 | 固定 AkShare 版本；缺失字段从 PDF 解析补全并标注 source；每个接口都有实测记录 |
| 东方财富净值接口不稳定 | 缓存 + 回退到快照，并标注 stale；测试和压测一律用 stub |
| 年报体量大、模板高度同质（20 只基金的文档大段雷同） | 基金实体过滤 + 上下文头 + 表格独立成块，在 dev 上验证 |
| 分红导致收益计算出错 | 统一使用复权口径，手算单测包含分红用例，并与来源方的 period_returns 对照 |
| 评测题时效性 | 所有题带时间锚点，以 DATA_AS_OF 为准；最新净值类题标 `volatile` 并实时判分 |
| 内存吃紧 | 停掉 ticket-qa 容器；模型懒加载；开发时 Java/Python 在 Windows 本机运行 |
| HF 下载慢、IK 装不上、DeepSeek 模型名变化 | hf-mirror；smartcn；模型名全部走 env，每次 run 记录响应模型名 |
