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
| S7 | 前端（Vue3）+ 四个 Dockerfile + 一键启动 | ✅ 代码与镜像完成；全栈验收证据见 PROGRESS「S7」 |
| S8 | 回答评测 | 未开始 |
| S9–S13 | 第二期：gRPC → Redis → Kafka → 压测 → 优化 | 未开始 |

详细计划见 [docs/PLAN.md](docs/PLAN.md)，逐阶段进度与验收证据见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 快速开始

本机环境（WSL2 + Docker、JDK 17、Python 3.12）的搭建与开发期启动命令见 [docs/SETUP.md](docs/SETUP.md)。技术选型与决策记录见 [docs/DECISIONS.md](docs/DECISIONS.md)。

### 一键启动全栈（Docker Compose，profile `app`）

下面的命令在 WSL2 的 Docker Engine 里执行（本机没有 Docker Desktop 的写法见 SETUP §4；有 Docker 的 Linux / macOS 直接去掉 `wsl.exe … bash -c '…'` 外壳）。需要 WSL 内存上限 10GB（SETUP §2），全栈内存上限合计约 8.9GB（PLAN §3），**先停掉其他占内存的容器**。

```bash
# 1. 生成 .env（随机密码 + DeepSeek 密钥；密钥文件只有一行 key）
python scripts/gen_env.py --llm-key-file <DeepSeek key 文件>

# 2. 构建镜像并起全部服务：infra（MySQL / Redis / ES / Milvus）+ mcp-tools / ai-service / backend / frontend
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
