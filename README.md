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
| S0 | 脚手架与基础设施（compose infra、health 接口、LLM 冒烟、安全扫描、CI） | ✅ 完成（2026-09-29），待统筹审查 |
| S1–S8 | 第一期：数据采集 → 文档入库 → 评测集 → 混合检索 → Agent → Java 后端 → 前端 → 回答评测 | 未开始 |
| S9–S13 | 第二期：gRPC → Redis → Kafka → 压测 → 优化 | 未开始 |

详细计划见 [docs/PLAN.md](docs/PLAN.md)，逐阶段进度与验收证据见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 快速开始

本机环境（WSL2 + Docker、JDK 17、Python 3.12）的搭建与启动命令见 [docs/SETUP.md](docs/SETUP.md)。技术选型与决策记录见 [docs/DECISIONS.md](docs/DECISIONS.md)。

## 数据与合规

- 披露 PDF 的版权归基金管理人，抓取的数据受来源网站条款约束：原始 PDF 与数据快照**不提交**到本仓库，仓库只包含采集脚本、`MANIFEST` 和自造的测试 fixture。
- 每条回答末尾由服务端固定追加风险提示；系统不输出荐基、择时或买卖建议。

## License

[MIT](LICENSE)
