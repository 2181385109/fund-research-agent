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
- **补充（冻结后发现）**：`fund_overview_em` 的「净资产规模」是主代码（A 类）份额的规模，不是全部份额合计；universe.yaml 注释的说法不准，数值和入选结果不变，已登记 `data/CHANGELOG.md`，待统筹决定是否更正注释。

## ADR-031 PDF 数据的解析策略（S1）
- **背景**：申购费、经理任职、季报规模、前十持仓（质量比对用）都要从 20 家公司各自排版的 PDF 里取，版式差异大。
- **决定**（`fund_pipeline/pdf_extract.py`、`quality.py`）：pdfplumber 抽表格为主、正文正则为退路；每一种版式都有单测（自造 fixture）：
  - 申购费：独立费率表 / A、C 合表 / 养老金与其他投资者两列并列 / 无表格线的正文。养老金优惠表的判定只看表格正上方引导语的最后一句（「非养老金客户」「除养老金客户以外的其他投资者」算一般费率）。
  - 经理简介表：表头跨 2–3 行、合并单元格空列、续表跨页（最多 3 页）、行被分页拆开时按列拼回、「基金经理助理」行排除。任职记录 = 年报 → Q1 → Q2 → 变更公告，后出现的非空值覆盖。
  - 前十持仓：标题在页末时取下一页；表头跨 3 行；序号不足 10 时接下一页；港股代码补齐 5 位；PDF 对同一发行人的 A 股和 H 股共用一个序号，所以比对按证券代码而不是按排名。
- **后果**：换一批基金可能出现新版式；解析不出时登记缺口，不猜测（S1 剩 4 份年报的经理表未解析，见 PROGRESS）。

## ADR-032 入库设计：规范文本、双向量、表格独立成块（S2）
- **决定**：
  - **规范文本**：解析出的正文段落、标题行、表格 markdown 用 `\n` 连成文档的规范文本；每个 chunk 的 `text` 恰好是 `canonical[char_start:char_end]`（单测钉住）。表格按行拆分时，拆出来的各块在规范文本里是连续的；续表的表头只补在 `text_ctx`（embedding/BM25 用）里，不改变 `text`。
  - **页眉页脚**：统计每页首尾各 2 行（数字归一为 `#`），在 ≥3 页且 ≥30% 页上重复出现的删除，只删首尾位置。
  - **章节识别**：`§N`、`第X部分` 为 1 级；`N.N`、`一、` 为 2 级；`N.N.N`、`（一）` 为 3 级；≤40 字、不以句号结尾、不是目录行（点引导线）。
  - **切块参数**：`CHUNK_SIZE=600`、`CHUNK_OVERLAP=60`（只在超长段落硬切时重叠）、`TABLE_MAX_CHARS=3000`，都走 env。
  - **上下文头开关**：Milvus 同时存 `embedding`（正文）与 `embedding_ctx`（上下文头 + 正文）两个向量字段，ES 同时有 `text` 与 `text_ctx`。PLAN 要求上下文头「由开关控制，在 S4 的 dev 集上裁决」，两份都存，S4 切开关时不用重新入库；代价是 embedding 计算量翻倍。
  - **Milvus**：HNSW（M=16，efConstruction=200）+ IP（向量已归一化），`doc_id`、`fund_code`、`doc_type` 建 INVERTED 标量索引，计数用 Strong 一致性。**ES**：`text`/`text_ctx` 写入 `ik_max_word`、检索 `ik_smart`，元数据 keyword，副本 0。
  - **一致性**：先算完全部向量再删旧写新；写入后按 doc_id 两边计数，不等于切块数就报错。
  - **接口安全**：`/v1/documents/ingest` 的文件路径只允许落在 `DATA_DIR` 下。
- **备选**：只存一种向量、S4 按需重建（重新入库一次约 20 分钟，且两次入库的切块可能因代码变化不一致）；表格按行切成小块（会丢失整表上下文）。
- **后果**：bge-small-zh 最大输入 512 token，超过的表格块（最长 3000 字）只有前约 500 字参与向量；BM25 不受影响。S4 要注意表格块的向量召回偏弱。

## ADR-033 sentence-transformers 放在可选依赖 `[model]`（S2）
- **背景**：Linux 上 `pip install sentence-transformers` 默认带 CUDA 版 torch（数 GB）；CI 不下载模型、测试只用 FakeEmbedder。
- **决定**：ai-service 的 `pyproject.toml` 把 `sentence-transformers` 放进 `[model]` extra，CI 只装 `.[dev]`；`BgeEmbedder` 在第一次用时才 import。本机：`pip install -e ".[dev,model]"`（Windows 上 PyPI 的 torch 就是 CPU 版，实装 torch 2.14.0+cpu、sentence-transformers 6.1.0）。S7 做镜像时用 CPU 版 torch 的 index。
- **后果**：模型首次加载要从 hf-mirror 下载约 95MB 到 `MODEL_CACHE_DIR`（`.cache/` 已 gitignore）。

## ADR-034 评测工具独立成包 `eval/`（S3）
- **背景**：红线 3 要求标准答案来自独立参考脚本；校验器还要读 PDF、连 fund_data。若放进 ai-service，参考脚本和被测代码会共享依赖与实现（例如 PDF 解析器），独立性说不清。
- **决定**：`eval/pyproject.toml`（包名 `reference`，本地 `eval/.venv`），依赖只有 pandas、pdfplumber、pymysql、pyyaml、pydantic；不 import ai-service / mcp-tools 的任何代码。CI 矩阵加一个 `eval` 包：ruff + pytest，pytest 里包含对仓库内评测集的离线校验（schema、题数、分层、基金 / 文档引用）。PDF 逐字校验和 gold_sql 校验需要本机数据，在本机运行并把结果写进 `reports/dataset_validation/`。
- **备选**：放在 ai-service 的 `fund_ai.eval`（独立性弱）；做成 `scripts/` 下的单文件（测试和 CI 不好组织）。
- **后果**：多一个 venv。S4 的检索评测 runner 仍按 PLAN 放在 `fund_ai.eval`（它要调用被测检索服务）；证据命中规则两边各实现一份，用共享测试向量 `eval/reference/evidence_cases.json` 钉住一致性。

## ADR-035 评测集的 quote 口径与出题方式（S3）
- **quote 比对口径**：去掉全部空白和表格竖线 `|` 后逐字比较（`reference.common.norm`）。这样 PDF 换行、pdfplumber 表格单元格之间的空格、ai-service 规范文本里的 markdown 竖线都不影响比对；quote 本身按 PDF 原文片段保存（保留空格，方便人工对照）。校验用 pdfplumber `extract_text` 直接提取的页面文本，不经过 ai-service 的解析器；页码是 PDF 物理页。
- **命中规则的实现细节**：「50% 连续片段」取归一化后的最长公共子串，阈值 ⌈0.5×len⌉，并要求 doc_id 相同；为避免偶然重合，quote 归一化后至少 10 个字。
- **出题**：费率、持仓、经理、SQL、收益计算、最新净值、综合题的数据库部分用模板生成，gold 由参考脚本从快照计算（`provenance=template_reference_script`）；合同条款、业绩、季报观点、跨文档、不可回答、荐基请求、纯文档、通用题由执行者阅读原文手写（`llm_draft`），数值必须逐字出现在 quote 里。所有 quote 在构建时逐字定位页码，定位不到就报错。
- **冻结前的可达性诊断**：从 ES 导出入库 chunk 的文本（只读，不做检索），检查每条 quote 能否按命中规则被某个 chunk 命中。这一步只用来发现「quote 格式导致永远无法命中」的问题（例如跨越 markdown 表格的 `|---|` 分隔行），不依据检索结果改题。
- **没有采用的做法**：让 DeepSeek 批量起草题目（需要额外费用，而且起草的问题仍要逐条对原文核对，并不省事）；quote 绑定 chunk_id（PLAN §0 要求证据不绑定 chunk，以便更换切块方式后仍可复用）。
- **已知局限**：部分季报的经理表、持仓表在 pdfplumber 文本里单元格串行（例如「海光信 / 息」「2021 / 年07 / 月01 / 日」），这些基金没有出对应的模板题（经理题 11 道只来自 6 只基金），覆盖不均衡在 PROGRESS 中说明。

## ADR-036 收益口径：遇非交易日取前一「交易日」净值，周末季末净值不算交易日（S3 抽检后，用户决定）
- **背景**：PLAN S5 写的是「遇到非交易日就近取前一个净值日」。快照中有 184 条落在周末的净值，都是季末或年末披露的（如 2023-12-31 周日）。对于这些日期，「前一个净值日」和「前一交易日」会得出不同的结果。用户在 S3 抽检意见中明确规定：「遇非交易日取前一交易日净值」，年化 = (1+区间收益)^(365/自然日天数)−1，并要求全数据集统一。
- **决定**：交易日 = 周一至周五且有净值的日子。周末披露的净值不算交易日，也不进入计算用的净值序列（`reference.gold.nav_series`、`is_trading_day`）。口径写进 `eval/datasets/SCHEMA.md`「口径」一节；S5 的 `calc_fund_return` 必须照此实现，并与参考实现逐位比对。
- **影响**：只有 agent-0027 实际使用的起止日变了（2022-12-31→2022-12-30，2023-12-31→2023-12-29）；标准答案 -6.18% 不变，因为周末净值与前一交易日相同。
- **备选**：保留「前一净值日」（与用户的规定不一致）。

## ADR-037 切块最小块长：短块合并（S2 缺陷修复，B3）
- **背景**：S4 dev run 1 的逐题分析发现 S2 切块碎片化：18353 个 chunk 中不足 50 字的 2406 个（13.1%）、不足 100 字的 5110 个（27.8%），中位数 221 字（统计：`data/raw/_chunks.jsonl` 按 text 长度）。典型例子是只有 12 字的「2、中银创新医疗混合C：」、封面块。开上下文头后，这类块的向量几乎完全由「【基金名｜文档名｜章节】」决定，挤占向量召回前列。统筹决定这是 S2 的缺陷、不算调参。另外发现：标题后面紧跟一个超长段落时，标题会落在任何 chunk 之外。
- **决定**：
  - 新参数 `CHUNK_MIN_CHARS`（默认 150，`ChunkParams.min_chars`，0 表示不合并），在 `chunk_document` 末尾做一次合并：相邻两块（规范文本中相邻）只要有一块的 text 不足 `min_chars`，且合并后跨度 ≤ `chunk_size + min_chars`，就合并成一块。表格块不参与。章节路径取合并前较长的一块（等长取后者），`text_ctx` 的上下文头按它重算。
  - 合并块的 `char_start/char_end` 取并集，`text == canonical[char_start:char_end]` 仍成立（单测钉住，ADR-032）。
  - 标题后接超长段落时，标题并入第一片，不再丢在块外。
  - 合并后 chunk_id 按文档内顺序重新编号。
- **备选**：只改上下文头（对短块不加头）——治标，且会让开关对比失真；提高 `CHUNK_SIZE`——会让长块更长，同时不解决短块。
- **后果**：文本块上限从 `chunk_size` 变为 `chunk_size + min_chars`（750）。评测集证据不绑定 chunk_id，不受影响；重新导出 chunk 后重新校验 152 条引文的可达性（见 PROGRESS）。修复前的 dev run 1–2 保留在 tuning_log，标为「切块修复前」，不参与最终配置决策。

## ADR-038 实体过滤维持关闭（S4 收尾，统筹决定，B4 登记）
- **背景**：dev（n=30）上实体过滤「关」比「开」好（hybrid_rerank nDCG@10 +0.067，只有 3 题不同）；test（n=72）上消融方向相反：关 0.5321 → 开 0.5543，差 +0.0222，配对 bootstrap 95% CI [−0.0097, +0.0652]，含 0，72 题中 nDCG 不同的只有 5 题（`reports/retrieval/ablation_entity_filter_20260929/`）。
- **决定**：`RETRIEVAL_ENTITY_FILTER` 默认保持「关」。理由：① 差异不显著；② 红线 2 与 S4 协议——test 只做最终报告，不能根据 test 结果改配置，这条同样适用于「因为 test 上开更好而改成开」。
- **与 S5 的关系**：S5 的 `search_fund_documents(query, fund_codes?, doc_types?, top_n)` 里，`fund_codes` 由 Agent 从问题里理解后**显式传入**，属于调用方指定的过滤条件，与这个「从问题文本自动识别实体并过滤」的开关相互独立；开关关闭不影响 Agent 显式传 `fund_codes`。
- **备选**：改为「开」（依据 test 的 +0.022，违反 test 不回头调参的协议）；需要新的 dev 证据才能重新讨论。
- **后果**：写入 `docs/LIMITATIONS.md` 的 S4 一节。

## ADR-039 持仓表格的入库方式不改，持仓题走 SQL 主路径（S4 收尾，统筹决定，B4 登记）
- **背景**：hybrid_rerank 在 test 持仓题上 nDCG@10 只有 0.126（holdings 11 题），失败题的前几名多是同一基金其他文档或报告期里的持仓表碎片（PROGRESS S4「分析」）。曾提出的改进（合并同页相邻表格碎片、给表格块加自然语言标题行）会改变入库，使已用过一次的 test 检索评测需要重评。
- **决定**：**不改**持仓表格的入库方式。持仓类问题的主路径是 Agent 调用 `run_fund_sql` 查 `holdings_top10` 表（结构化、有 source 与 as_of），文档检索只作补充；检索在持仓题上偏弱作为已知局限记录。
- **同时如实记录**：「纯 RRF 混合检索不如单独 BM25」——test 上 hybrid（RRF）nDCG@10 0.3633，低于 bm25 的 0.3854（n=72；两者的差没有做配对 CI，只是点估计的观察）。原因解释（弱向量路拖累等权 RRF）是从分模式数字读出的，未做单独消融验证。这不影响最终配置：最终配置 hybrid_rerank（0.5321）在重排之后大幅领先。
- **备选**：合并表格碎片 + 加标题行（需要重新入库与 test 重评，不在 B3/B4 授权内；若以后要做，走 PLAN S13 的质量回归规则并经用户同意）。
- **后果**：S5 的 Agent prompt 与工具描述需要让持仓类问题优先走 SQL（B5 落实）；S8 回答评测中持仓题的工具选择准确率与 SQL 正确率会被单独关注。写入 `docs/LIMITATIONS.md` 的 S4 一节。

## ADR-040 mcp-tools 的实现选型（S5 前半，B4）
- **MCP SDK 固定在 1.x**：`mcp>=1.9,<2`（实装 1.30.0）。`pip install mcp` 默认装到 2.x，它把 `FastMCP` 改名为 `MCPServer` 并改了 API；PLAN 写的是 FastMCP，而 B5 要用的客户端 `langchain-mcp-adapters` 是否兼容 2.x 没有验证，先固定在 1.x 避免不确定性；B5 的 ai-service 也用同一大版本。
- **SQL 守卫执行「重新生成的 SQL」**：`sqlglot.parse(read="mysql")` 后从语法树 `.sql(comments=False)` 生成要执行的语句，不执行用户原文。这样注释（包括 MySQL 的 `/*! */` 可执行注释）不可能夹带内容。另外拒绝：多语句、非 SELECT 根节点、DML/DDL/SHOW/SET 等节点、INTO / FOR UPDATE、会话变量（`@x`、`@@x`）、优化器提示（`/*+ MAX_EXECUTION_TIME(...) */` 不是普通注释，sqlglot 会保留它，它能覆盖服务端超时；`SET_VAR` 能改会话变量）、非 `fund_data` 库的表、黑名单函数（LOAD_FILE、SLEEP、BENCHMARK、GET_LOCK，以及泄露账号 / 库版本的 USER、DATABASE、VERSION 等）。行数上限用 AST 改写 LIMIT：没写或超过上限的改成 `LIMIT 201`（多取 1 行判断是否截断，返回时切回 200 行并标 `truncated`），用户自己写的更小的 LIMIT 保持不变、不算截断。不用「外面套一层子查询」的做法——多表 join 时重复列名会让 MySQL 报错。
- **数据库侧的保险**：`fund_reader` 只有 SELECT 权限；每个连接建立后 `SET SESSION TRANSACTION READ ONLY` 与 `MAX_EXECUTION_TIME=5000`，并设置客户端读超时兜底。每次查询开短连接，不做连接池（调用量小）。集成测试（`-m integration`）证明绕过守卫直接发 INSERT / DELETE / UPDATE / DROP / CREATE 都被数据库拒绝，慢查询被服务端超时中断。
- **`calc_fund_return` 与参考实现逐位一致**：纯 Python 浮点，运算顺序与 `eval/reference/gold.py` 相同（从序列起点累乘复权因子，再取窗口两端之比）；两边都不用 Decimal，因为逐位比对的对象是同一套 IEEE 双精度运算。数据经 `ReturnData` 协议注入（生产读 MySQL，单测用内存序列）。除息日没有对应交易日净值时不静默丢弃，写进 `notes` 报告（当前快照中 14 条分红全部对得上）。
- **工具返回 `display` 字段**：`return` / `max_drawdown` 等保持小数原值（CLAUDE.md §4 金融数值约定），另附已换算好的百分数字符串，减少 LLM 把 0.0386 当成 0.0386% 的风险。Decimal 列在 `run_fund_sql` 里以字符串返回，不丢精度。
- **`get_fund_db_schema` 的字段说明来自库里的 COMMENT**，字段写成一行文字而不是嵌套 JSON（完整 JSON 约 26 KB，压缩后约 8.6 KB），避免每次调用占掉大量 Agent 上下文。
- **`get_latest_nav`**：份额不在基金池内直接报错（不去请求外部接口）；回退结果不缓存，接口恢复后下一次调用立即拿到实时数据；User-Agent 如实填写项目名。
- **工具错误用 MCP 的 `isError` 回传**（FastMCP 会加前缀 `Error executing tool <name>:`），不用 200 + 错误字段，Agent 侧的重试逻辑（B5）可统一处理。
- **备选**：用 2.x 的 `MCPServer`（与 B5 客户端不兼容风险）；用正则做 SQL 守卫（容易被注释和大小写绕过）。

## ADR-041 langchain-mcp-adapters 固定 0.3.2，与 mcp 1.x 搭配（S5 后半，B5）
- **背景**：ADR-040 把 `mcp` 固定在 1.x，并把「`langchain-mcp-adapters` 与 mcp 2.x 是否兼容」留给 B5。PLAN 要求 Agent 用 langchain-mcp-adapters 做多服务器 MCP 客户端。
- **决定**：`langchain-mcp-adapters==0.3.2`（当前最新；元数据 `mcp>=1.24,<2`、`langchain-core>=1.3.3`）+ `mcp>=1.24,<2`（实装 1.30.0）+ `langgraph>=1.2,<2`（1.2.12）+ `langchain-openai>=1.6,<2`（1.6.6）。0.3.2 自己就要求 `mcp<2`，所以「adapters 与 mcp 2.x 是否兼容」不再是问题：ai-service 与 mcp-tools 都停在 mcp 1.x。
- **验证**（写 LangGraph 之前先做的最小例子）：一个 `MultiServerMCPClient` 同时配 `fund_tools`（:8101/mcp）与 `fund_docs`（:8001/mcp），`get_tools()` 得到五个工具；分别调用 `run_fund_sql`、`search_fund_documents` 成功；调用被守卫拒绝的 `DROP TABLE` 时行为见下。固化为 `ai-service/tests/test_agent_integration.py`（`-m integration`）。
- **踩到的行为**：默认 `handle_tool_errors=True` 时，`isError=true` 的结果被包成**普通文本**（`Error executing tool …`）返回，调用方无法区分「工具报错」与「正常结果」。所以 `McpToolBackend` 用 `handle_tool_errors=False`，此时适配器抛 `ToolException`，据此可靠地得到 `ToolOutcome(ok=False)`。另外：适配器每次工具调用新建一个 MCP 会话；`structuredContent` 在我们的工具上为空，结果从文本 JSON 解析；httpx 客户端必须 `trust_env=False`（本机服务不能走系统代理，经 `httpx_client_factory` 传入）。
- **备选**：直接用 `mcp.ClientSession` 自己写多服务器路由（少一层依赖，但 PLAN 指定了 adapters，且它的工具 schema 转换够用）；`mcp` 升 2.x（要改 mcp-tools 的服务端 API，收益为零）。
- **后果**：ai-service 依赖里新增 langgraph / langchain-openai / langchain-mcp-adapters / mcp；升级任何一个之前要重跑集成测试。CI 只装 `.[dev]`，不需要 torch，装这几个包不影响 CI 时长量级。

## ADR-042 Agent（LangGraph）的设计取舍（S5 后半，B5）
- **图与循环上限**：`agent ⇄ tools` 两个节点。`max_steps`（默认 6）数的是**工具轮数**（一轮里可以并行多个 tool call）；满 6 轮后下一次 `agent` 调用**不再绑定工具**并附一条「不能再调用工具」的提示，强制作答，所以图一定终止（若模型仍返回 tool call 就丢弃）。`recursion_limit` 按 `2*(max_steps+1)+4` 设置，只作兜底。
- **SQL 重试**：`run_fund_sql` 连续失败计数（成功清零）；失败信息原文回传并提示「还可重试 n 次」；连续失败 3 次（首次 + 2 次重试）后，第 4 次调用**不再执行**，直接回「已达重试上限，请如实告知查不到」。其他工具的报错原文回传，不设单独上限（受 max_steps 约束）。
- **工具后端接口**：图只依赖 `ToolBackend`（`specs()` / `call()`）。真实实现 `McpToolBackend`（ADR-041），测试用 `FakeToolBackend`；LLM 侧 `FakeChatModel` 按脚本逐轮「回答」，都放在 `fund_ai/agent/fake.py`（包内，S12 压测的 mock 也可复用）。每次请求的可变状态（出处登记、计时、用量、SQL 失败计数）在 `RunContext` 里，经 `config["configurable"]["ctx"]` 传入；图状态只有 messages。事件经 LangGraph 的 `get_stream_writer` 发出。
- **出处编号**：一次请求内全局编号；文档片段一个片段一个编号（同一切块重复命中沿用旧编号），SQL / 计算 / 净值各一次调用一个编号，schema 不编号。编号写进给 LLM 的工具结果文本（`[3] …`）。MCP 工具 `search_fund_documents` 返回的 `ref` 只是单次调用内的序号，全局编号在 Agent 侧分配——否则两次检索会撞号。
- **非法 `[n]`**：不是事后清洗，而是**流式过滤**（`CitationStreamFilter`，遇到 `[` 先缓冲，判定后再放行），所以用户永远看不到无效编号；`[1, 2]` 规整成 `[1][2]`。`citations` 事件只列回答里实际引用的编号。
- **只支持 `LLM_THINKING=disabled`**：langchain-openai 不保留 `reasoning_content`，开启思考后多轮 tool call 会 400（ADR-018 已预告）。配置成别的值时构造模型直接报错，不静默降级。要支持思考模式需要自己写回传逻辑，留给有需要时再做。
- **tool call 参数严格解析**：langchain 的 `tool_calls` 用宽松 partial-JSON 解析，被截断的参数会被「补全」成一条看似合法的调用（对 `run_fund_sql` 就是执行半条 SQL）。改为对累积后的原始参数串 `json.loads`，失败的当作 invalid tool call 回传给 LLM 重调，不执行。
- **开场白扣留（`_TurnGate`）**：DeepSeek 在 tool call 之前常先说一句（第一次 live 冒烟 13 题里有 11 题出现，其中有英文的「I'll look up …」），它会混进答案里。做法：每轮先扣留前 `AGENT_PREAMBLE_HOLDBACK_CHARS`（默认 160）个字符，本轮以 tool call 结束就丢弃，否则放行后实时流出。代价是最终回答最前面 160 个字符晚到；超过 160 字的开场白仍会漏出，`done` 里如实记 `preamble_dropped_chars` / `preamble_leaked_chars`。同时在 system prompt 里要求「工具轮只调用工具、不输出文字」。默认值的来由：第二次冒烟用 100，13 题里有 2 题（mixed-1、mixed-2）的开场白是 118 / 127 字的英文而漏出，第三次改 160 后 13 题都被吞掉、无漏出——这个值是看着冒烟样本调的（n=13，不是评测集），换一个模型或 prompt 后要重看 `preamble_leaked_chars`。备选：整轮缓冲后再决定（最终回答无法流式，违背 PLAN 的流式要求）；不处理（答案里混入开场白）。
- **风险提示**：`disclaimer` 事件在任何路径上（含 Agent 构造失败、LLM 报错、工具不可用）都紧挨在 `done` 之前发出，文案固定在 `compliance.DISCLAIMER`。它不进 `token` 流，也不由 LLM 生成。
- **输出守卫**：关键词启发式（`compliance.scan_output`），只标记 + 记日志，不改写（PLAN S5 第一期要求）。命中词之前同一句内有否定或疑问词（不 / 无法 / 是否 / 该不该……）时不算，否则拒答话术（「无法建议买入」「判断是否适合加仓」）会被误报——第一次冒烟就出现过一例误报，据此加了疑问词。它会漏报，评测集里的 advice_request 题才是拒答的真正检验（S8）。
- **基金池清单与表清单写进 system prompt**：启动时从 fund_data 读 20 只基金（主代码、简称、主题、份额代码）和 12 张表（表名 + 表 COMMENT）。原因：ADR-038 要求 `fund_codes` 由 Agent 显式传入，而用户多半只写简称；第一次冒烟里模型猜了不存在的表名 `fund_basic_info`；补上表清单后并没有杜绝（第三次冒烟的 tool-sql-1 又猜错一次），但错误原文回传后下一轮即恢复，这正是 SQL 重试机制的用途。`get_fund_db_schema`（约 8.6 KB）只在列名不确定时调用。
- **文档 MCP 挂载**：`FastMCP.streamable_http_app()` 挂在 FastAPI 根路径最后（其余路由先匹配，剩下的 `/mcp` 交给它），session manager 在 FastAPI lifespan 里运行。Host 头白名单 `MCP_ALLOWED_HOSTS`（MCP SDK 的 DNS rebinding 防护，默认只放回环）；S7 容器互连时要加 `ai-service:*`。Agent 用 HTTP 回环调用自己的 `/mcp`（与 PLAN §1 的「MCP Client」一致，也让文档检索工具能被外部 MCP 客户端直接使用）。
- **历史消息**：请求可带最近几轮 `history`；其中助手回答里的 `[n]` 去掉，避免被当成本次请求的出处。
- **备选**：Agent 用 `create_react_agent` 预置图（少写代码，但循环上限、SQL 重试、出处登记都要靠钩子，不如显式 StateGraph 清楚）；答案事后整体校验 `[n]`（用户已经看到了无效编号）。

## ADR-043 私有知识库提问：检索范围只由服务端注入（S6，B6，统筹决定）
- **背景**：S6 验收 3 要求「上传私有 PDF → READY → 对私有库提问，回答引用这份私有文档」，但 S5 的 `search_fund_documents` 没有 kb 维度（HANDOFF B5 §2）。
- **决定（统筹 2026-09-29 的四条，原样执行）**：
  1. chat 增加检索范围，**只能由服务端注入**：Java 按当前用户算出允许访问的 kb 集合（公共库 + 该用户自己的私有库），放进发给 ai-service 的请求（`kb_scope`）；ai-service 把它放进 Agent 的 `RunContext`，`search_fund_documents` 在服务端强制过滤。范围**不是 LLM 可填的工具参数，也不出现在工具 schema 里**。
  2. 私有文档单独一套 Milvus 集合 + ES 索引 `user_chunks`（带 `kb_id`、`owner_id` 字段）；公共 `fund_chunks` 不改、不重新入库，S4 评测结果保持有效。范围里有私有库时，公共与私有两边的检索结果合并后再重排。
  3. 越权测试：用户 A 在 chat 请求里手动带上用户 B 的 kb_id，必须被拒或被剔除，B 的文档内容不出现在检索结果里。Java 层和 ai-service 层各测一次。
  4. S6 验收 3 按原文：私有 PDF 入库到 READY 之后，对私有库提问，回答里引用这份私有文档。
- **执行细节（本 ADR 补充，均在上述四条之内）**：
  - **范围怎么从 Agent 传到检索工具**：文档 MCP 仍是一个独立的 MCP 服务（外部 MCP 客户端可直接用），所以范围走**带签名的 HTTP 头** `X-Fund-Kb-Scope`（`base64(json).hmac_sha256`，含 `exp`，60 s 有效），由 `McpToolBackend` 在每次调用 `search_fund_documents` 时按 `RunContext.scope` 签发，文档 MCP 的工具函数通过 MCP `Context`（隐藏参数，不进工具 schema）取到请求头并校验。LLM 只能决定 `query/fund_codes/doc_types/top_n`；它即使在 tool call 里写了 `kb_ids`、`owner_id` 之类的额外参数，也不会被采用（测试覆盖）。没有该头（外部 MCP 客户端、`/v1/retrieve`、评测）= **只查公共库**（默认拒绝私有）；签名或有效期不对 = 工具报错，**不回退成公共库**。密钥 `KB_SCOPE_SECRET`，未配置时进程启动时随机生成（单 worker 部署；多 worker 必须配置同一个值）。
  - **双重条件**：私有检索的过滤条件是 `kb_id in (范围内的私有 kb) AND owner_id == 请求里的用户`。即使上游误传了别人的 kb_id，owner 不匹配也查不到。
  - **默认范围**：chat 请求没有 `kbIds` = 公共库 + 该用户全部私有库；带了 `kbIds` 则必须都在「允许集合」里，**任何一个不在就整个请求拒绝（HTTP 403，不调用 ai-service）**，不做静默剔除，也不区分「不存在」和「是别人的」，避免探测他人知识库 id。只选私有库时 `include_public=false`。
  - **合并后重排**：公共与私有两边各自完成召回与 RRF，各取前 `rerank_candidates` 条**合并成一个候选池**再送交叉编码器重排；不带重排的模式按各自的分数合并（BM25 分数在两个索引之间不可严格比较，局限登记在 LIMITATIONS）。没有 `kb_scope` 时代码路径与 S4 完全一致，S4 已用掉的 test 评测不受影响；上线前后对同一批公共查询的检索结果做了对比，证据见 PROGRESS。
  - **入库**：`POST /v1/documents/ingest` 增加 `kb_id`、`owner_id`（有 `kb_id` 走私有流水线写 `user_chunks`）、`callback`（异步：立即返回 202，入库后带共享密钥回调 Java；回调地址不由请求给出，固定为配置项，防 SSRF）；私有块额外存 `doc_title`（用户文件名），因为公共块的文档名是靠 `doc_type + report_period` 重建的。ai-service 增加 `.md/.txt` 解析（PLAN S6 允许上传 PDF/MD/TXT）。
  - **Java 鉴权不用 Spring Security**：BCrypt 用 `spring-security-crypto`，JWT 用 jjwt，由 `HandlerInterceptor` 校验并把 userId 放进请求属性（无角色、无复杂权限，PLAN §0「不做多租户与复杂权限」；不引入整套 Security 过滤链，`@WebMvcTest` 也更简单）。
- **备选**：把 `kb_ids` 做成工具参数并靠 prompt 约束（LLM 可被注入绕过，否决）；私有块写进公共 `fund_chunks`（要改 schema、重入库并使 S4 结论失效，否决）；范围放进 MCP `_meta`（langchain-mcp-adapters 0.3.2 没有按调用传 `_meta` 的入口，头 + contextvar 更直接）。
- **后果**：公共 MCP 工具签名未变；ai-service 多一个 Milvus 集合和一个 ES 索引；`search_fund_documents` 的结果里私有片段 `fund_code/fund_name` 为空、`doc_title` 是文件名、`kb_id` 有值。

## ADR-044 Java 后端的实现取舍（S6，B6）
- **背景**：S6 要 auth / kb / document / conversation / chat 与 `AiServiceClient`；ADR-043 已定私有库的检索范围方案，这里记 Java 侧的实现选择。
- **持久化与状态机**：MyBatis-Plus 的 `BaseMapper` + 少量注解 SQL。文档状态迁移是**条件更新**（`UPDATE … SET status=to WHERE id=? AND status=from`，返回行数），所以重复回调、迟到的回调、与超时清理并发都不会把终态改回去；`DocumentStatus` 枚举只放行 PENDING→PROCESSING/FAILED、PROCESSING→READY/FAILED。公共库是 Flyway V2 里插入的一行（`id=1, kb_type=PUBLIC, owner_id=NULL`），私有库 id 从 100 起。
- **入库是异步的**：上传接口 202 返回 PENDING；线程池里 PENDING→PROCESSING 后 `POST /v1/documents/ingest`（`callback=true`，ai-service 立即 202），ai-service 入库完成后带共享密钥回调 `/internal/documents/callback`。回调丢了 / ai-service 重启了怎么办：定时任务把超过 `UPLOAD_STALE_AFTER`（15 分钟）仍停在 PENDING/PROCESSING 的文档置为 FAILED（按数据库时钟算，避免应用与库时区不一致）。备选：同步等入库返回（大文档要几十秒，占住 HTTP 线程，且 backend 重启会丢状态）。
- **对话流：`SseEmitter` + JDK `HttpClient`（`BodyHandlers.ofInputStream()`，强制 HTTP/1.1，不走系统代理）**。备选 WebClient / OkHttp：要引入 reactive 依赖或新依赖，而这里只需要「读一条 SSE 流、顺序回调」；JDK 客户端足够，取消 = `future.cancel(true)` + 关闭响应流，连接随之断开（测试里用 JDK `HttpServer` 验证上游真的看到断开）。每路对话占一个读取线程（上限 64，满了返回 503）。
- **发现客户端断开**：Tomcat 只有写失败才知道连接没了，而 LLM 思考期间没有事件可写，所以 backend 每 `CHAT_HEARTBEAT`（默认 5 s）写一行 SSE 注释 `: ping`——心跳写失败 / 事件写失败 / `SseEmitter` 的 onCompletion·onTimeout·onError 任一触发即取消上游。代价：取消最晚延迟一个心跳间隔（e2e 实测约 3.3 s，见 LIMITATIONS）。
- **协议不变量在 Java 侧也成立**：上游中途失败或提前断开时，backend 补发 `error → disclaimer(固定文案) → done(status=error)`；`ChatService.DISCLAIMER` 与 ai-service 的 `compliance.DISCLAIMER` 是同一句（两边各有测试）。被取消的回答以 `CANCELLED` 保存已生成的部分；失败 / 被取消的回答及其提问不进入后续上下文（`recentHistory` 只取「提问 + 成功回答」成对的轮次）。
- **权限判定集中在 `KbService`**：`resolveScope` 算出范围；请求里任何一个 kb 不在「公共库 + 自己的私有库」里 → 整个请求 403，且**先于**保存提问 / 调用 ai-service；不存在与「是别人的」返回同一个 403 与同一句话。别人的会话 / 文档一律 404。
- **错误响应强制 `application/json`**：SSE 接口 `produces=text/event-stream`，客户端也会带 `Accept: text/event-stream`，不预设 Content-Type 时 Spring 找不到能写 `ApiResponse` 的转换器，403 会变成 500（集成测试发现，`GlobalExceptionHandler.build` 已修，WebMvcTest 里有回归用例）。
- **拦截器不注册成 Bean**：`@WebMvcTest` 会自动装入所有 `HandlerInterceptor` / `WebMvcConfigurer` Bean，鉴权用 `@Bean WebMvcConfigurer` 方法在 `WebConfig` 里创建，控制器切片测试不必关心鉴权，鉴权单独用 `WebConfigSecurityTest` 测。
- **上传**：校验魔数（PDF 必须 `%PDF-` 开头；md/txt 必须是不含 NUL 的合法 UTF-8）而不是信任 Content-Type；文件名去路径 / 控制字符；同库内 sha256 去重（409），上次失败的允许重传；落盘 `{UPLOAD_DIR}/{用户}/{库}/{sha256}.{ext}`（先写临时文件再原子改名）；`UPLOAD_DIR` 必须在 ai-service 的 `DATA_DIR` 之下，ai-service 按绝对路径读取（`resolve_allowed` 拒绝目录之外的路径）。
- **覆盖率规则**：JaCoCo `PACKAGE` 规则 + `includes=com.fundagent.backend.*.service`，即**每个 service 包各自**行覆盖率 ≥ 70%（比「合起来 70%」严）；`mvn verify` 里 `check` 目标强制。
- **集成测试**：Testcontainers `mysql:8.4.11`（与 compose 同版本）+ 整个 HTTP 栈（`@SpringBootTest` RANDOM_PORT），ai-service 用 JDK `HttpServer` 扮演；故意把 Redis 指向不通的端口，证明业务流程不依赖它。CI 的 ubuntu runner 自带 Docker；本机 Docker 在 WSL 里，用临时 TCP 代理暴露给 Windows（见 SETUP §6.2）。
- **后果**：backend 新增依赖 jjwt、spring-security-crypto（仅 BCrypt）、Testcontainers（test）、JaCoCo 插件；JWT 无吊销 / 刷新；`/internal/**` 只靠共享密钥。

## ADR-045 前端与容器化的取舍（S7，B7）
- **背景**：S7 要 Vue3 前端（登录、知识库与文档、对话页）、四个 Dockerfile 和 compose 的 `app` profile；统筹与用户对前端的补充要求：**显示私有库 / 公共库的检索范围、展示四类出处、风险提示固定显示且不能折叠隐藏**。
- **前端栈**：Vue3 + Vite 6 + TS + vue-router，不引入 Pinia 和 UI 库（全局状态只有登录态和知识库列表两个 `reactive` 模块）；Markdown 用 `marked` + `DOMPurify` 清洗后再把 `[n]` 换成角标（回答是 LLM 生成的、可能被提示注入影响，不能直接 `v-html`）；测试用 vitest + @vue/test-utils。**SSE 用 fetch + ReadableStream，自写解析器**（`src/api/sse.ts`）：不能用 EventSource（要 `Authorization` 头且是 POST）；先看 HTTP 状态码再读流（流开始前的错误是 JSON），忽略 `: ping` 心跳，块边界可在任意位置（单测把样本在每个字节位置切开，结果都一致，含 CRLF 与多字节字符被切断）。
- **风险提示两层，都没有关闭 / 折叠入口**：① 每条回答下显示服务端下发的 `disclaimer` 原文（历史消息取消息里的 `disclaimer` 字段），回答结束后仍没有（例如流开始之前就失败）时回退到固定文案，不留空；② 对话页底部 `DisclaimerBar` 常驻。组件测试断言两者都不含任何按钮 / `details` 类控件，且 ok / error / cancelled / failed 四种结局下每条回答都有风险提示。前端的固定文案是服务端文案的一份拷贝（`src/lib/constants.ts`），服务端改文案时要同步（记入 LIMITATIONS）。
- **检索范围**：`ScopePicker` 列出公共库与自己的私有库（带「公共库 / 私有库」标签），选中的 id 作为 `kbIds` **显式**发送（默认全选；用户取消的勾选记在 localStorage，上次访问之后新建的库默认勾选；一个都不选时禁止发送）；每条提问下面显示「检索范围：公共库 + 私有库「…」」（历史消息取 `kbIds`，库已被删时显示「已删除的库 #id」）；文档类出处另标「公共库 / 私有库」。这只是**申请**，授权由 backend 判定（ADR-043）。
- **出处**：四类 `kind` 各有图标和类型标签；折叠态一行摘要（文档 = 基金 / 文档 / 页码，数据库 = 表 + 行数，计算 = 起止净值日，接口 = 净值日期 + 「非最新」标记），展开看页码与片段 / 数据表、数据源、快照日期与 SQL / 计算入参 / 抓取时间。正文里的 `[n]` 是角标，点击展开并滚动到对应出处；**流式过程中 citations 事件还没到，角标类型按 `tool_end` 的工具名与 `citation_ids` 推断**，所以角标一出现就有对应图标。
- **构建上下文与镜像**：四个 Dockerfile 都在 `deploy/<服务>/`，构建上下文是仓库根，`.dockerignore` 排除 `data/`、`.cache`、`.venv`、`node_modules` 等（披露 PDF 与快照绝不进镜像）。backend 镜像构建时 `-DskipTests`（测试在 CI 的 `mvn verify`）；ai-service 先装 CPU 版 torch（`download.pytorch.org/whl/cpu`），再按 `pyproject.toml` 抽出的依赖清单安装，源码改动不会使依赖层缓存失效；代码放 `/app/ai-service` 并可编辑安装，使 `REPO_ROOT` 解析为 `/app`，与本机开发的目录布局一致（入库 CLI 按 MANIFEST 里的 `data/raw/...` 相对路径找 PDF、把报告写到 `reports/`）。
- **共享上传目录**：命名卷 `uploads` 挂在两个容器的同一路径 `/app/data/uploads`（backend 可写、ai-service 只读），另把宿主机 `./data` 只读挂到 ai-service 的 `/app/data`。两个镜像用同一个非 root uid（10001）并在镜像里预建该目录，命名卷首次创建时继承属主，两边才都能读写。
- **compose 变量**：不用 `env_file: .env`（会把 MySQL root 密码等全部发给每个容器），每个服务列出自己用得到的变量；容器互连用服务名与容器端口（`MYSQL_HOST=mysql`、`MYSQL_PORT=3306` 覆盖 `.env` 里给宿主机用的 127.0.0.1:3307），`MCP_ALLOWED_HOSTS` 加 `ai-service:*`（Agent 调自己的 `/mcp`）。应用变量一律 `${X:-}`，不用 `${X:?}`：compose 会对整个文件做插值，否则只起 infra 时也会因缺应用变量报错。
- **nginx**：`nginx-unprivileged`（非 root，监听 8080）；`/api/conversations/*/chat` 单独一个 location：`proxy_buffering off`、`proxy_http_version 1.1`、`proxy_read_timeout 300s`、`gzip off`；上游用变量 + Docker DNS（`resolver 127.0.0.11`），backend 重建换 IP 不会连到旧地址、backend 未起时 nginx 也能启动；`/internal/` 返回 404（回调只在容器网络内）。
- **模型缓存**：命名卷 `model_cache` 挂 `/models`；首次用 `python -m fund_ai.models_cli` 预下载（走与线上相同的加载路径：编码一条文本、给一对文本打分，所以下载的正好是运行时要用的文件并顺带验证能加载）。
- **健康检查**：backend 的运行镜像没有 curl / wget，用 bash 的 `/dev/tcp` 发 HTTP 请求并要求状态行是 200；其余服务用 Python `urllib` / `wget --spider`。
- **备选**：EventSource（不能带头也不能 POST，否决）；`env_file`（见上，否决）；把 `data/` 复制进 ai-service 镜像（违反「PDF 与快照不入仓库 / 镜像」的版权约定，否决）；用 Docker 卷而不是 bind 挂 `data/`（要先把数据拷进卷，多一步且本机 `data/` 就是数据源，否决）。
- **后果**：前端新增 npm 依赖（vue、vue-router、marked、dompurify；开发期 vite、vitest、jsdom、@vue/test-utils）；vitest 3 的 `npm audit` 有 2 条 moderate（`@vitest/mocker` 的 mock 重定向路径穿越，只影响开发期的 vitest，不进构建产物；升级到 5.x 需要 Vite 7，本批不动）。

## ADR-046 S8 回答评测的判分口径与运行方式（B8）
- **背景**：PLAN S8 规定 numeric / entity / list 规则判分、text 类 LLM 裁判 0/1/2、refusal 与 advice_request 判拒答、外加工具选择 / SQL / 收益 / 出处 / 风险提示 / 延迟 / 费用等指标，并要求所有 run 保留、失败单列。细节要在跑 test 之前写死（红线 2）。
- **决定（运行）**：评测直接调 `POST /v1/chat/stream`（真实 Agent、真实 LLM、公共库范围），两种检索配置各起一个本机 ai-service 进程，靠 `RETRIEVAL_MODE=vector|hybrid_rerank` 区分（文档检索 MCP 用默认配置，Agent 不能改检索参数，所以不新增任何请求参数）；两个进程共用 compose 里的 Milvus / ES / MySQL / mcp-tools。`run` 与 `score` 分开：answers.jsonl 是原始记录，判分可重跑而不重新问 Agent。**只有 API 本身报错（超时 / 5xx / 连接错误）的题重试，最多 2 次，每次尝试都记进 `attempts`；其他失败原样保留，续跑时已有记录的题一律跳过，不会重跑到成功。** 失败的 run 在准确率里记 0（`acc_all`），并另报只算成功 run 的 `acc_completed`；判分失败（裁判输出无法解析 / 参考净值缺失）的题不进分母，单独上报条数。
- **决定（规则判分）**：见 `ai-service/src/fund_ai/eval/scoring.py` 文件头。numeric：抽出回答里所有「数字 + 单位」，换算到基本单位，种类相容且落在 gold 容差内即对（主判分 `any`，灵敏度 `first` 只看第一个相容数字，两个都报）；entity：标准答案出现在回答里（日期归一化）；标准答案是基金简称且题面里还有别的候选基金时，要求标准答案先于其他候选出现；list：所有标准项都出现（严格版另要求没有多出的池内基金名，单列；**小样后修订**：dev 小样里 agent-0003 的正确回答因正文顺带提到另一只基金被严格规则误判，所以主判分改成「全部出现」，test 尚未跑过）。「多调用工具」只统计 no_tool 类题（advice_request 允许调工具陈述事实，小样后修订）。numeric 的 `any` 在 dev 小样里出现过一次碰巧命中（qa-0050：把另一份额的数字当成了 A 类，而它恰好等于标准答案），`first` 会判错。**用户 2026-09-30 决定：numeric 主口径用 `first`（回答里第一个相容数字），`any` 只作参考、单列，any 与 first 不一致的题逐条说明；README 与结论里的准确率只引用 first。**（上面三处口径修订——list 主判分、多调用只算 no_tool、numeric 主口径——都发生在看到 test 结果**之前**，依据只是 dev 小样，test 在做出这些修订时尚未运行过。）判分器自洽检查：entity / list 题的参考回答必须全部判对，numeric 题必须 `any` 判对（`first` 不要求：参考回答常先给别的相关数字，共 8 题）：评测集里 108 道 numeric / entity / list 题的 `reference_answer` 送进判分器必须全部判对（单测 `test_reference_answers_pass_their_own_scorer`）。
- **决定（裁判，用户 2026-09-30 修订）**：text 类 0/1/2 与 refusal 类二值判断用 `JUDGE_MODEL`。**最初默认 deepseek-v4-pro（ADR-018 / ADR-025，刻意与被测模型不同）；用户在费用关卡后决定改用 deepseek-flash（与被测模型同一个）**，`Settings.judge_model`、`.env.example`、CLAUDE.md §5 已同步；费用小样（dev）仍是用 v4-pro 判的，文件里记着响应模型名。同一个模型自己判自己，可能有自我偏好、给分偏宽，写进 LIMITATIONS；人工盲标的一致率就是用来检验这一点的。temperature 0、关闭思考、只输出 JSON；裁判只看问题、参考要点（及参考回答）和回答，不看检索配置与工具日志。unanswerable：`declined && !fabricated`；advice_request：`refused && !gave_advice && 规则违规词为空`（违规词用 `agent/compliance.py` 的扫描，与服务端同一份，只作补充检查）。
- **决定（其他指标）**：工具选择 = `expected_tools` 是否被调用集合包含（另报集合完全一致、无需工具题的多调用率；`get_fund_db_schema` 不计）；SQL 结果正确率 = tool_sql 类题端到端回答判对的比例，并另报 run_fund_sql 至少成功一次的比例（**没有**单独重跑 Agent 写的 SQL 去比对结果，写进局限）；出处准确率按 evidence（用 `data/raw/_chunks.jsonl` 把 400 字 snippet 还原成完整块再套 PLAN §4.3 命中规则，全部 evidence 都被某个被引片段覆盖才算对）/ gold_sql 涉及的表 / 收益计算的份额与实际起止日 / 净值的份额代码分别核对；风险提示覆盖 = 事件序列以 `disclaimer → done` 结尾且文案逐字一致，分母是全部 run（含失败）；最新净值（volatile）在 run 结束后由评测脚本**自己的代码**请求东方财富接口取参考值，回答必须含该净值（4 位小数）与净值日期。
- **费用**：价格取 DeepSeek 官方价目表（2026-09-30 读取，写在 `answer_score.PRICES`），`done.usage` 不区分缓存命中，所以按全部输入缓存未命中计，是上界；同时给高峰价与非高峰价。
- **备选**：给文档检索 MCP 加「按请求覆盖检索模式」的参数（改变生产接口，只为评测，否决）；用 LLM 判 numeric（PLAN 要求规则判分，否决）；重试直到全部成功（红线，否决）。
- **后果**：`ai-service` 新增 `eval` 可选依赖 openpyxl（盲标表 .xlsx，缺失时退回 CSV）；`Settings` 新增 `judge_model`。规则判分的局限（any 偏宽松、别名 / 简称识别不到会误判为错、二选一题靠首次出现位置）写进 LIMITATIONS。


## ADR-047 gRPC 改造的实现取舍（S9，B9）
- **背景**：PLAN S9 要求 proto（`Chat` server streaming、`Retrieve`、`IngestDocument`、`DeleteDocument`）、Java `GrpcAiServiceClient`（复用 channel、deadline、取消传播、可切回 http）、Python 端 grpc.aio 与 FastAPI 同进程，并保证两种传输协议行为一致。
- **决定（proto 与桩代码）**：proto 的唯一来源是仓库根 `proto/fundagent/v1/ai_service.proto`。Python 桩代码由 `scripts/gen_proto.py`（grpcio-tools 1.84.0，版本固定）生成并**提交**到 `ai-service/src/fundagent/v1/`（Docker 镜像与本机开发都不需要 protoc），CI 新增 `proto` job 用 `--check` 校验已提交的与 proto 一致；Java 桩代码由 backend 的 `protobuf-maven-plugin`（ascopes 5.1.11，protoc 4.36.2、protoc-gen-grpc-java 1.84.0）在构建时从同一份 proto 生成，**不提交**（所以 `mvn verify` 也校验 proto 能编译）。两边版本对齐：Python `grpcio>=1.84` / `protobuf>=7.36`（生成的桩代码会在 import 时校验运行时版本，下限必须与生成工具一致），Java `grpc 1.84.0` / `protobuf-java 4.36.2`。Docker 的 `.dockerignore` 原先排除了 `proto/`，改为只排除 `proto/testdata`，backend 镜像构建时 `COPY proto /proto`。
- **决定（事件建模）**：`ChatEvent` 用 `oneof`，字段名就是 SSE 事件名，消息的字段名与 HTTP JSON 完全一致，所以 Python 侧直接用 `json_format.ParseDict` 把 Agent 产出的 dict 填进消息（事件协议仍然只有「Agent 产出的 dict」这一个事实来源；测试里用**严格**模式——proto 缺字段会立刻失败；生产里遇到未知字段不能让流中断，因为 `disclaimer` 必须在 `done` 之前发出，所以退回宽松解析并记 error 日志）。JSON 里「可能缺省」的字段用 `optional`（有存在性），Java 侧用 `JsonFormat`（保留 proto 字段名）把消息还原成 SSE 的 `data`，字段集合与 HTTP 一致；已知差别只有**数组字段总是输出**（空数组也输出）。`citations.items[]` 用 `oneof detail` 建模（四种出处各有类型），Java 侧还原时展平到与 `id`、`kind` 同一层；工具入参 `args` 是任意 JSON，用 `google.protobuf.Struct`。**不用 int64**（protobuf 的 JSON 映射会把 int64 写成字符串）；耗时一律 double。消息 `Error` 命名为 `ChatError`（避免与 `java.lang.Error` 在生成的 Java 包里同名）。
- **决定（服务端）**：grpc.aio 服务端在 FastAPI 的 lifespan 里启动，与 HTTP 同进程同事件循环（`AI_GRPC_ENABLED` / `AI_GRPC_HOST` / `AI_GRPC_PORT`）。为了让两种传输不分叉，把 HTTP 处理函数背后的逻辑抽成传输无关的函数（`chat_events`、`run_retrieve`、`do_ingest`、`do_delete`），两边共用；校验错误（pydantic、`HTTPException`）按 proto 文件头的映射转成 gRPC 状态码。**取消**：客户端取消 / 超 deadline 时 grpc.aio 取消处理协程，取消沿 LangGraph → LLM / MCP 传播（与 HTTP 断开时同一条路径），`chat_stream_cancelled` 日志里带 `transport=`，用来量取消延迟。端口被占用时只记 error、HTTP 照常服务（本机同时起两个 ai-service 做评测时第二个要 `AI_GRPC_ENABLED=false`）。gRPC 端口**没有鉴权**（与 HTTP 一致，信任内网）；compose 只把它绑定到宿主机回环。`IngestDocument(callback=true)` 返回 `accepted`，完成后仍是 **HTTP 回调** backend（回调是 ai-service → backend 方向，不在本阶段范围）。
- **决定（Java 客户端）**：`GrpcAiServiceClient` 与 `HttpAiServiceClient` 实现同一个 `AiServiceClient` 接口，`fra.ai.transport`（`AI_TRANSPORT`）选择，**默认 grpc**，设为 http 即回到第一期实现（两个都保留，并有 `ApplicationContextRunner` 测试）。一个进程一个 `ManagedChannel`（HTTP/2 多路复用，keepalive 30 s）；对话流带 deadline（`AI_GRPC_CHAT_DEADLINE`，默认 6 分钟，大于 SSE 的 5 分钟 emitter 超时）；**手动流控**——处理完一个事件（写进 SSE）才向上游要下一个，客户端读得慢时不会在本进程无限堆积（单测验证）；回调在 `chatStreamExecutor` 上串行执行；保留与 HTTP 实现一致的「空闲超时」看门狗（`AI_CHAT_IDLE_TIMEOUT`）。取消 = `ClientCall.cancel`（RST_STREAM），不依赖心跳。与 HTTP 实现的一个差别：HTTP 每路对话占一个读取线程（上限 64，超出 503），gRPC 是异步回调，不再有这个并发上限（线程只在处理事件的瞬间占用）。`Retrieve` 只在 proto / Python 侧实现并测试，Java 的 `AiServiceClient` 没有对应方法（backend 没有调用点，YAGNI）。
- **备选**：手写 dict↔proto 映射（字段多、易漏，改事件时要改三处，否决）；整个事件用 `Struct` 包一层（等于没有 schema，否决）；Java 桩代码提交进仓库（生成物 diff 噪声大，且构建本来就能生成，否决）；用 `xolstice protobuf-maven-plugin`（0.6.1，需要 os-maven-plugin 扩展，且已不活跃，否决）；gRPC 与 FastAPI 分两个进程（PLAN 规定同进程，且要共享检索服务 / Agent 单例，否决）。
- **后果**：ai-service 新增运行时依赖 grpcio、protobuf（dev：grpcio-tools）；backend 新增 grpc-netty-shaded / grpc-protobuf / grpc-stub / protobuf-java-util；backend 单测中 `BackendIntegrationTest` 需要 Docker（Testcontainers），本机 Windows 无法跑，只在 CI 验证（与第一期相同）。


## ADR-048 Redis：限流 + 每日配额 + 语义缓存的实现取舍（S10，B10）
- **背景**：PLAN S10 要求令牌桶（Lua 原子、用户 / 全局两个维度、429 + Retry-After）、每日配额（次数与 token，定时回写 MySQL）、语义缓存（Redis 向量索引、按 `kb_id + kb_version + DATA_AS_OF` 隔离、时效性 / 荐基 / 出错的回答不缓存、命中时按同一事件协议回放并标 `cache_hit`、风险提示照常追加）、阈值校准集（同义改写对 + 金融难负例对），验收含并发限流测试、配额跨天、缓存误命中率与命中 / 未命中延迟。
- **决定（令牌桶，`backend/.../ratelimit/`、`lua/token_bucket.lua`）**：一个 Lua 脚本一次检查「该用户的桶」和「全局桶」，**两个都够才同时各扣一个**，被拒时不写任何状态（不会因为全局被拒而白扣用户的令牌）；时间取 Redis 服务端的 `TIME`，多个 backend 实例不受本机时钟偏差影响；桶不存在 = 满桶，键带过期（空闲足够久必然是满的）。被拒时返回需要等待的毫秒数，向上取整成 `Retry-After` 秒。**检查顺序**：令牌桶放在 `ChatService.start` 最前面（之后没有任何数据库访问，被拒的请求几乎不耗资源）→ 会话归属与检索范围校验（越权先 403）→ 每日配额（校验通过、确定要调用 AI 才占用）。默认值（用户容量 10、0.5 个/秒，全局容量 100、10 个/秒）是**经验值，不是实测容量**，S12 压测后再校准；全部走环境变量。
- **决定（每日配额，`QuotaService`、`lua/quota_acquire.lua`、`V3__s10_usage_daily.sql`）**：计数是 Redis hash（`calls`、`tokens`），键里带「配置时区的自然日」，所以过零点自动是新的一份（跨天），键 TTL 3 天；占用次数是 Lua 里「检查 + HINCRBY」原子完成，并发下放行数不会超过上限（单测 100 个并发抢 25 个名额，恰好放行 25）。**token 是软上限**：token 数在对话结束（`done.usage.total_tokens`）才知道，开始前只检查「已用量是否已达上限」，最后一次请求可以越过它；请求跨过零点时 token 记在开始的那一天。被取消的对话没有 `done`，其 token 不计（局限）。缓存命中算一次调用（它占用了系统）但 `usage` 为 0，不耗 token。变动过的 `userId:日期` 记进 `fra:quota:dirty` 集合，`UsageFlushJob` 每 30 秒弹出 → 读当前值 → 按**绝对值** upsert 到 `usage_daily`（幂等；`GREATEST` 保证 Redis 丢数据后不会把库里更大的值改小），失败的成员放回集合下轮重试，关闭时再回写一次。Redis 里没有今天的计数时（重启且无持久化）用 MySQL 的值补种（`HSETNX`，不覆盖已有计数），所以丢 Redis 数据不会让配额「意外清零」。
- **决定（故障时放行）**：Redis 不可用（令牌桶 / 配额脚本抛异常）时**放行**并记 warn（`ChatAdmission`）。限流和配额是保护措施，不应该让 Redis 故障变成整站不可用；代价是 Redis 故障期间没有限流 / 配额。被限流的异常（`RateLimitedException`）仍然正常抛出，不会被当成故障吞掉。
- **决定（语义缓存位置与命名空间）**：放在 `fund_ai.api.chat.chat_events()`——HTTP 与 gRPC 共用的对话入口（S9 的约定）。缓存条目挂在一个**命名空间**下，只在同一命名空间内做近邻检索；命名空间 = 哈希（公共库版本、各私有库 id 与版本、owner、`DATA_AS_OF`、**模型 / 提示词 / 工具轮数上限的指纹**、嵌入模型 id）。「公共库版本」默认取 `data/MANIFEST.json` 的 sha256 前 12 位（数据换了版本就变）；「私有库版本」由 backend 在算检索范围时一并给出（`KbScope.private_kb_versions`）：库里 **READY 文档的数量 + 其中最近一次更新的毫秒时间戳**，增删文档、重新入库都会变（只在 backend 知道，所以走检索范围传给 ai-service；proto 加 `map<string,string>`，向后兼容）。PLAN 只写了 `kb_id + kb_version + DATA_AS_OF`，把模型 / 提示词 / 嵌入模型也放进去是因为它们同样决定「同一个问题的答案是否还有效」。
- **决定（不缓存什么，`cache/policy.py`，单测覆盖）**：请求侧（不查也不写）：带对话历史的提问（追问依赖上文）、荐基 / 择时 / 买卖建议类提问（`compliance.looks_like_advice_request`，关键词启发式，宁可多判）、含「今天 / 现在 / 实时…」的相对时间提问。回答侧（不写）：用过 `get_latest_nav` 的（含出处里有 `api` 类）、`error` 事件 / `done.status != ok`、工具服务不可用、输出守卫命中违规表述、达到工具轮数上限、没有正文、超长。**读取侧不依赖回答的任何标记**：能被读取的条目都已经通过了写入侧的检查。
- **决定（回放）**：按与 Agent 相同的顺序回放 `meta → (tool_start → tool_end)* → token* → citations → disclaimer → done`；`meta` / `done` 带 `cache_hit`，`done` 另带 `cache_similarity`、`cache_lookup_ms`，`usage` 全 0；`request_id` 用新请求的；**`disclaimer` 由服务端常量重新生成，不从缓存取**（文案改了旧缓存回放出来的也是新文案，单测验证）。开启缓存后未命中的请求的 `meta` / `done` 带 `cache_hit: false`；没开缓存时这个字段不出现。命中不需要 Agent（LLM 密钥 / 工具服务）可用——构造 Agent 失败时仍能回放缓存。proto 新增 `Meta.cache_hit`、`Done.cache_hit / cache_similarity / cache_lookup_ms`（`optional`），金样例（`proto/testdata/chat_events.jsonl`）补了这四种事件，Java 侧跨语言单测通过。
- **决定（存储与故障）**：Redis 8 Query Engine，HASH + `VECTOR FLAT`（FLOAT32、余弦），查询 `(@ns:{…})=>[KNN k @vec …]` 先按命名空间 TAG 过滤再取 top-k（k=3）；索引名带维度（换嵌入模型不会撞上旧数据）；条目 TTL 24 小时。redis-py 8 默认 RESP3，`FT.SEARCH` 响应解析同时支持 RESP2 / RESP3（单测）。嵌入用 `embed_documents`（不加查询指令前缀：这里要的是两个问题之间的对称相似度），在线程池里跑。**任何缓存故障（构造失败、嵌入出错、Redis 不可用、写失败）都当作未命中，对话照常走 Agent**，只计数 + warn；单飞（同一个未命中问题并发时只跑一次）**没做**。**默认关闭**（`SEMANTIC_CACHE_ENABLED=false`，compose 里也是）：评测脚本与本机开发不会被缓存悄悄影响（评测调的是同一个 `/v1/chat/stream`，开着缓存会让重复题命中旧回答）；`.env.example` 说明了怎么打开。
- **决定（关键要素守卫，`cache/guard.py`）——dev 上的发现**：第一次在 dev 上扫描阈值（`reports/cache_calibration/20261002T050213Z_dev`，当时数据集还有一个代码紧贴年份的渲染缺陷，已修）发现 **BGE 句向量的余弦相似度分不开「只差一个关键要素」的问题**：难负例（管理费 vs 托管费、A 类 vs C 类、一季度 vs 二季度……）的相似度中位数 0.82、最大 0.99，同义改写的中位数 0.92，两个分布大面积重叠；**零误命中的阈值是 0.995，此时 recall 为 0**（等于只有一字不差的问题才命中）。所以命中改成「相似度 ≥ 阈值 **且** 两个问题的关键要素签名一致」：签名 = 基金（用检索同一套实体识别，代码 / 简称 / 全称归一到主代码）+ 数字与时间（年份、季度、季末日期、近 N 年 / 个月、持有 N 天 / 年、第 N 大、百分比与金额；中文数字先转阿拉伯数字）+ 术语概念（费用种类、份额类别、上下限、现任 / 前任、指标名、条款名…，每个概念可有多种说法）。**这是启发式不是证明**：词典里没有的区分维度守卫看不见，只能靠相似度兜底。**选阈值的规则在看结果之前写进了代码**（`eval/cache_calibration.py` 文件头，红线 2）：规则 A（只用相似度）选零误命中的最小阈值；规则 B（相似度 + 守卫）选「不低于 0.80（预先写定的保守下限）、且 dev 上难负例与无关对都零误命中」的最小阈值。规则 B 与守卫是在 dev 第一次扫描之后、实现守卫之前补充的（文件头如实写了这一点）；守卫词典只依据 dev 调整。
- **决定（校准结果）**：详见 `docs/perf/semantic_cache.md`、`reports/cache_calibration/`。dev：A 的阈值 0.995（recall 0 / 72），B 的阈值 0.80（recall 64 / 72 = 0.889，难负例误命中 0 / 104，无关对 0 / 32）。**test 只跑了一次**（`TEST_RUN.json` 标记，代码再次运行会拒绝），B 在 test 上：recall 77 / 92 = 0.837（95% 区间 0.748–0.899），**难负例误命中 3 / 132 = 2.3%（0.8%–6.5%）**，无关对 0 / 40；A 在 test 上 recall 0 / 92。3 次误命中全部来自词典里没有的区分维度（「股票」vs「港股通标的股票」× 2、「仓位变化」vs「市场展望」× 1），正是守卫的已知局限。另报配置 B_dev（词典只保留 dev 问题里出现过的概念，用来估计对没见过的维度的泛化）：与 B 的 test 结果相同。**这 2.3% 是「缓存里恰好已经有一个只差一个要素的近邻」条件下的误命中率**（测试集里每个难负例都是这样构造的）；真实流量里这种近邻出现的概率远低于 100%，所以生产误命中率更低，但**没有测过**。
- **决定（数据集缺陷的处理）**：test 跑完之后发现数据集有一个渲染缺陷——简称以数字结尾的「天弘中证医药100」遇到年份开头的槽位时，「代码后补空格」规则把 `100202` 误当成代码，生成了「天弘中证医药100202 6年」这样的畸形问题（8 对：dev 1、test 7）。**没有改数据集、没有重跑 test**（改了就不是同一份数据了，也违反「test 只跑一次」）：官方数字是含这 8 对的第一次运行；另在**同一次运行的逐对相似度**上排除畸形对重新计数，作为事后诊断单独列出（test：recall 77 / 89，难负例误命中 3 / 128），明确标注「事后」。构建脚本 `scripts/build_cache_pairs.py` 保持原样以便复现 v1，缺陷写进文件头与 `eval/datasets/CACHE_PAIRS.md`；下一版（v2）需要修掉并重新校准。
- **备选**：只用相似度阈值（dev 实测零误命中点 recall 为 0，否决）；把阈值直接定在 0.95 这类「常见经验值」（dev 上 0.95 时难负例误命中 13 / 104，否决）；用 reranker / LLM 判断「两个问题是否等价」（会给命中路径加上几百毫秒到几秒，且没有评估过，**没有尝试**，只是没选）；只做精确匹配缓存（等价于配置 A 在 1.0 处，命中率几乎为 0）；缓存键里加对话历史哈希让追问也能缓存（命中率低、复杂度高，否决）；令牌桶放网关 / nginx（本项目 backend 才有用户身份，否决）。
- **后果**：backend 新增表 `usage_daily`、`fra.ratelimit.*` 配置、`GET /api/usage/today`、429 + `Retry-After`；backend 测试新增对真实 Redis / MySQL 的测试（CI 用 Testcontainers；本机没有可供 Testcontainers 连接的 Docker，用 `FRA_TEST_REDIS` / `FRA_TEST_MYSQL` 指向一次性实例，不设置则真的去起容器、起不来就失败，不跳过）；ai-service 新增 `fund_ai/cache/`、`eval/cache_calibration.py`，`Settings` 新增 `semantic_cache_*`、`kb_public_version`；`KbScopeIn` 新增 `private_kb_versions`；评测集目录新增 `cache_pairs_v1.jsonl`（**不是** PLAN §4.3 里已冻结的 fund_qa / agent_tasks，所以没有改冻结的 MANIFEST，另写 `CACHE_PAIRS.md`）。局限见 `docs/LIMITATIONS.md`「语义缓存与限流（S10）」。


## ADR-049 Kafka 季报批量入库 + Redis 分布式锁的实现取舍（S11，B11）
- **背景**：PLAN S11 要求「某个报告期的季报发布后，一次性把基金池所有基金的季报批量入库」：Java 侧 `POST /api/ingest-batches {report_period}` + outbox（与业务数据同事务）+ relay 发 Kafka + 消费结果幂等更新 + 进度查询；Python 侧 aiokafka 消费者组、处理完才提交 offset、`lock:ingest:{doc_id}` 分布式锁（SET NX PX + token + Lua 比对删除 + watchdog）、sha256 没变且 READY 则跳过、重试 N 次进 DLQ。验收要求锁的单测、「重复提交 + 2 个消费者实例每份文档只处理一次」、「处理到一半被 kill 后另一实例接管并最终一致」、毒消息进 DLQ、贴进度接口原文。
- **决定（topic 与消息）**：`doc.ingest.requested`（4 分区）、`doc.ingest.result`（2 分区）、`doc.ingest.dlq`（1 分区，保留 30 天），**key 一律是 `doc_id`**，所以同一文档的所有消息在同一分区内保持顺序（先到的入库、后到的重复请求拿到 SKIPPED）。消息体是 UTF-8 JSON（snake_case，带 `schema: 1`），字段定义见 `ai-service/src/fund_ai/messaging/protocol.py` 与 `backend/.../ingestbatch/IngestMessages.java`，两边各有测试锁住字段集合。4 个分区是为了「两个消费者实例各分得到分区」，不是吞吐设计（单实例一次只处理一份文档，瓶颈是 CPU 上的 embedding）。topic 由 compose 里一次性的 `kafka-init` 显式创建，关闭自动建 topic（分区数是设计的一部分）。Kafka 用 `apache/kafka:3.9.1`（KRaft combined 单节点，堆 512m，`mem_limit` 1g，宿主机 listener 9094、容器网络 `kafka:9092`）；与 spring-kafka 管理的客户端（3.9.x）同代。
- **决定（outbox + relay，`OutboxRelay`、`V4__s11_ingest_batch.sql`）**：批次、任务、outbox 消息在**同一个 MySQL 事务**里写入；relay 用 `@Scheduled` 轮询（默认 500ms）`SELECT … WHERE status='PENDING' ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED`，批量异步发送后逐条等确认，成功的标记 SENT 并把任务从 PENDING 推进到 SENT（只推进 PENDING——结果可能已先到，终态不能被改回）。`SKIP LOCKED` 让多个 backend 实例各领各的行。**语义是至少一次**：Kafka 已确认、标记 SENT 之前进程崩溃，下一轮会重发——消费端用「文档锁 + sha256 没变且 READY 则跳过」吸收。Kafka 不可用时消息留在表里，恢复后自动发出；`send()` 在拿不到元数据时（`max.block.ms=5s` 后）同步抛异常，relay 把错误记在这一行（`attempts`、`last_error`）并**停止本轮后面的行**（否则每行再等 5 秒，而事务一直开着；单测复现过）。
- **决定（批次幂等）**：`ingest_batch.active_key`（进行中 = `类型:报告期`，结束后置 NULL）上有唯一键，所以同一报告期同一时刻只有一个进行中的批次；重复提交返回已有批次（HTTP 200，`created=false`），不会重复建任务、不会重复写 outbox。进行中的批次超过 24 小时（`INGEST_BATCH_STALE_AFTER`）没结束，不再阻止新批次（旧批次标 EXPIRED）。**批次结束后**同一报告期可再建批次——这就是「整批重投」，由消费端的 READY 跳过让它几乎零成本。
- **决定（事务写法——单测里撞出来的）**：用 `TransactionTemplate` 显式划事务，不用 `@Transactional`。`expireStale`（一条在唯一索引上可能没命中任何行的 UPDATE）**必须在创建事务之外**：放在同一个事务里，它留下的间隙锁会让并发的两次创建在随后的 INSERT 上互相死锁（8 线程并发提交的单测第一次就复现了 `Deadlock found`）。另外对 `DuplicateKeyException | CannotAcquireLockException` 一并按「已有进行中的批次」处理。
- **决定（结果幂等更新，`IngestBatchService.applyResult`）**：只更新状态仍是 PENDING / SENT 的任务（`WHERE id=? AND batch_id=? AND doc_id=? AND status IN ('PENDING','SENT')`），所以重复的、迟到的、`doc_id` 对不上的结果都是 0 行更新、被忽略，终态不会被改写。事务里**先 `SELECT … FOR UPDATE` 锁批次行**再更新并检查「是否全部完成」：否则两个线程（或两个 backend 实例）同时处理同一批次的最后两条结果时，各自都看到对方的任务「还没完成」，批次永远停在 RUNNING（单测：20 份文档的结果由 10 个线程并发处理，批次恰好结束一次）。结果监听对无法解析的消息记录后丢弃，对数据库异常无限重试（每 2 秒；丢了结果任务就永远停在 SENT，宁可卡住也不丢）。
- **决定（进度口径）**：`total = succeeded + skipped + failed + processing`，`processing = pending（还在 outbox，没发出）+ inFlight（已发到 Kafka，等结果）`；批次状态 RUNNING / COMPLETED / COMPLETED_WITH_FAILURES / EXPIRED；`failures` 列出失败任务的原因（最多 50 条），`?tasks=true` 附带全部任务明细。
- **决定（消费者）**：aiokafka 消费者组，`enable_auto_commit=False`，**一条消息处理完（结果与 DLQ 都已发出）才 `commit({tp: offset+1})`**，`max_poll_records=1`（单实例一次一份文档）。发布结果 / DLQ 失败时不提交、重试到成功。`CommitFailedError`（处理期间发生了再均衡）只记 warning，消息会被新的分区所有者重新收到，由幂等逻辑吸收。消费者有两种运行方式：嵌在 ai-service 进程里（`KAFKA_CONSUMER_ENABLED=true`，FastAPI lifespan 启动，与 HTTP 入库共用同一个 `ingest_lock` 与 embedding 模型，**compose 默认开**）；或 `python -m fund_ai.messaging.worker`（第二个实例、演示、kill 测试用）。本机直接起 ai-service 评测时默认**关**（`.env.example` 里这一行故意注释掉：写进 `.env` 会同时覆盖 compose 的默认值）。
- **决定（分布式锁，`messaging/lock.py`）**：键 `fra:lock:ingest:{doc_id}`（PLAN 的 `lock:ingest:{doc_id}` 加全项目通用的 `fra:` 前缀）；加锁 `SET key token NX PX ttl`（默认 30s）；token = `消费者标识:uuid`，所以能从 Redis 里直接看出是谁持有；释放和续期都是 Lua「先比对 token 再 DEL / PEXPIRE」，错误 token 或已过期的锁既不能释放也不能续期；**watchdog** 每 `ttl/3` 续一次，长时间的入库不会因为超过 TTL 而丢锁，进程被 kill 时 watchdog 随进程消失、锁在 TTL 后自动过期——这是「kill 后另一实例接管」的前提。**丢锁检测**：续期返回 0 时置 `lost`；入库完成后、写 READY 状态之前必须 `verify()`（读键比对），丢了就抛 `LockLostError`，按可重试错误处理，**不写 READY**（入库本身先删后写，重做是幂等的）。别的实例拿不到锁时**等待**（默认最多 180 秒，每 200ms 重试），等到后再检查 READY——所以同一份文档的两条并发请求，后者得到 SKIPPED，而不是失败。
- **决定（READY 状态与跳过规则，`messaging/state.py`）**：状态存在 Redis hash `fra:ingest:state:{doc_id}`（`status=READY`、`sha256`、`chunks`、`consumer_id`、`updated_at`），入库**两个存储的条数都核对通过**且仍持有锁之后才写。跳过规则：消息里的 sha256 == 文件实际 sha256 == 状态里的 sha256，状态为 READY，**并且两个存储里该 doc_id 的条数都等于状态里记的块数**（Redis 的 READY 还在、但 Milvus 卷被清了的情况下不会错误地跳过）。文件实际 sha256 与消息（来自 MANIFEST）不一致是不可重试错误（进 DLQ）。状态丢了只会多入库一次，不会出错。
- **决定（失败分类与 DLQ）**：**不可重试**（`PermanentIngestError`：文件不存在、路径越界、sha256 不符、解析不出文字、不支持的类型；`PoisonMessageError`：JSON 无法解析、字段不合法）立刻判死；**可重试**（存储 / 网络异常、两边条数对不上、丢锁、等锁超时）最多尝试 `INGEST_MAX_ATTEMPTS`（默认 3，含第一次），第 n 次失败后退避 n × 2 秒。判死的消息：先写 DLQ（key 同原消息，**value 是原始字节**，便于排查和重投；headers：`error`、`attempts`、`source-topic / partition / offset`、`consumer-id`、`failed-at`），再发 `FAILED` 结果（捞得到 `task_id` 就发，所以 backend 的进度里能看到失败原因），最后才提交 offset——毒消息不会卡住后面的消息。这是对 PLAN「重试 N 次后进 DLQ」的一处细化：不可重试的错误不浪费重试次数（`attempts=0 / 1` 就进 DLQ）。
- **备选**：Kafka 事务（跨 MySQL 和 Kafka 没有原子性，outbox 才是这个问题的标准解，否决）；Debezium / binlog CDC 代替轮询 relay（多一个组件和内存，对「20–100 条消息的批次」是过度设计，否决）；按 `doc_id` 单分区顺序性代替锁（再均衡、重复消息、两个独立的 backend 触发路径下仍会重复处理，锁 + 幂等状态才能保证「恰好一次的效果」，两者并用）；RedLock / Redisson（单个 Redis 实例，复杂度换不来收益；并且 PLAN 明确要求 SET NX PX + Lua + watchdog 的自实现，否决）；DLQ 自动重投（没有需求，先留原始消息，重投留给运维 / 后续阶段）；手动提交之外用 Kafka 的 `isolation.level` 等（不相关）。
- **后果**：backend 新增依赖 spring-kafka、测试依赖 testcontainers-kafka；新增表 `ingest_batch / ingest_task / outbox_event`（V4）、`POST|GET /api/ingest-batches`（API.md）、`fra.ingest-batch.*` 配置；ai-service 新增依赖 aiokafka、`fund_ai/messaging/`、`Settings` 的 `kafka_*` / `ingest_*` 配置；compose 新增 `kafka`、`kafka-init`（infra 内存预算 +1g，PLAN §3 已计入）并给 ai-service / backend 接线（backend 挂载 `data/MANIFEST.json` 只读）。**任何登录用户都能创建批次**（项目没有角色体系）；批次只能从 MANIFEST 已登记的文件创建，请求体不接受路径，所以不能借它读任意文件。CI 新增 `messaging` job（服务容器里的真实 Redis 8 + Kafka 跑集成测试）；backend 的 relay / 监听测试在 `mvn verify` 里用 Testcontainers 的 Kafka + MySQL。


## ADR-050 压测方法与工具的取舍（S12，B12）
- **背景**：PLAN S12 要求 mock LLM（OpenAI 兼容、流式、首 token 延迟与 tokens/s 可配、能按脚本返回 tool call）、Locust 场景 A–E、阶梯加压、每点重复 3 次取中位数、py-spy / JFR / `docker stats` 定位，产出 `docs/perf/baseline.md` + 原始数据 + 火焰图。
- **决定（mock 回放真实运行，`loadtest/mock_llm/`）**：mock 不用拍脑袋的延迟参数，而是**回放 S8 的真实运行记录**（`reports/answer_eval/20260929T171529Z/hybrid_rerank/answers.jsonl`，124 题全部可用）：每个问题真实走过的工具路线与参数（含 SQL 原文）、真实回答文本、每次 LLM 调用的首 token 延迟、输出 token 数和输出速度。脚本里没有的问题走默认路线（先 `search_fund_documents(query=问题)`，再返回脚本里一段真实回答）；Agent 的「强制作答」轮（请求不带 `tools`）一律直接作答。延迟可以用环境变量覆盖成固定值（`MOCK_TTFT_MS` 等），也可整体缩放。同一个 mock 进程兼任净值接口的 stub（`GET /f10/lsjz`，延迟 `MOCK_NAV_MS`，默认 250 ms：S8 里 `get_latest_nav` 的中位数 670 ms 减去每次 MCP 调用约 380 ms 的固定开销），所以 `get_latest_nav` 在压测里不碰外网。mock 响应的 `model` 恒为 `mock-llm`，`usage` 来自记录或按字符数估算——**不是计费数字**。
- **决定（压测栈）**：不改默认的 `docker-compose.yml`，用覆盖文件 `loadtest/compose.loadtest.yml`（`stack.sh up [cache]` / `restore`）把 ai-service 的 LLM 指向 mock、mcp-tools 的净值接口指向 mock，backend 关限流并取消配额（令牌桶默认值会让并发压测立刻 429），ai-service 关掉 Kafka 消费者（空转也占一份 embedding 模型内存）；`PERF_CACHE=true` 才开语义缓存（只有场景 D 需要）。mock 容器复用 `fra/ai-service:dev` 镜像（已有 fastapi + uvicorn），代码只读挂入，不另建镜像。
- **决定（负载模型）**：**闭环**（每个虚拟用户读完整条流立刻发下一个，think time = 0，并发数 = 用户数）。理由：本项目的瓶颈在 CPU 上的重排，闭环能直接给出「并发数 → 吞吐 / 延迟」曲线和拐点；代价是**协调遗漏**（过载时排队延迟被低估），README 与报告里都写明，拐点以「QPS 不再随并发上升」为准。入口：A 直连 ai-service `/v1/retrieve`；B / C / D 经 backend（登录用户 → 新建会话 → SSE 提问；每个请求一个新会话，所以不带历史，语义缓存可以命中）；每个虚拟用户固定一个预先注册好的账号（`make_users.py`，BCrypt 不污染压测）。
- **决定（统计口径，`perf_common.window_stats`）**：每次运行先预热 N 秒不计入，再取一个固定长度的窗口；统计**窗口内完成**的请求；QPS = 完成数 / 窗口长度；错误率分母 = 窗口内完成数；延迟只统计成功请求（失败按错误类别单独计数）；每档并发独立跑 3 次，报告三次各自指标的**中位数**，并列出三次原值与每次窗口内完成数。窗口内完成数很少的点（A / B 的低并发），分位数的可信度有限，报告里逐档写明 n。
- **决定（数据与原始记录）**：Locust 自己的统计只作交叉核对；每个请求写一行 JSONL（含 SSE 首字节、首 token、总耗时、`done` 事件里的服务端分段 `llm / tools / first_token`、每个工具的耗时、缓存命中），gzip 后随 `reports/perf/<ts>_<场景>/runs/*/requests.jsonl.gz` 入库；`summary.json` 带 git commit、数据集 / MANIFEST / 回放脚本来源的 sha256、栈配置、机器信息、命令行原文（PLAN §4.4）。docker stats（`dockerstats.sh`，每 ~1.5 s 一次）与宿主机 / Locust 进程的 CPU（psutil）同步采样，用来判断压测端是否成为瓶颈。
- **决定（定位工具）**：py-spy 用**旁路容器共享 ai-service 的 PID 命名空间**（`--pid=container:fra-ai-service --cap-add SYS_PTRACE`）采样，不改被测镜像；JFR 通过 `PERF_BACKEND_JAVA_OPTS` 在 backend 容器里启动、停止时转储，用宿主机 JDK 17 的 `jfr` 解析（`stack_summary.py` 汇总）。分段打点不新增埋点：`done` 事件的 `timings_ms` 与 `/v1/retrieve` 返回的 `timings_ms` 已经给出 llm / tools / embed / vector / bm25 / rerank 的耗时。
- **备选**：开环（固定到达率，Locust `constant_throughput` / 自定义 LoadTestShape）——能避免协调遗漏，但需要先知道容量；本阶段先用闭环找拐点，开环留给 S13 的前后对比（否决不是因为不好，而是顺序）。用固定延迟的 mock（`MOCK_TTFT_MS=…`）——简单但数字无来源，只保留为覆盖开关。Locust 分布式（master + worker）——单机已足够，且会抢更多 CPU，否决。Locust 装进 `ai-service` 依赖或镜像——只在本机压测用，装在 `loadtest/.venv`，不进 CI 和各服务镜像（CI 只跑 mock 与统计函数的单测）。
- **后果**：新增 `loadtest/`（mock、locustfile、编排、报告表格、py-spy / docker stats 脚本、单测）、CI 的 `loadtest` job、`.gitignore` 增加 `loadtest/mock_llm/replay_v1.json`（由 `replay.py` 确定性重建）和 `loadtest/.secrets/`（压测账号令牌）；`docs/perf/baseline.md` 与 `reports/perf/` 是 S12 的证据。局限见 `docs/LIMITATIONS.md`「压测（S12）」。
