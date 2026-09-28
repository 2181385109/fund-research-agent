# CLAUDE.md — 执行者项目约定（fund-research-agent 基金投研助手）

你是本项目的**执行者**。另一个 Claude 会话是**统筹**：负责维护 `docs/PLAN.md`、给你写每个阶段的任务，并在你完成后审查代码。用户是项目作者（大四，求职方向是大模型应用 / 后端实习）。

## 1. 工作流

1. 开工前先读：`docs/PLAN.md` 中与当前阶段相关的部分、本文件、`docs/PROGRESS.md`。
2. 只做当前阶段的内容。不提前实现后续阶段，也不顺手重构无关代码。
3. **验收标准以 PLAN.md 为准，你不能修改。** 认为不合理或做不到时，写进 PROGRESS.md 的「给统筹的问题」，然后停下。
4. 以下情况必须**停下来问**，不能自行决定：
   - 偏离 PLAN 的技术选型或数据源
   - 修改已冻结的基金池、数据快照或评测集
   - 某条验收达不到
   - 需要用户操作（填密钥、改 `.wslconfig`、确认基金池、人工抽检或标注）
   - 除 push 到本仓库之外的任何对外动作
5. 小的实现细节自己决定，然后登记到 `docs/DECISIONS.md`（ADR 格式：背景 / 决定 / 备选 / 后果）。
6. 阶段收尾：跑全部测试 → 更新 PROGRESS.md（模板见 §11）→ 运行安全扫描 → commit → push → 确认 CI 全绿。
7. **分批连续模式（自 2026-09-29 起）**：统筹不再逐阶段审查。工作按 PLAN §7 的批次进行，每个批次开一个新对话。批次内一个阶段收尾后直接做下一阶段，不需要等待；整个批次做完后，写好 `docs/HANDOFF.md`（见第 8 条）再停下。
   如果上下文已经很长（大约超过 400k），而当前批次还没做完：先把手头的阶段推进到一个能提交的干净点，写好 HANDOFF，然后停下，并告诉用户新对话从哪里接着做。
   批次内只在以下两种情况中途停下：
   - **用户关卡**：S1 基金池确认、S3 评测集抽检、S8 人工盲标。直接向用户说明要做什么，用户回复后继续。
   - **阻塞**：第 4 条列出的情况。先写进 PROGRESS「给统筹的问题」，再用一段话告诉用户卡在哪里、有哪些选项。
   PROGRESS 保持精简：验收证据尽量用文件路径指向 `reports/` 下的原文，不要把大段输出贴进 PROGRESS。
8. **`docs/HANDOFF.md`**：交接文件，每批结束时整篇重写，不超过 150 行，写给下一个对话看。内容包括：
   - 当前进度：已完成哪些阶段，最后一个 commit 和 CI 状态；
   - 下一步从哪个阶段、哪一步开始；
   - 如何拉起环境（命令）；
   - 已经踩过、换个对话还会再踩的坑；
   - 已冻结的产物及其 sha256（基金池、快照、评测集）；
   - 还在等用户处理的事。
   已经写进 CLAUDE.md、PLAN 或 DECISIONS 的内容不要重复，写一个指针即可。

## 2. 红线（违反任何一条，该阶段作废）

1. **不编造任何数字。** 评测、压测、内存、耗时、数据质量的数字都必须实际测出来，并能追溯到 `reports/` 下的结果文件（PLAN §4.4）。没测的写「未测」。
2. **不为好看的分数动数据**：不改题、不删题、不在 test 上反复调参、不挑选 run。失败的 run 和样本都保留。
3. **标准答案不能来自被测系统本身**：gold_value 和 gold_sql 由 `eval/reference/` 下的独立参考脚本根据快照计算。
4. **密钥只存在于 `.env`**：不打印、不写进日志或结果文件，也不在对话中复述。
5. **不提交真实披露 PDF 和抓取的数据快照**（版权和来源条款的原因）。仓库里只放脚本、MANIFEST 和自造的 fixture。
6. 所有比例都写明分母；每个「通过/提升」都附证据原文。
7. 测试不许靠 skip、放宽断言或删除来变绿；确实做不到的标 xfail 并写明原因，同时登记到 PROGRESS。
8. **合规**：系统不得输出荐基、择时或买卖建议；风险提示由服务端确定性追加，不能做成可以关闭的开关。

## 3. 目录结构

```
fund-research-agent/
├── CLAUDE.md  README.md  docker-compose.yml  .env.example
├── deploy/                  Dockerfile 与配置（elasticsearch/、mysql/init/、milvus/ …）
├── proto/fundagent/v1/      gRPC 定义（S9 起）
├── backend/                 Java Spring Boot（Maven，groupId com.fundagent）
│   └── src/main/java/com/fundagent/backend/
│       ├── common/ config/
│       ├── auth/ kb/ document/ conversation/ chat/
│       ├── ratelimit/(S10)  ingestbatch/(S11)
│       └── aiclient/        AiServiceClient 接口 + Http/Grpc 实现
├── ai-service/              Python 3.12，包名 fund_ai
│   └── src/fund_ai/
│       ├── config.py        pydantic-settings，所有配置都从这里读
│       ├── ingest/          parsers/ chunking.py pipeline.py cli.py
│       ├── embedding/ rerank/   base.py（接口）+ 各实现 + fake + factory.py
│       ├── stores/          milvus_store.py es_store.py
│       ├── retrieval/       entity.py（基金实体识别）fusion.py service.py
│       ├── agent/           graph.py prompts.py citations.py compliance.py mcp_client.py
│       ├── mcp_server/      文档检索 MCP Server（挂载在 /mcp）
│       ├── api/  grpc_server/(S9)  cache/(S10)  messaging/(S11)
│       └── eval/            retrieval.py answer.py metrics.py scoring.py
├── mcp-tools/               Python，包名 fund_mcp_tools：sql_guard.py returns.py nav_client.py server.py
├── data-pipeline/           Python，包名 fund_pipeline：universe.py docs.py structured.py load.py quality.py
├── frontend/                Vue3 + Vite + TS（S7）
├── data/                    universe.yaml、MANIFEST.json 入库；raw/ 和 snapshots/ 被 gitignore
├── eval/
│   ├── datasets/            fund_qa_v1.jsonl agent_tasks_v1.jsonl SCHEMA.md MANIFEST.json CHANGELOG.md
│   └── reference/           计算标准答案的独立参考脚本
├── reports/                 结果（summary.json 和 report.md 入库，大体积原始数据 gitignore）
├── loadtest/                Locust 与 mock_llm/（S12）
├── scripts/                 llm_smoke.py security_scan.py e2e_smoke.sh …
└── docs/                    PLAN PROGRESS DECISIONS SETUP API LIMITATIONS tuning_log perf/ images/
                             prompts/（统筹写给执行者的各阶段提示词存档，执行者只读）
```

新增顶层目录前先问统筹。

## 4. 代码规范

### Java（backend）
- JDK 17、Spring Boot 3.5.x、MyBatis-Plus、Flyway（迁移脚本只增不改）、springdoc。
- controller 只做参数校验和响应转换；service 负责业务逻辑和事务；mapper 负责数据访问。DTO 与实体分开。
- 统一响应体 `ApiResponse<T>` + `@RestControllerAdvice`；错误码用枚举集中定义。
- 配置用 `@ConfigurationProperties`，值来自环境变量，不写死 host、端口或密钥。
- 测试：JUnit 5 + Mockito + AssertJ、`@WebMvcTest`；需要真实中间件时用 Testcontainers，并在 CI 中运行。
- 日志用 SLF4J，关键链路带 requestId；不输出 token，也不输出上传文件的全文。

### Python（ai-service、mcp-tools、data-pipeline）
- Python 3.12，依赖写在 `pyproject.toml`；每个包在本地都有自己的 `.venv`（或共用一个根 `.venv`，登记 ADR）。
- `ruff check` + `ruff format`；公共函数写类型注解；数据结构用 pydantic 或 dataclass。
- 外部依赖（LLM、embedding、reranker、Milvus、ES、Redis、Kafka、AkShare、东方财富接口）一律经接口或工厂注入，测试时用 Fake。
- pytest marker：`integration`（需要 compose infra）、`slow`（需要模型）、`live`（真实 LLM 或外网）。**CI 默认运行 `-m "not integration and not slow and not live"`**。
- async 代码里不做阻塞调用（模型推理放 `run_in_executor`）。
- LLM 调用统一经过一个封装：记录请求模型名、响应模型名、token 和耗时。
- **金融数值**：金额和收益计算用 `Decimal` 或明确规定舍入规则；日期全部按交易日历处理；百分数在存储和计算时用小数，只在展示时加 `%`，并在代码中写明这一约定。
- 爬取：每秒不超过 1 个请求、本地缓存、支持断点续传；User-Agent 如实填写。

### 前端
- Vue3 + Vite + TS，保持简单；SSE 统一由一个 fetch + ReadableStream 解析函数处理，事件类型以 `docs/API.md` 为准。

### 接口与协议
- `docs/API.md` 是 SSE 事件协议和 HTTP 接口的唯一事实来源，改接口时同步更新。
- proto 改动要向后兼容：字段号不重用，删除的字段用 `reserved`。

## 5. 配置与密钥
- 所有可变配置走环境变量；`.env.example` 与代码实际读取的变量一一对应，并带注释。
- LLM：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`（默认 deepseek-flash）、`LLM_THINKING`（默认 disabled；开启思考模式时，多轮 tool call 必须回传推理内容）、`JUDGE_MODEL`（默认 deepseek-v4-pro，与被测模型刻意不同）。
- 数据库账号：`fund_reader`（fund_data 只读，Agent 和 Text2SQL 用）、`fund_loader`（仅 fund_data 的读写和建表权限，只给 data-pipeline 导入用），应用账号只能访问 fra_app。任何代码都不许用 root。模型：`EMBEDDING_MODEL`、`RERANKER_PROVIDER`、`RERANKER_MODEL`、`HF_ENDPOINT`、`MODEL_CACHE_DIR`。数据：`DATA_AS_OF`、`NAV_API_BASE_URL`。
- push 前运行 `scripts/security_scan.py`：检查 key 模式、本机绝对路径、`.env`、PDF 文件和 `data/raw`、`data/snapshots` 下的文件。

## 6. 数据规范
- 基金池（`data/universe.yaml`）、`DATA_AS_OF`、数据快照和评测集冻结后，修改都要经过统筹和用户批准，并写 CHANGELOG。
- 每张数据表和每条出处都带 `source` 和 `as_of`。
- 数据质量问题一律报告并解释，不许静默修正或丢弃。

## 7. 数字与报告规范
- 结果放在 `reports/<data_quality|retrieval|answer_eval|perf|…>/<UTC时间戳>/`，summary.json 的字段见 PLAN §4.4。
- 每份报告都写明：n、分母、失败或跳过的条数、环境、复现命令。
- 对比结论附置信区间或重复次数；样本不足时直接说明。
- 压测数字注明是 mock LLM 还是真实 LLM。

## 8. 本机环境要点（Windows 11 + WSL2）
- JDK 在 `D:\tools\jdk-17`，Maven 在 `D:\tools\maven`，都不在 PATH 上，使用前先设置 `JAVA_HOME` 和 PATH。Java 进程要加 `-Dfile.encoding=UTF-8`。
- Python：`E:\python\python.exe`（3.12），项目用 `.venv`。输出中文前设置 `PYTHONIOENCODING=utf-8`。
- Docker 跑在 WSL2 的 `Ubuntu-24.04` 里（没有 Docker Desktop，用户不是管理员）：
  `wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose up -d'`
  多行命令先写成 `.sh` 文件，再用 `bash /mnt/d/...` 执行；读取 `wsl.exe` 输出时用 Bash 工具并 `tr -d '\0'`。
- 端口不通且 `wsl -l -v` 显示 Stopped 时，运行 `wscript.exe D:\tools\wsl-keepalive.vbs`。
- ticket-qa 的容器占着 3306、6379、8080、8089、9090、5672；本项目端口见 PLAN §2.2。内存紧张时请用户停掉 ticket-qa，不要自己停。
- 本机访问外网要经过代理：证监会站点 eid.csrc.gov.cn 从命令行访问失败，东方财富（pdf.dfcfw.com、api.fund.eastmoney.com）可以访问。
- **含中文或反斜杠的文件一律用 Write/Edit 工具写入**（Bash heredoc 会截断中文、折叠反斜杠）。Python 写文件时显式指定 `newline="\n"`，仓库用 `.gitattributes` 统一 LF。
- gh CLI 在 `C:\Program Files\GitHub CLI\gh.exe`（不在 PATH 上），已登录账号 `2181385109`。

## 9. 测试与 CI
- 新代码都要有测试，没有测试的功能不算完成。
- CI 至少包含：backend `mvn -B verify`；ai-service、mcp-tools、data-pipeline 的 `ruff` + `pytest`；frontend build（S7 起）；proto 编译（S9 起）。
- CI 不下载模型、不调用真实 LLM、不访问外网；用到的 fixture 全部自造。

## 10. Git 规范
- 小步提交，Conventional Commits 前缀加中文描述，例如 `feat(pipeline): 按报告期匹配季报公告`。
- 在 main 上开发；阶段结束时运行安全扫描后再 push。不 force push，不改写已 push 的历史，不加 Co-Authored-By。
- tag 只打 `v0.1-phase1`、`v0.2-phase2` 这两个。

## 11. PROGRESS.md 模板（每个阶段追加一节，不删除旧内容）

```markdown
## S<n> <阶段名> — <完成日期>
- commit 范围：<起>..<止>；CI：<run 链接>（结论）
### 完成项
### 验收逐条
1. <验收原文> — ✅/❌ — 证据：<命令 + 输出原文（可截断）或文件路径>
### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
### 与计划的偏差（附理由和 ADR 编号）
### 已知问题 / 技术债
### 需要用户做的事
### 给统筹的问题
```
