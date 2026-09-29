# 决策记录（ADR）

格式：背景 / 决定 / 备选 / 后果。ADR-001 ~ ADR-014 登记 PLAN §2 已定的技术选型；ADR-015 起为执行阶段自行决定的实现细节。已接受的 ADR 不改写，被替代时新增一条并在旧条目标注「被 ADR-xxx 替代」。

---

## ADR-001 LLM：DeepSeek（OpenAI 兼容 API）
- **背景**：Agent、回答评测需要支持流式与 tool calling 的通用对话模型；不部署本地大模型（PLAN §0）。
- **决定**：DeepSeek，走 OpenAI 兼容接口；`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `JUDGE_MODEL` 全部走 env。每次调用同时记录**请求模型名**和**响应 `model` 字段**。具体默认模型见 ADR-018。
- **备选**：其他 OpenAI 兼容云模型（改 env 即可替换）；本地模型（超出 16GB 笔记本能力，第一期不做）。
- **后果**：模型名可能被服务方调整（另一个项目记录到 2026-07 `deepseek-chat` / `deepseek-reasoner` 被弃用），所以每次 run 的 summary.json 都要写响应模型名。

## ADR-002 Embedding：本地 `BAAI/bge-small-zh-v1.5`（512 维）
- **背景**：中文披露文档检索；CI 与笔记本内存有限。
- **决定**：bge-small-zh-v1.5，经 `Embedder` 接口注入，`EMBEDDING_MODEL` 可替换；查询指令前缀做成开关，在 S4 dev 集上裁决。
- **备选**：bge-large-zh（效果可能更好，内存与延迟约 3 倍）；云端 embedding API（引入额外费用与外网依赖）。
- **后果**：S2 起需要下载模型（`HF_ENDPOINT` 默认 hf-mirror，缓存目录 `MODEL_CACHE_DIR`）；CI 用 `FakeEmbedder`。

## ADR-003 Reranker：本地 `BAAI/bge-reranker-base`
- **决定**：`Reranker` 接口 + `CrossEncoderReranker` + `NoopReranker`，`RERANKER_PROVIDER` / `RERANKER_MODEL` 可替换。
- **备选**：不重排（作为消融对照保留）；bge-reranker-large（内存更大）。
- **后果**：S4 主对比 `vector` vs `hybrid_rerank`。

## ADR-004 向量库：Milvus 2.5.x standalone（内嵌 etcd + 本地存储，单容器）
- **背景**：需要带标量过滤（fund_code、doc_type）的向量检索；笔记本内存预算 2g。
- **决定**：`milvusdb/milvus:v2.5.27` 单容器 standalone。环境变量与 `embedEtcd.yaml` 取自官方 `scripts/standalone_embed.sh`（v2.5.27 标签）：`ETCD_USE_EMBED=true`、`ETCD_DATA_DIR=/var/lib/milvus/etcd`、`ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml`、`COMMON_STORAGETYPE=local`，`seccomp:unconfined`；数据卷 `milvus_data`；healthcheck 用 `:9091/healthz`；不对宿主机暴露 2379。
- **备选**：官方三容器 compose（etcd + minio + milvus，多占约 0.5–1g）；Milvus Lite（不支持 Windows 本机/无服务化）；pgvector / Redis 向量（功能与生态弱于 Milvus，Redis 向量留给语义缓存）。
- **后果**：S0 实测启动后常驻约 330MiB（mem_limit 2g）。

## ADR-005 关键词检索：Elasticsearch 8 + IK 分词
- **背景**：BM25 路需要中文分词；IK 插件版本必须与 ES 严格一致。
- **决定**：ES **8.19.22** + IK **8.19.22**，`deploy/elasticsearch/Dockerfile` 在官方镜像上 `elasticsearch-plugin install --batch` 安装。S0 核实：IK 8.19.22 在 infinilabs 发布站 `release.infinilabs.com/analysis-ik/stable/` 返回 200（WSL 内直连约 1.4 秒），GitHub Releases 上 8.19.x 没有对应资产（404）。安装时 ES 提示该插件需要 `outbound_network` 权限（IK 远程词典功能），本项目不配置远程词典。单节点、堆 `-Xms1g -Xmx1g`、`xpack.ml.enabled=false`（省去 ML 本地进程）、关闭磁盘水位检查（笔记本磁盘余量小时避免索引变只读）。
- **备选**：analysis-smartcn（官方插件，兜底方案，本次未启用）；ES 9.x（IK 发布包与生态更新慢）；OpenSearch。
- **后果**：S0 `_analyze` 实测：「基金管理人的管理费率」ik_max_word 切出 8 个 token（基金/管理人/管理/人/的/管理费/管理/费率），ik_smart 切出 5 个（基金/管理人/的/管理/费率）。ik_smart 把「管理费率」切成「管理/费率」而不是「管理费/率」，S2 起写入用 ik_max_word、检索用 ik_smart（PLAN S2），必要时加自定义词典。

## ADR-006 本地开发关闭 ES 安全认证
- **背景**：ES 8 默认开启 TLS + 认证，本地开发要为每个客户端配置证书和密码。
- **决定**：`xpack.security.enabled=false`，仅限本地开发。WSL2 NAT 模式下容器端口只经 Windows 本机 localhost 转发可达；ES 里只有公开披露文件的切块，无个人或敏感数据。
- **备选**：开启安全并在 `.env` 生成 `ELASTIC_PASSWORD`（更接近生产，增加配置负担）。
- **后果**：不得原样用于任何可被他人访问的部署；如有部署需求，新增 ADR 开启安全。

## ADR-007 Redis：`redis:8.x` 社区版（自带 Query Engine）
- **背景**：S10 语义缓存需要向量索引；PLAN 规定 `redis:8.x` 不可用时退回 redis-stack-server。
- **决定**：`redis:8.10.2`。S0 核实：官方镜像在 `/usr/local/lib/redis/modules/` 自带 redisearch / rejson / redistimeseries / redisbloom，但**只有当入口脚本收到的第一个参数是 `redis-server` 时才会逐个 `--loadmodule`**。最初用 `sh -c "exec redis-server ..."` 启动，`MODULE LIST` 只有 `vectorset`、`FT._LIST` 报 `ERR unknown command`；改为 `sh -c "exec docker-entrypoint.sh redis-server --requirepass \"$REDIS_PASSWORD\" ..."` 后 `MODULE LIST` 出现 `search 81001`、`ReJSON 81000`、`timeseries 81001`、`bf 81001`、`vectorset 1`，`FT._LIST` 正常返回空列表。其余参数：`--requirepass`（密码经容器环境变量传入，不写进 compose 命令行）、`--appendonly yes`、`--maxmemory 192mb --maxmemory-policy noeviction`（低于 mem_limit 256m）。
- **备选**：`redis/redis-stack-server`（不需要，已确认 8.x 自带）。
- **后果**：以后改 redis 的 `command` 时必须保留经 `docker-entrypoint.sh` 的写法；S10 若 192mb 不够再调整。

## ADR-008 Kafka：`apache/kafka`，KRaft combined 单节点（S11 起）
- **决定**：堆 512m、mem_limit 1g，S11 才加入 compose。宿主机端口 `KAFKA_PORT=9094` 已在 `.env.example` 预留。
- **备选**：Redpanda（内存更省，但与 Java 生态的 Kafka 客户端兼容性和面试价值不如原生 Kafka）。

## ADR-009 Java 技术栈
- **决定**：JDK 17（本机 `D:\tools\jdk-17`）、Spring Boot **3.5.16**、MyBatis-Plus **3.5.17**（`mybatis-plus-spring-boot3-starter`）、Flyway（Boot 管理的版本 + `flyway-mysql`）、springdoc-openapi **2.8.17**；groupId `com.fundagent`。版本为 2026-09-29 Maven Central 上 3.5.x / 2.8.x 线的最新版。
- **备选**：Spring Boot 4.x（已发布，但 PLAN 定为 3.5.x；springdoc 3.x 对应 Boot 4）。
- **后果**：S0 启动日志有 Flyway 警告「MySQL 8.4 is newer than this version of Flyway」，迁移成功；记为技术债，Boot 升级时一并解决。

## ADR-010 Python 技术栈
- **决定**：Python 3.12、FastAPI、LangGraph 1.x、langchain-mcp-adapters、官方 `mcp` SDK、pydantic-settings；`ruff check` + `ruff format`；pytest marker `integration` / `slow` / `live`，CI 只跑 `-m "not integration and not slow and not live"`。
- **后果**：S0 实际安装版本：fastapi 0.141.1、starlette 1.7.0、httpx 0.28.1、pydantic 2.13.5、pydantic-settings 2.15.0、redis 8.1.0、pytest 9.1.1、ruff 0.16.9、uvicorn 0.54.0。`pyproject.toml` 只写下限，S1 起若出现兼容问题再加上限或锁文件。

## ADR-011 PDF 解析：pdfplumber（MIT），正文可用 pypdf 辅助
- **决定**：pdfplumber 提取正文与表格；不用 PyMuPDF（AGPL，与本仓库 MIT 许可冲突）。
- **后果**：只收可直接提取文字的 PDF（PLAN §4.1），扫描件剔除并登记。

## ADR-012 数据来源与版权合规
- **决定**：结构化数据用 AkShare（MIT）；披露 PDF 用东方财富公告 PDF（`pdf.dfcfw.com`，证监会站点从本机命令行不可达，见 PLAN §2.1）；**原始 PDF 与数据快照不提交**，仓库只提交脚本、`MANIFEST` 和自造 fixture；爬取每秒不超过 1 个请求、本地缓存。
- **后果**：`.gitignore` 排除 `data/raw/`、`data/snapshots/`、`*.pdf`；`scripts/security_scan.py` 在 push 前检查（ADR-021）；data-pipeline 有单测钉住这两个 gitignore 规则。

## ADR-013 服务间通信：第一期 HTTP + SSE，第二期 gRPC
- **决定**：Java 侧定义 `AiServiceClient` 接口，S6 实现 `HttpAiServiceClient`，S9 增加 `GrpcAiServiceClient`，可按配置切换；SSE 事件协议以 `docs/API.md` 为唯一事实来源（S5 起）。

## ADR-014 前端、压测、CI 与仓库
- **决定**：前端 Vue3 + Vite + TS（S7）；压测 Locust + mock LLM（S12）；CI 用 GitHub Actions，不下载模型、不调用真实 LLM、不访问外网（依赖安装除外）；公开仓库 `2181385109/fund-research-agent`，MIT。

---

## ADR-015 镜像版本固定与拉取路径（S0）
- **背景**：compose 镜像必须固定具体版本（不用 latest）；WSL 内 Docker 访问不到 Windows 的本机代理（NAT 模式不镜像 localhost 代理）。
- **决定**：`mysql:8.4.11`、`redis:8.10.2`、`elasticsearch:8.19.22`（经 Dockerfile 构建为 `fra/elasticsearch-ik:8.19.22`）、`milvusdb/milvus:v2.5.27`，均为 2026-09-29 Docker Hub 上对应版本线的最新补丁。拉取实测与报错原文：
  - `redis:8.10.2`：默认镜像加速拉取成功，10 秒。
  - `elasticsearch:8.19.22`：默认镜像加速拉取成功，78 秒。
  - `mysql:8.4.11`：默认路径（daemon 先走 docker.1ms.run）约 10 分钟无进度；WSL eth0 实测接收约 109 KB/s；显式 `docker pull docker.m.daocloud.io/library/mysql:8.4.11` 报 `error from registry: unavailable`。
  - `milvusdb/milvus:v2.5.27`：显式 DaoCloud 同样报 `error from registry: unavailable`。
  - Windows 侧测速（同一个 132MB 层，12 秒窗口）：Docker Hub 经本机代理 647 KB/s、docker.1ms.run 直连 389 KB/s、docker.m.daocloud.io 直连 7948 KB/s。
  - 于是 mysql 和 milvus 改为在 Windows 侧用临时脚本按 registry API 下载（每个 blob 先试 DaoCloud，失败回退 Docker Hub 经代理；逐个校验 sha256 与大小），打成 OCI layout 归档后在 WSL 里 `docker load -i`。加载结果：`mysql:8.4.11` 镜像 ID `sha256:f015b98a954d…`（mysql 239MB，188 秒）、`milvusdb/milvus:v2.5.27` 镜像 ID `sha256:dfcfefefbbde…`（896MB，78 秒），与 Docker Hub 的 linux/amd64 清单 digest 一致。
- **备选**：给 WSL 开 mirrored 网络模式以使用本机代理（要改 `.wslconfig` 的网络模式，影响 ticket-qa，未授权）；调整 `/etc/docker/daemon.json` 的镜像顺序（影响 ticket-qa，未做）。
- **后果**：镜像都已在本机；换机器时按 SETUP §4.1 处理。下载脚本是一次性运维操作，没有放进仓库。

## ADR-016 MySQL 运行配置与初始化（S0）
- **背景**：mem_limit 512m；`/mnt/d` 下的文件在 WSL 里权限是 777，MySQL 会忽略 world-writable 的 `conf.d/*.cnf`。
- **决定**：配置全部用 compose `command` 传参：utf8mb4 / `utf8mb4_0900_ai_ci`、时区 +08:00、`innodb-buffer-pool-size=128M`、`max-connections=100`、**`performance-schema=OFF`**（常驻约 100–200MB，本地开发不需要）、`skip-name-resolve`。初始化脚本 `deploy/mysql/init/01-databases-and-users.sh`：建 `fra_app`、`fund_data` 两个库；应用账号 `fra_app` 只有 `fra_app.*` 的 ALL；`fund_reader` 只有 `fund_data.*` 的 SELECT；密码来自容器环境变量（compose 从 `.env` 注入）。healthcheck 用 TCP `mysqladmin ping`：初始化期间临时服务器是 skip-networking，不会过早报 healthy。
- **备选**：挂 `conf.d` 并在 Dockerfile 里 COPY + chmod 644（多一个自建镜像）；用官方 `MYSQL_DATABASE/MYSQL_USER`（只能建一个库一个用户）。
- **后果**：S0 实测常驻 215.6MiB / 512MiB。S1 的数据导入需要一个 `fund_data` 写账号（或用 root），届时新增 ADR。

## ADR-017 Python venv 策略：每个包一个 `.venv`（S0）
- **背景**：三个包依赖差异大：ai-service 在 S2 起要装 torch / sentence-transformers（数 GB），mcp-tools 和 data-pipeline 很轻；S7 每个服务单独做镜像。
- **决定**：`ai-service/.venv`、`mcp-tools/.venv`、`data-pipeline/.venv` 各自独立，`pip install -e ".[dev]"`；CI 也按包矩阵分别安装。`scripts/` 不是包：`security_scan.py`、`gen_env.py` 只用标准库，`llm_smoke.py` 只依赖 httpx，本地用 ai-service 的 venv 运行，CI 单独装 `httpx pytest ruff`。
- **备选**：根目录共用一个 `.venv`（省磁盘，但轻量包的测试也要装上 torch，且掩盖依赖声明遗漏）。
- **后果**：本机多占几百 MB 磁盘；各包依赖声明必须完整，否则 CI 立即暴露。

## ADR-018 默认 LLM 模型：`deepseek-flash`，思考模式默认关闭（S0）
- **背景**：S0 实测 `GET https://api.deepseek.com/models` 返回 `deepseek-flash`、`deepseek-v4-pro` 两个模型。官方文档（api-docs.deepseek.com 定价页与思考模式页，2026-09-29 查阅）：`deepseek-flash` 对应 DeepSeek-V4.1-Flash（旧名 `deepseek-v4-flash` 已退役但仍接受），`deepseek-v4-pro` 对应 DeepSeek-V4-Pro；两者都支持 tool calling、JSON 输出和思考模式；**思考模式默认开启**，开启时多轮 tool call 必须把 `reasoning_content` 完整回传，否则 400。
- **决定**：`LLM_MODEL=deepseek-flash`（便宜、快，支持流式和 tool calling）；新增 `LLM_THINKING=disabled`，请求体带 `"thinking": {"type": "disabled"}`；`JUDGE_MODEL=deepseek-v4-pro`（S8 裁判刻意与被测模型不同源）。
- **备选**：`deepseek-v4-pro` 作为主模型（输入价约 4 倍）；开启思考模式（首 token 更慢，且 LangGraph / langchain 适配层要保证回传 `reasoning_content`，S5 再评估）。
- **后果**：S0 冒烟（`reports/smoke/20260928T163850Z/summary.json`）：请求 `deepseek-flash`、响应 `model` 字段为 `deepseek-flash`；流式首 token 751.8ms、总耗时 1102.8ms；tool call 返回 `get_latest_nav({"share_code": "110022"})`，耗时 1702.8ms。以上是单次测量（n=1），不代表稳定延迟。

## ADR-019 健康检查设计（S0）
- **决定**：
  - backend `GET /api/health`：`DependencyProbe` 接口（mysql：从连接池取连接 + `isValid`；redis：PING），`HealthService` 逐个探测并计时；超时由 Hikari `connection-timeout` 和 Lettuce `timeout/connect-timeout`（均取 `HEALTH_TIMEOUT_SECONDS`，默认 2s）控制；任一 DOWN 返回 503，响应体仍是 `ApiResponse`，`data` 带各依赖明细。
  - ai-service `GET /health`：`HealthChecker` 协议，`asyncio.gather` 并发探测，每项 `asyncio.wait_for(timeout)`；Milvus 调 RESTful v2 `POST /v2/vectordb/collections/list`（与 SDK 同一 19530 端口，比 9091/healthz 更接近实际使用）；ES 查 `_cluster/health`，green/yellow 为 UP（单节点副本分片无法分配，yellow 正常）；Redis PING。本机访问 infra 的 httpx 客户端 `trust_env=False`，避免走本机 HTTP 代理。
- **后果**：backend 在 Redis 恢复后要等 Lettuce 指数退避重连（S0 实测约 15 秒）才回到 UP；S10 引入限流时再评估是否调小重连间隔。

## ADR-020 配置与 `.env`（S0）
- **决定**：`.env.example` 列出全部已知变量（未到阶段的标「Sx 起」）；`scripts/gen_env.py` 生成 `.env`：注释里带 `[secret]` 的空变量随机生成 24 位字母数字串（只含字母数字，避免在 shell / URL / SQL 中转义），`LLM_API_KEY` 从 `--llm-key-file` 读取；只打印变量名。backend 用 `spring.config.import: optional:file:../.env[.properties]` 读同一份 `.env`；Python 包用 pydantic-settings 的 `env_file` 指向仓库根 `.env`，密钥字段用 `SecretStr`。
- **后果**：一份 `.env` 同时服务 compose、Java、Python；容器化（S7）后由 compose 注入环境变量，`.env` 文件导入是 optional 的。

## ADR-021 安全扫描规则（S0）
- **决定**：`scripts/security_scan.py`（仅标准库）：默认扫描 `git ls-files` 的工作区内容，`--history` 扫描 `git rev-list --all --objects` 中的每个 blob。规则：`sk-` 开头的长串、GitHub token、AWS key、私钥头；本地 `.env` 中以 PASSWORD/SECRET/API_KEY/TOKEN 结尾的变量的**实际值**是否出现在仓库内容里（只报变量名）；本机绝对路径；跟踪了 `.env*`（`.env.example` 除外）、`*.pdf`、`data/raw/`、`data/snapshots/`。本机路径规则对以下文件豁免（理由写在脚本 `LOCAL_PATH_EXEMPT` 里）：`CLAUDE.md`、`docs/PLAN.md`、`docs/SETUP.md`、`docs/PROGRESS.md`、`docs/DECISIONS.md`、`docs/prompts/`、扫描器本身及其单测。豁免不覆盖密钥规则。发现问题退出码 1，疑似密钥打码输出。CI 的 scripts job 对仓库跑两种模式。
- **后果**：README、代码、compose 里不能出现本机路径（compose 注释改为指向 SETUP.md）。

## ADR-022 Java 未实现包用 `package-info.java` 占位（S0）
- **背景**：S0 要求未实现模块放占位说明所属阶段。
- **决定**：Python 子目录与顶层目录放 `README.md`；Java 包（auth、kb、document、conversation、chat、ratelimit、ingestbatch、aiclient）用带 Javadoc 的 `package-info.java`，因为 `src/main/java` 下的 README 不会被 Maven 处理，也不符合 Java 惯例。

---

## ADR-023 fund_data 导入账号 `fund_loader`（B1，统筹决定 S0 问题 2）
- **背景**：S1 的 `fund_pipeline.load` 要在 fund_data 建表并导入；S0 时 fund_data 只有 root 和只读的 `fund_reader`。
- **决定**：新增 `fund_loader`，权限为 `fund_data.*` 上的 SELECT、INSERT、UPDATE、DELETE、CREATE、DROP、ALTER、INDEX、REFERENCES，无其他库权限；只给 data-pipeline 导入用，任何代码都不用 root（CLAUDE.md §5）。建号脚本 `deploy/mysql/init/02-fund-loader.sh`（幂等：CREATE IF NOT EXISTS + ALTER USER 同步密码），新数据卷自动执行，已有数据卷按 SETUP §3.1 手动执行一次。密码变量 `FUND_LOADER_PASSWORD` 由 `gen_env.py --add-missing` 追加到已有 `.env`（新增的选项，已有值不动，有单测）。
- **备选**：直接用 root（统筹否决）；给 `fra_app` 账号加 fund_data 权限（混淆业务库与数据库边界）。
- **后果**：B1 实测（`reports/infra/20260928T174514Z/s0_followup.txt`）：`fund_loader` 在 fund_data 上建表 / 插入 / 查询 / 删表成功；查 `fra_app` 报 ERROR 1142，建其他库报 ERROR 1044；`fund_reader` 权限不变。

## ADR-024 ES 内存：mem_limit 1792m + `-XX:MaxDirectMemorySize=256m`（B1，统筹决定 S0 问题 1）
- **背景**：S0 实测 mem_limit 1536m 下 ES 空载常驻 1457MiB（94.9%），堆 1g 启动即预占；ES 默认 direct 上限为堆的一半（512MiB），写入或检索负载下可能被 OOM kill。
- **决定**：`mem_limit: 1792m`，`ES_JAVA_OPTS=-Xms1g -Xmx1g -XX:MaxDirectMemorySize=256m`；PLAN §3 已由统筹同步（infra ≈ 5.5g）。
- **备选**：只调 direct 上限不加内存；维持现状观察。
- **后果**：重建容器后实测 1.426GiB / 1.75GiB（81.5%），heap_max 1073741824，direct 已用约 4MiB（同一证据文件）。S2 全量入库时再看 `docker stats`。

## ADR-025 `LLM_THINKING=disabled`、`JUDGE_MODEL=deepseek-v4-pro` 维持（B1，统筹决定 S0 问题 3）
- **决定**：维持 ADR-018，统筹已写入 CLAUDE.md §5 的变量清单。无代码改动。

## ADR-026 WSL Docker 镜像加速顺序不改、不重启 Docker（B1，统筹决定 S0 问题 4）
- **决定**：`/etc/docker/daemon.json` 保持原样，不 `systemctl restart docker`（会连带重启 ticket-qa 的容器）。新镜像拉取慢时继续按 SETUP §4.1 的办法（Windows 侧下载 + `docker load`）。

---

## ADR-027 AkShare 1.18.97 的两个缺陷：绕开处理，不改 AkShare 源码（S1）
- **背景**：
  1. `fund_fee_em(indicator="申购费率")` 返回空表。读 1.18.97 源码：它按页面 `<h4 class="t">` 的标题建表字典，但分支只认 `"申购费率（前端）"`；东方财富页面（`fundf10.eastmoney.com/jjfl_{code}.html`）现在的标题是 `"申购费率"`，于是任何参数都落到 `else` 分支返回空表（传 `"申购费率（前端）"` 则 KeyError）。2026-09-29 实测 003095、003096 均如此；`"赎回费率"`、`"运作费用"` 正常。
  2. `fund_announcement_personnel_em` 在来源返回 0 条公告时，仍给空表设 8 个列名，抛 `ValueError: Length mismatch: Expected axis has 0 elements`。
- **决定**：
  1. 申购费分档改从最新招募说明书（更新）的费率表解析（pdfplumber），`purchase_fee_tiers.source` 标 doc_id 与页码（用户 2026-09-29 确认）。
  2. `AkShareSource` 把含 `Expected axis has 0 elements` 的 ValueError 视为空结果（有单测）。
  3. 同时给 `AkShareSource` 加有限重试：只对网络类临时错误（`requests.RequestException`、`OSError`，含来源偶发返回非 JSON）重试 3 次，退避 5s/10s/15s；逻辑错误立即抛出（有单测）。
  4. 不修改、不 monkeypatch AkShare 源码；版本固定 1.18.97（`pyproject.toml`）。登记到 `docs/LIMITATIONS.md`。
- **备选**：直接解析东方财富费率页面（同一来源，但绕过了 AkShare；且页面上是销售平台展示值）；给 AkShare 打补丁（用户否决）。
- **后果**：申购费依赖招募说明书的表格版式，解析失败的份额登记为缺口，不静默补值。

## ADR-028 公告列表直接调用东方财富 JJGG 接口（S1）
- **背景**：PLAN §2.1 的报告 ID 来自 AkShare `fund_announcement_report_em`，它调用的是 `api.fund.eastmoney.com/f10/JJGG?type=3`（定期报告）。招募说明书和基金合同在同一接口的 `type=1`（发行运作），AkShare 没有封装。
- **决定**：`fund_pipeline.docs.EastmoneyAnnouncements` 直接调 JJGG 的 type=1 和 type=3，带 `Referer` 与如实的 User-Agent（`fund_pipeline.net.USER_AGENT`，AkShare 用的是伪装的浏览器 UA）；节流 ≤1 次/秒、公告列表按 as_of 缓存。PDF 仍从 `pdf.dfcfw.com/pdf/H2_{ID}_1.pdf` 下载。数据源与 PLAN 一致，只是换了调用方式。
- **后果**：接口字段名（`TITLE`、`PUBLISHDATEDesc`、`ID`）由我们自己解析，接口变动时 docs 模块直接报错。

## ADR-029 fund_data 表结构细化（S1）
- **决定**（`data-pipeline/src/fund_pipeline/schema.sql`）：
  - 比率一律存小数（DECIMAL，`0.012` = 1.20%），金额单位元、持股单位股（来源的万元/万股在导入前换算），日期 DATE。
  - 在 PLAN §4.2 的基础上：`purchase_fee_tiers`/`redemption_fee_tiers` 增加 `tier_text`（档位原文）；`purchase_fee_tiers` 增加 `fixed_fee`（每笔固定费用）和 `source_page`；赎回期限「年」按 365 天、「月」按 30 天折算，原文保留在 `tier_text`。
  - `fund_manager_tenures` 来自定期报告的「基金经理（或基金经理小组）简介」表；`managers` 只收本基金池涉及的现任经理。
  - `period_returns` 来自 `fund_open_fund_rank_em`，只有当它的「日期」等于 DATA_AS_OF 时才入库，否则登记问题。
  - 每张表都有 `source`、`as_of`；表和列带中文 COMMENT，S5 的 `get_fund_db_schema` 直接读。
  - 导入：`fund_loader` 账号（ADR-023）整库 DROP + CREATE + INSERT，幂等；导入后用 `fund_reader` 逐表 COUNT(*) 核对。

## ADR-030 基金池筛选规则与用户决定（S1）
- **筛选规则**（`fund_pipeline.universe`）：类型白名单 股票型 / 混合型-偏股 / 混合型-灵活 / 指数型-股票；名称排除 ETF、联接、LOF、持有期、定开、封闭、QDII、港股通/沪港深/全球/海外（跨境持仓会让持仓和净值口径变复杂）；主题按简称关键词，两类都命中的剔除；成立日 ≤ as_of − 2 年、最新净资产 ≥ 2 亿均取自 `fund_overview_em`。474 组 → 193 组通过（计数见 `reports/universe/20260928T181640Z/summary.json`）。
- **「近两年换过经理」的口径**：2024-09-30～2026-09-30 之间，基金经理变更公告 PDF 的「基金经理变更类型」含「解聘」（即有经理离任）；只增聘不算。20 只中 8 只满足。
- **用户决定（2026-09-29）**：
  1. LOF 可以收：160219、161035 可场外申赎、净值口径与普通开放式一致，数据一律用场外 A/C 份额；PLAN §4.1 已加备注。（名称里带「LOF」的基金在自动筛选时被排除，这两只的简称不含「LOF」，是人工取舍时发现并经用户确认的。）
  2. 011832 保留：规模 2.06 亿（截至 2026-06-30）满足门槛，三次换经理适合出题。
  3. 其余 19 只不调换，universe v1 冻结（`data/CHANGELOG.md`）。
- **后果**：候选名单里存在后端收费份额（名称带「(后端)」）被当成独立一组的情况，不影响最终 20 只，但筛选计数里 474/193 组含这类重复组。
