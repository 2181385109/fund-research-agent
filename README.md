# fund-research-agent（基金投研助手）

面向约 20 只医药医疗与科技主题公募基金的投研问答助手：Agent 按问题决定查披露文档（RAG：向量 + BM25 混合检索与重排）、查基金数据库（只读 Text2SQL）、做收益计算或查询最新净值，然后流式给出带出处的回答；Java/Python 双栈，第二期做 gRPC、Redis 限流与语义缓存、Kafka 批量入库和压测。

> **数据仅用于学习研究。**
> **本项目输出不构成投资建议。**

## 架构

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

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| S0–S4 | 脚手架 → 数据采集 → 文档入库 → 评测集 v1 → 混合检索 + 重排 + 检索评测 | ✅ 完成（2026-09-29），证据见 PROGRESS |
| S5 | MCP 工具 + LangGraph Agent + SSE | ✅ 完成（2026-09-29）：mcp-tools 四个工具（[说明](mcp-tools/README.md)）、文档 MCP `/mcp`、`POST /v1/chat/stream`（协议见 [docs/API.md](docs/API.md)），证据见 PROGRESS |
| S6 | Java 主后端 | ✅ 完成（2026-09-29）：auth / kb / document / conversation / chat（SSE 代理，断开取消上游）、私有知识库检索范围由服务端注入（ADR-043）；接口见 [docs/API.md](docs/API.md)，证据见 PROGRESS |
| S7 | 前端（Vue3）+ 四个 Dockerfile + 一键启动 | ✅ 完成（2026-09-29）：登录 / 知识库与文档 / 对话页（检索范围、四类出处、固定风险提示）；`docker compose --profile app up -d` 一键起全栈（见「一键启动全栈」）；证据见 PROGRESS |
| S8 | 回答评测 + 第一期收尾 | ✅ 完成（2026-10-01）：test 124 题 × 2 种检索配置的回答评测、人工盲标 36 条（见下方「第一期指标」），tag `v0.1-phase1` |
| S9 | gRPC 改造 | ✅ 完成（2026-10-02）：proto（`Chat` 流式 / `Retrieve` / `IngestDocument` / `DeleteDocument`）、ai-service 的 grpc.aio 与 FastAPI 同进程、backend `GrpcAiServiceClient`（复用 channel、deadline、取消传播；`AI_TRANSPORT=grpc|http` 可切回 http）；HTTP vs gRPC 延迟与取消延迟预实验见 [docs/perf/grpc_vs_http.md](docs/perf/grpc_vs_http.md)（预实验，不是压测基线），决策见 ADR-047 |
| S10 | Redis：限流 + 配额 + 语义缓存 | ✅ 完成（2026-10-02）：Lua 令牌桶（用户 + 全局，429 + `Retry-After`，并发 200 抢容量 20 → 放行恰好 20）、每日配额（跨天、定时回写 MySQL）、语义缓存（Redis 向量索引，按检索范围 + 数据快照 + 模型隔离，不缓存最新净值 / 荐基 / 出错的回答，命中按同一事件协议回放并标 `cache_hit`，**默认关闭**）。**纯向量阈值分不开「只差一个关键要素」的问题**（零误命中点 0.995，recall 0），所以命中还要过关键要素守卫；test 上（只跑一次）recall 77 / 92 = 83.7%、难负例误命中 3 / 132 = 2.3%；命中 vs 未命中首字 57 ms vs 5.4 s（n = 10，真实 LLM）。报告 [docs/perf/semantic_cache.md](docs/perf/semantic_cache.md)，决策见 ADR-048 |
| S11 | Kafka：季报批量入库 + 分布式锁 | ✅ 完成（2026-10-03）：`POST /api/ingest-batches` → 批次 / 任务 / outbox 同一事务 → relay（`FOR UPDATE SKIP LOCKED`）发 `doc.ingest.requested`；ai-service 消费者组手动提交 offset，`fra:lock:ingest:{doc_id}`（SET NX PX + token + Lua + watchdog）、sha256 未变且 READY 则跳过、重试 3 次或不可重试错误进 `doc.ingest.dlq`；结果 topic 幂等更新、进度接口。**2 个消费者实例 + 每份请求发两遍：20 份 2026Q2 季报只入库 20 次（另 20 次 SKIPPED），前后 664 块的 chunk_id 集合一致**；入库中途 kill -9，另一实例约 30 s 后接管并最终一致；毒消息进 DLQ。2026Q3 季报尚未发布，演示是对 2026Q2 的「整批重投」。证据 [reports/s11/](reports/s11/)，决策 ADR-049，局限见 [LIMITATIONS](docs/LIMITATIONS.md)「批量入库（S11）」 |
| S12–S13 | 第二期：压测 → 优化 | 未开始 |

详细计划见 [docs/PLAN.md](docs/PLAN.md)，逐阶段进度与验收证据见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 第一期指标（S8 回答评测）

**每个数字都是对应 `summary.json` 里的一个字段**，表里给出链接和字段路径（`R` = [`reports/answer_eval/20260929T171529Z/summary.json`](reports/answer_eval/20260929T171529Z/summary.json) 的 `results`）。评测输入只用两个评测集的 **test 划分**：`fund_qa_v1` 79 题 + `agent_tasks_v1` 45 题 = **124 题**；被测模型 deepseek-flash（真实 LLM），两种检索配置 `vector` 与 `hybrid_rerank` 各跑一遍（每题一次），数据快照 `DATA_AS_OF=2026-09-28`，入库 run `20260929T141806Z`。**没有失败的 run、没有 API 报错重试、没有判分失败的题**（分母都是 124，见 `failed_runs`、`api_error_retries`）。
**口径**（ADR-046）：numeric 题按「回答里**第一个**相容数字」判（主口径 first，是保守的下界口径——回答先给背景数字再给答案时会被判错，逐条说明见 [numeric_any_vs_first.md](reports/answer_eval/20260929T171529Z/numeric_any_vs_first.md)）；entity / list 规则判分；text 类由 **deepseek-flash 裁判**按要点打 0/1/2（裁判与被测同一个模型，可能偏宽，见 [LIMITATIONS](docs/LIMITATIONS.md)）；refusal 类裁判二值判断。「统一得分」= 规则类对 1 / 错 0，text 类 = 裁判分 / 2，refusal 类对 1 / 错 0 的平均。

| 指标（分母） | hybrid_rerank | vector | 字段（`R.<配置>.…`） |
|---|---|---|---|
| 统一得分（124） | [0.875](reports/answer_eval/20260929T171529Z/summary.json) | [0.875](reports/answer_eval/20260929T171529Z/summary.json) | `overall.acc_all.value` |
| numeric，first 口径（50，含 3 道最新净值题） | [0.720](reports/answer_eval/20260929T171529Z/summary.json) | [0.740](reports/answer_eval/20260929T171529Z/summary.json) | `by_answer_type.numeric.acc_all.value` |
| entity（17） | [0.941](reports/answer_eval/20260929T171529Z/summary.json) | [0.941](reports/answer_eval/20260929T171529Z/summary.json) | `by_answer_type.entity.acc_all.value` |
| list，标准项全部出现（10） | [1.000](reports/answer_eval/20260929T171529Z/summary.json) | [1.000](reports/answer_eval/20260929T171529Z/summary.json) | `by_answer_type.list.acc_all.value` |
| text，裁判分 / 2（36） | [0.986](reports/answer_eval/20260929T171529Z/summary.json) | [0.958](reports/answer_eval/20260929T171529Z/summary.json) | `by_answer_type.text.acc_all.value` |
| text，裁判均分 0–2（36） | [1.972](reports/answer_eval/20260929T171529Z/summary.json) | [1.917](reports/answer_eval/20260929T171529Z/summary.json) | `text_judge.mean_score_0_2` |
| refusal 正确拒答（11：不可回答 7 + 荐基 4） | [1.000](reports/answer_eval/20260929T171529Z/summary.json) | [1.000](reports/answer_eval/20260929T171529Z/summary.json) | `by_answer_type.refusal.acc_all.value` |
| advice_request 正确拒绝推荐（4） | [4/4](reports/answer_eval/20260929T171529Z/summary.json) | [4/4](reports/answer_eval/20260929T171529Z/summary.json) | `compliance.advice_request_correct` |
| 工具选择：必需工具都调用（118） | [99/118 = 83.9%](reports/answer_eval/20260929T171529Z/summary.json) | [99/118 = 83.9%](reports/answer_eval/20260929T171529Z/summary.json) | `tool_selection.required_recall` |
| SQL 类题端到端正确，first 口径（tool_sql 15） | [12/15](reports/answer_eval/20260929T171529Z/summary.json) | [12/15](reports/answer_eval/20260929T171529Z/summary.json) | `sql.end_to_end_correct` |
| 收益计算正确，first 口径（calc_return 6） | [4/6](reports/answer_eval/20260929T171529Z/summary.json) | [5/6](reports/answer_eval/20260929T171529Z/summary.json) | `calc_return_correct` |
| 最新净值正确，实时对照（3） | [3/3](reports/answer_eval/20260929T171529Z/summary.json) | [3/3](reports/answer_eval/20260929T171529Z/summary.json) | `latest_nav_correct` |
| 出处准确率（有可核对出处的 111） | [86/111 = 77.5%](reports/answer_eval/20260929T171529Z/summary.json) | [73/111 = 65.8%](reports/answer_eval/20260929T171529Z/summary.json) | `citation_accuracy.overall` |
| 风险提示覆盖率（全部 124 个 run） | [124/124 = 100%](reports/answer_eval/20260929T171529Z/summary.json) | [124/124 = 100%](reports/answer_eval/20260929T171529Z/summary.json) | `disclaimer_coverage` |
| 首 token p50 / p95（ms，124） | [7836 / 14204](reports/answer_eval/20260929T171529Z/summary.json) | [4112 / 6232](reports/answer_eval/20260929T171529Z/summary.json) | `latency_ms.first_token` |
| 总延迟 p50 / p95（ms，124） | [7996 / 15370](reports/answer_eval/20260929T171529Z/summary.json) | [4885 / 9668](reports/answer_eval/20260929T171529Z/summary.json) | `latency_ms.total` |
| Agent token（输入 / 输出） | [1,453,580 / 55,250](reports/answer_eval/20260929T171529Z/summary.json) | [1,559,449 / 61,101](reports/answer_eval/20260929T171529Z/summary.json) | `tokens_and_cost.agent_*_tokens` |
| 费用上界 USD（非高峰价，含裁判） | [0.2573](reports/answer_eval/20260929T171529Z/summary.json) | [0.2769](reports/answer_eval/20260929T171529Z/summary.json) | `tokens_and_cost.cost_usd_upper_bound.off_peak`；与估算的对照见 [cost_actual_vs_estimate.md](reports/answer_eval/20260929T171529Z/cost_actual_vs_estimate.md) |

**两种检索配置的配对比较**（逐题统一得分，差 = hybrid_rerank − vector，配对 bootstrap 10000 次，[`comparison_vector_to_hybrid_rerank`](reports/answer_eval/20260929T171529Z/summary.json)）：124 题 **+0.000，95% CI [−0.048, +0.048]**（`all_questions`）；调用了文档检索的 84 题 +0.012 [−0.054, +0.083]（`questions_where_docs_tool_used_in_either_run`）。**端到端得分上看不出两种检索配置的差别**；出处准确率 77.5% vs 65.8% 是描述性差异（未做置信区间）。首 token / 总延迟 vector 更短是因为它不做 CPU 重排（`search_fund_documents` 工具耗时中位 0.43 s vs 5.2 s）。样本小（每类 2–15 题）、每题只跑一次，细小差异不能解读为配置差异。

读表时注意：entity 的 hybrid_rerank 一题（qa-0101）是判分器对二选一题的误判（回答结论正确），vector 一题（qa-0023）是真实错误；两处都在 [numeric_any_vs_first.md](reports/answer_eval/20260929T171529Z/numeric_any_vs_first.md) 末尾逐条说明，**没有**为此改口径。

**人工盲标 vs LLM 裁判**（text 类，n = 36 行，来自 26 道不同的题，两个配置的回答混合按 topic 分层抽样，种子 20260930；人工标注时看不到裁判分，全部数字来自 [`blind_agreement.json`](reports/answer_eval/20260929T171529Z/blind_agreement.json)）：

| 指标 | 值 | 字段 |
|---|---|---|
| 精确一致率（n = 36） | [34/36 = 94.4%](reports/answer_eval/20260929T171529Z/blind_agreement.json)（Wilson 95% 区间 81.9%–98.5%） | `exact_agreement` |
| Cohen kappa（无权 / 线性加权 / 满分 vs 非满分） | [0.478 / 0.478 / 0.478](reports/answer_eval/20260929T171529Z/blind_agreement.json) | `cohen_kappa_unweighted`、`cohen_kappa_linear_weighted`、`binary_kappa_full_vs_not` |
| 人工均分 / 裁判均分（0–2） | [1.917 / 1.972](reports/answer_eval/20260929T171529Z/blind_agreement.json) | `human_mean_score`、`judge_mean_score` |

混淆矩阵（行 = 人工，列 = 裁判）：人工 0 分 0 行；人工 1 分 3 行（裁判给 1 分 1 行、给 2 分 2 行）；人工 2 分 33 行（裁判全给 2 分）。**一致率高，kappa 只有中等**：人工只给了 3 个非满分，标签极不均衡，kappa 对这 3 行极其敏感，不宜单独解读。**两处分歧方向相同——裁判给 2、人给 1（裁判偏宽）**，且都是 no_tool 类通用概念题（抽到的 2 行 no_tool 全部分歧、其余 34 行全部一致）；只有 2 行，不足以量化偏宽程度，也不能推广到 no_tool 以外。裁判与被测是同一个模型（deepseek-flash）的局限见 [LIMITATIONS](docs/LIMITATIONS.md) S8-2；盲标只覆盖 text 类，refusal 类裁判判断未人工核对。本表的裁判分没有因盲标结果调整。

**检索评测（S4，test，n=72）**：最终配置 hybrid_rerank 的 hit@5 [0.653](reports/retrieval/20260929T090548Z/summary.json)、nDCG@10 [0.532](reports/retrieval/20260929T090548Z/summary.json)（`results.hybrid_rerank.overall`），vector 单路 nDCG@10 [0.200](reports/retrieval/20260929T090548Z/summary.json)。容器内重新入库后同配置复现：hybrid_rerank nDCG@10 [0.530](reports/retrieval/20260929T164941Z/summary.json)，bm25 路有小幅差异、原因未验证，见 [LIMITATIONS](docs/LIMITATIONS.md)「回答评测方法」第 1 条。

已知局限汇总见 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)。

## 快速开始

本机环境（WSL2 + Docker、JDK 17、Python 3.12）的搭建与开发期启动命令见 [docs/SETUP.md](docs/SETUP.md)。技术选型与决策记录见 [docs/DECISIONS.md](docs/DECISIONS.md)。

### 一键启动全栈（Docker Compose，profile `app`）

下面的命令在 WSL2 的 Docker Engine 里执行（本机没有 Docker Desktop 的写法见 SETUP §4；有 Docker 的 Linux / macOS 直接去掉 `wsl.exe … bash -c '…'` 外壳）。需要 WSL 内存上限 10GB（SETUP §2），全栈内存上限合计约 8.9GB（PLAN §3），**先停掉其他占内存的容器**。

```bash
# 1. 生成 .env（随机密码 + DeepSeek 密钥；密钥文件只有一行 key）
python scripts/gen_env.py --llm-key-file <DeepSeek key 文件>

# 2. 构建镜像并起全部服务：infra（MySQL / Redis / ES / Milvus / Kafka）+ mcp-tools / ai-service / backend / frontend
docker compose --profile app up -d --build
docker compose --profile app ps          # 全部 healthy 后再继续

# 3. 首次：下载本地向量与重排模型到命名卷 model_cache（bge-small-zh ≈ 95MB，bge-reranker-base ≈ 1.1GB；HF_ENDPOINT 默认 hf-mirror）
docker compose --profile app run --rm ai-service python -m fund_ai.models_cli
```

**首次还需要数据**（披露 PDF 与结构化快照因版权 / 来源条款不入库，见「数据与合规」）：

```bash
# 4. 采集（需要外网，节流每秒 ≤ 1 个请求）：基金池 → 披露 PDF → 结构化数据 → 质量报告，步骤见 data-pipeline/README.md
#    已有 data/raw、data/snapshots 时跳过采集，只做导入：
cd data-pipeline && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m fund_pipeline.load --as-of 2026-09-28        # 快照 → fund_data 库（连宿主机发布的 MySQL 端口）

# 5. 披露文档入库 Milvus + ES（CPU 上约 20 分钟；幂等，可重复执行）
cd .. && docker compose --profile app run --rm ai-service python -m fund_ai.ingest.cli
```

然后打开 <http://127.0.0.1:8088>，注册账号即可提问。对话页可以选择检索范围（公共库 = 基金披露文件；私有库 = 你在「知识库」页上传的文件），回答带四类出处（文档 / 数据库 / 计算 / 接口），页面底部的风险提示固定显示、不能关闭。停止与清理：`docker compose --profile app down`（保留数据卷）；`down -v` 会清空所有数据卷，之后要重做第 3–5 步。

内存实测与验收证据见 [docs/PROGRESS.md](docs/PROGRESS.md) 的 S7 一节。

## 数据与合规

- 披露 PDF 的版权归基金管理人，抓取的数据受来源网站条款约束：原始 PDF 与数据快照**不提交**到本仓库，仓库只包含采集脚本、`MANIFEST` 和自造的测试 fixture。
- 每条回答末尾由服务端固定追加风险提示；系统不输出荐基、择时或买卖建议。

## License

[MIT](LICENSE)
