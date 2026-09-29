# 进度记录（PROGRESS）

> 由执行者在每个阶段结束时按 CLAUDE.md §11 的模板追加一节，旧内容不删除。统筹审查后会在对应小节末尾追加「统筹审查结论」。

## 当前状态
- 当前阶段：批次 B4（S5 前半：mcp-tools 的 4 个工具）已完成。下一批 B5（S5 后半：文档 MCP、LangGraph、出处、风险提示、SSE），见 `docs/HANDOFF.md`。统筹对 B3 遗留的两个问题已答复（ADR-038、ADR-039）
- 最近一次 CI：见 `docs/HANDOFF.md`
- 已完成的用户事项：`.env` 已生成（含 DeepSeek key）；`.wslconfig` 已设 10GB/4GB；**基金池 v1 已于 2026-09-29 确认并冻结**；**S3 评测集抽检已于 2026-09-29 完成**（27 条，通过 23，修改 4）
- 待用户处理：
  1. 决定是否停掉 ticket-qa 的容器（S0 实测合计约 844MiB）
  2. （可选）用自己的浏览器确认证监会披露网站能否访问
  3. S8 盲标回答

---

## S0 脚手架与基础设施 — 2026-09-29
- commit 范围：`614659d`..本节所在提交（首次 push 时 HEAD 为 `9c1a2b3`）；CI：https://github.com/2181385109/fund-research-agent/actions/runs/36454732880 （✅ 全绿：backend 33s、python×3 各约 15s、scripts + security scan 11s）
- 仓库：https://github.com/2181385109/fund-research-agent （公开，MIT）

### 完成项
- **第 0 步环境**：`.wslconfig` 在 `[wsl2]` 下追加 `memory=10GB`、`swap=4GB` 和一行说明注释，ticket-qa 原有的 `vmIdleTimeout` 与注释原样保留；`wsl --shutdown` → `wscript.exe D:\tools\wsl-keepalive.vbs`。先写 `.gitignore`（含 `.env`、`data/raw/`、`data/snapshots/`）和 `.env.example`，再用 `scripts/gen_env.py` 生成 `.env`：7 个密钥类变量（MySQL root / 应用 / fund_reader 密码、Redis 密码、内部回调密钥、JWT 密钥、LLM key）全部由脚本写入，脚本只输出变量名。
- **仓库**：git init（main）、`.gitattributes`（`* text=auto eol=lf`）、MIT LICENSE（YaoYinJie）、README 骨架（一句话介绍、PLAN §1 架构图、进度表、两句固定声明）；目录结构按 CLAUDE.md §3，未实现模块放占位 README（Python / 顶层目录）或 `package-info.java`（Java 包，ADR-022），注明所属阶段。
- **docker-compose.yml**（默认只起 infra）：`mysql:8.4.11`、`redis:8.10.2`、`fra/elasticsearch-ik:8.19.22`（`deploy/elasticsearch/Dockerfile`，官方 8.19.22 + IK 8.19.22）、`milvusdb/milvus:v2.5.27`（standalone，内嵌 etcd + 本地存储，参数取自官方 standalone_embed.sh）。每个服务都有 mem_limit（按 PLAN §3）、healthcheck、具名卷、`restart: unless-stopped`；宿主机端口从 `.env` 读，默认值按 PLAN §2.2。MySQL 初始化脚本建 `fra_app` / `fund_data` 两个库、`fra_app` 应用账号（只有 fra_app 权限）与 `fund_reader`（fund_data 只读）。Redis Query Engine 已确认（ADR-007）；ES 本地关闭安全认证（ADR-006）。
- **backend**：Spring Boot 3.5.16 / JDK 17 / groupId `com.fundagent`；`GET /api/health` 检查 MySQL + Redis，每项 UP/DOWN + 耗时，任一 DOWN 返回 503；`ApiResponse<T>` + `ErrorCode` 枚举 + `@RestControllerAdvice` 全局异常 + requestId 过滤器（MDC + 响应头）；Flyway `V1__baseline.sql`；`@ConfigurationProperties` 配置；单测 5 个（health 全 UP 200、redis DOWN 503、mysql DOWN 503，依赖用 Mockito mock；全局异常 2 个）。
- **Python 三个包**（各自 `.venv`，ADR-017；pyproject + Python 3.12 + pydantic-settings + ruff）：ai-service `GET /health` 并发探测 Milvus / ES / Redis，每项带超时，任一 DOWN 返回 503（单测 9 个，依赖用 fake 与 httpx.MockTransport）；mcp-tools、data-pipeline 只有配置骨架（单测 2 个、3 个），无业务逻辑。
- **scripts**：`llm_smoke.py`（/models → 流式 → tool call，写 `reports/smoke/<UTC>/summary.json`，失败即停不重试）、`security_scan.py`（tracked / `--history` 两种模式）、`gen_env.py`；离线单测 22 个。
- **CI**：`.github/workflows/ci.yml`：backend temurin 17 `mvn -B verify`；三个 Python 包矩阵跑 `ruff check`、`ruff format --check`、`pytest -m "not integration and not slow and not live"`；scripts job 跑单测 + 两种模式的安全扫描；Maven / pip 缓存已配。CI 不下载模型、不调 LLM。
- **文档**：`docs/SETUP.md`（前置条件、.wslconfig 与 10GB 的理由、经 wsl 启动的可复制命令、端口表、排障含 keepalive 恢复和与 ticket-qa 共存的内存建议）；`docs/DECISIONS.md`（ADR-001~014 登记 PLAN §2 选型，ADR-015~022 为本阶段决定）。

### 验收逐条

原始证据文件都在 `reports/infra/20260928T165734Z/`（`summary.json` 带 git commit、机器信息、命令行），LLM 冒烟在 `reports/smoke/20260928T163850Z/summary.json`。

**1. 4 个 infra 服务 healthy — ✅**
证据：`reports/infra/20260928T165734Z/compose_ps_and_docker_stats.txt`
```
$ docker compose ps
NAME                IMAGE                          COMMAND                  SERVICE         CREATED          STATUS                    PORTS
fra-elasticsearch   fra/elasticsearch-ik:8.19.22   "/bin/tini -- /usr/l…"   elasticsearch   13 minutes ago   Up 13 minutes (healthy)   9300/tcp, 0.0.0.0:9201->9200/tcp, [::]:9201->9200/tcp
fra-milvus          milvusdb/milvus:v2.5.27        "/tini -- milvus run…"   milvus          7 minutes ago    Up 7 minutes (healthy)    0.0.0.0:9091->9091/tcp, [::]:9091->9091/tcp, 0.0.0.0:19530->19530/tcp, [::]:19530->19530/tcp
fra-mysql           mysql:8.4.11                   "docker-entrypoint.s…"   mysql           9 minutes ago    Up 9 minutes (healthy)    33060/tcp, 0.0.0.0:3307->3306/tcp, [::]:3307->3306/tcp
fra-redis           redis:8.10.2                   "docker-entrypoint.s…"   redis           12 minutes ago   Up 5 minutes (healthy)    0.0.0.0:6380->6379/tcp, [::]:6380->6379/tcp
```

**2. `docker stats --no-stream` 原文并与 PLAN §3 预算对照 — ✅（ES 贴近上限，见「给统筹的问题」1）**
证据：同上文件（采样 2026-09-29 00:57:34 +0800，backend/ai-service 未运行，infra 空载无数据，n=1 次快照）
```
CONTAINER ID   NAME                  CPU %     MEM USAGE / LIMIT     MEM %     NET I/O           BLOCK I/O        PIDS
4ce5254ddaad   fra-milvus            3.92%     330.9MiB / 2GiB       16.16%    8.44kB / 8.22kB   226MB / 3.7MB    47
c4835e8d3d2b   fra-mysql             0.50%     224.8MiB / 512MiB     43.91%    111kB / 149kB     87.4MB / 287MB   47
ffbb02f64f5e   fra-redis             0.14%     6.359MiB / 256MiB     2.48%     4.97kB / 7.73kB   4.1kB / 4.1kB    6
439bb899104d   fra-elasticsearch     4.01%     1.423GiB / 1.5GiB     94.89%    12.8kB / 14.9kB   4.1kB / 1.29MB   136
8d6568ea620a   ticketqa-rabbitmq     0.15%     177.1MiB / 9.711GiB   1.78%     30.2kB / 27.2kB   65.3MB / 209kB   56
dab4fcbc50ae   ticketqa-redis        0.26%     10.68MiB / 9.711GiB   0.11%     80.8kB / 40.4kB   31MB / 8.19kB    6
3a3ea2182c8c   ticketqa-wiremock     0.07%     169.7MiB / 9.711GiB   1.71%     2.31kB / 126B     131MB / 1.39MB   40
75e94d85ce99   ticketqa-mysql        0.47%     442MiB / 9.711GiB     4.45%     393kB / 359kB     234MB / 17.3MB   58
1bd59405624f   ticketqa-prometheus   0.00%     44.4MiB / 9.711GiB    0.45%     25.3kB / 29.4kB   140MB / 983kB    18
```

| 服务 | PLAN §3 mem_limit | 实测 | 占上限 |
|---|---|---|---|
| mysql | 512m | 224.8MiB | 43.9% |
| redis | 256m | 6.4MiB | 2.5% |
| elasticsearch | 1536m（堆 1g） | 1457.2MiB | **94.9%** |
| milvus | 2g | 330.9MiB | 16.2% |
| **本项目 infra 合计** | 4352MiB（S0 无 Kafka；PLAN 的 5.3g 含 S11 的 Kafka 1g） | **2019.3MiB（≈1.97GiB）** | 46.4% |
| ticket-qa（单独列出，未设 mem_limit） | — | 843.9MiB（mysql 442.0、rabbitmq 177.1、wiremock 169.7、prometheus 44.4、redis 10.7） | — |

远低于「infra 合计超过 6.5g」的停止线。ES 的 94.9% 另查了 cgroup：`anon=1443MiB, file=0`，JVM `heap_max=1024MiB`（ES 默认 AlwaysPreTouch，堆启动即整块常驻）、`non_heap=197MiB`、`direct=16MiB`，`oom_kill=0`。也就是说这是空载时的常驻内存，不是 page cache；有负载时 direct buffer 增长可能触发 OOM（见「给统筹的问题」1）。同时 `free -h`：Mem total 9.7Gi，used 3.3Gi，available 6.4Gi，Swap used 164Ki。

**3. IK `_analyze`「基金管理人的管理费率」ik_max_word / ik_smart — ✅（IK 装上了，未退回 smartcn）**
证据：`reports/infra/20260928T165734Z/es_ik_and_redis_modules.txt`（原文是 `?pretty` 格式，这里压缩成一行一个 token）
```
$ curl -s localhost:9201 → "number" : "8.19.22", "lucene_version" : "9.12.2"
$ GET _cat/plugins → 439bb899104d analysis-ik 8.19.22
$ POST _analyze {"analyzer":"ik_max_word","text":"基金管理人的管理费率"}
  基金[0-2,CN_WORD] 管理人[2-5,CN_WORD] 管理[2-4,CN_WORD] 人[4-5,CN_CHAR] 的[5-6,CN_CHAR] 管理费[6-9,CN_WORD] 管理[6-8,CN_WORD] 费率[8-10,CN_WORD]   （8 个 token）
$ POST _analyze {"analyzer":"ik_smart","text":"基金管理人的管理费率"}
  基金[0-2,CN_WORD] 管理人[2-5,CN_WORD] 的[5-6,CN_CHAR] 管理[6-8,CN_WORD] 费率[8-10,CN_WORD]   （5 个 token）
```

**4. `fund_reader` 执行 INSERT 被拒，SELECT 可执行 — ✅**
证据：`reports/infra/20260928T165734Z/mysql_privileges.txt`。fund_data 在 S0 没有业务表，所以由 root 临时建探针表 `_s0_privilege_probe`，验证后已删除（`SHOW TABLES FROM fund_data` 为空）。
```
$ [fund_reader] SHOW GRANTS
| GRANT USAGE ON *.* TO `fund_reader`@`%`            |
| GRANT SELECT ON `fund_data`.* TO `fund_reader`@`%` |
$ [fund_reader] SELECT * FROM fund_data._s0_privilege_probe
|  1 | probe |
$ [fund_reader] INSERT INTO fund_data._s0_privilege_probe VALUES (2, "x")
ERROR 1142 (42000) at line 1: INSERT command denied to user 'fund_reader'@'127.0.0.1' for table '_s0_privilege_probe'
$ [fund_reader] UPDATE ... → ERROR 1142 (42000) ... UPDATE command denied ...
$ [fund_reader] DELETE ... → ERROR 1142 (42000) ... DELETE command denied ...
$ [fund_reader] CREATE TABLE fund_data.t (id INT) → ERROR 1142 (42000) ... CREATE command denied ...
$ [fund_reader] SELECT COUNT(*) FROM fra_app.flyway_schema_history → ERROR 1142 (42000) ... SELECT command denied ...
$ [fra_app] SHOW GRANTS
| GRANT ALL PRIVILEGES ON `fra_app`.* TO `fra_app`@`%` |
$ [fra_app] SELECT * FROM fund_data._s0_privilege_probe → ERROR 1142 (42000) ... SELECT command denied ...
```

**5. 两个 health 接口依赖全部 UP；停 redis 后 backend 返回 503，再恢复 — ✅**
证据：`reports/infra/20260928T165734Z/health_up_and_redis_down.txt`、`health_after_redis_restore.txt`
```
$ curl -s -i http://127.0.0.1:8081/api/health
HTTP/1.1 200
{"code":0,"message":"ok","data":{"status":"UP","components":{"mysql":{"status":"UP","latencyMs":1},"redis":{"status":"UP","latencyMs":0}}},"requestId":"e317d32686e048dca1b0cf209cba15e9"}
$ curl -s -i http://127.0.0.1:8001/health
HTTP/1.1 200 OK
{"status":"UP","components":{"milvus":{"status":"UP","latency_ms":15,"error":null},"elasticsearch":{"status":"UP","latency_ms":15,"error":null},"redis":{"status":"UP","latency_ms":8,"error":null}}}
$ docker compose stop redis
$ curl -s -i http://127.0.0.1:8081/api/health
HTTP/1.1 503
{"code":50300,"message":"依赖服务不可用","data":{"status":"DOWN","components":{"mysql":{"status":"UP","latencyMs":1},"redis":{"status":"DOWN","latencyMs":2012,"error":"QueryTimeoutException: Redis command timed out"}}},"requestId":"4f143c0d1b3940f5adc8935b9cc50df6"}
$ curl -s -i http://127.0.0.1:8001/health
HTTP/1.1 503 Service Unavailable
{"status":"DOWN","components":{"milvus":{"status":"UP","latency_ms":49,"error":null},"elasticsearch":{"status":"UP","latency_ms":8,"error":null},"redis":{"status":"DOWN","latency_ms":2014,"error":"timeout after 2.0s"}}}
$ docker compose start redis   → fra-redis Up 6 seconds (healthy)
$ curl ai-service /health → HTTP/1.1 200 OK（redis UP，latency_ms 8）
$ curl backend /api/health → 仍为 HTTP 503（redis DOWN，2007ms 超时）
backend.log：00:52:38.524 ReconnectionHandler: Reconnected to 127.0.0.1/<unresolved>:6380
00:52:50 轮询 backend /api/health → HTTP 200，redis UP latencyMs 1
```
如实说明：这次 redis 停了约 1 分钟（我的恢复脚本第一次有引号错误，没启动成功，重跑后才启动），Lettuce 按指数退避重连（00:52:04、:13、:22、:38 各试一次），所以 redis healthy 后 backend 还报了约 15 秒 DOWN，之后自动恢复。同一流程的前一次演练（redis 只停约 10 秒）恢复后立即 200。

**6. llm_smoke 流式与 tool call 都成功 — ✅**
证据：`reports/smoke/20260928T163850Z/summary.json`（git_commit `6a01852`、dirty=false；不含 key，已用脚本核对 key 字符串不在文件中）
```
[models] ['deepseek-flash', 'deepseek-v4-pro']
[stream] requested=deepseek-flash response=['deepseek-flash'] first_token=751.8ms total=1102.8ms usage={'prompt_tokens': 14, 'completion_tokens': 42, 'total_tokens': 56, ...}
[tool_call] requested=deepseek-flash response=deepseek-flash tool=get_latest_nav args={'share_code': '110022'} total=1702.8ms usage={'prompt_tokens': 297, 'completion_tokens': 42, 'total_tokens': 339, ...}
[PASS] summary → reports/smoke/20260928T163850Z/summary.json
```
请求模型名 `deepseek-flash`，响应 model 字段 `deepseek-flash`（流式和 tool call 都是）；首 token 751.8ms（首个 SSE chunk 229.2ms）；思考模式 `disabled`（ADR-018）。

**7. 首次 CI 全绿；安全扫描两种模式通过 — ✅**
CI：https://github.com/2181385109/fund-research-agent/actions/runs/36454732880
```
✓ main ci · 36454732880
✓ scripts + security scan in 11s
✓ python (data-pipeline) in 16s
✓ backend (mvn verify) in 33s
✓ python (mcp-tools) in 15s
✓ python (ai-service) in 15s
```
首次 push 前本地扫描（`reports/infra/20260928T165734Z/security_scan.txt`）：
```
$ python scripts/security_scan.py
security_scan mode=tracked env_secrets_checked=7 findings=0 → PASS
exit=0
$ python scripts/security_scan.py --history
security_scan mode=history env_secrets_checked=7 findings=0 → PASS
exit=0
```
如实说明：第一次跑扫描时发现 `backend/README.md:10` 含本机路径（`/d/tools/jdk-17`）。当时还没 push，所以我改了文件，并在本地把后续 5 个未推送的提交重新 cherry-pick 到修正后的提交上，让 `--history` 也干净。push 之后没有改写过历史。

**8. 第 0 步证据 — ✅**
证据：`reports/infra/20260928T165734Z/step0_wsl.txt`、`step0_check_ignore.txt`（WSL 重启后立即采集，此时 ticket-qa 容器正在自动恢复）
```
$ wsl -l -v
  NAME            STATE           VERSION
* Ubuntu-24.04    Running         2
$ wsl -d Ubuntu-24.04 -u root -- free -h
               total        used        free      shared  buff/cache   available
Mem:           9.7Gi       1.5Gi       6.8Gi        22Mi       1.6Gi       8.3Gi
Swap:          4.0Gi          0B       4.0Gi
$ git check-ignore -v .env
.gitignore:2:.env	.env
```
（改之前的 `free -h`：Mem total 7.4Gi，Swap 2.0Gi。）

### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
|---|---|---|---|
| 本项目 infra 内存合计（空载） | 2019.3MiB | 1 次快照，4 个容器 | reports/infra/20260928T165734Z/summary.json |
| elasticsearch 内存 / 上限 | 1457.2MiB / 1536MiB（94.9%） | 1 次快照 | 同上 |
| milvus / mysql / redis 内存 | 330.9 / 224.8 / 6.4 MiB | 1 次快照 | 同上 |
| ticket-qa 内存合计 | 843.9MiB | 1 次快照，5 个容器 | 同上 |
| LLM 流式首 token / 总耗时 | 751.8ms / 1102.8ms | n=1 | reports/smoke/20260928T163850Z/summary.json |
| LLM tool call 总耗时 | 1702.8ms | n=1 | 同上 |
| LLM token 用量 | 流式 56、tool call 339 | n=1 | 同上 |
| health 探测耗时（全 UP） | backend mysql 1ms / redis 0ms；ai-service milvus 15ms / es 15ms / redis 8ms | n=1 | reports/infra/20260928T165734Z/health_up_and_redis_down.txt |
| 单测 | backend 5、ai-service 9、mcp-tools 2、data-pipeline 3、scripts 22，全部通过，0 skip | 41/41 | CI run 36454732880 |

延迟都是单次测量，不代表稳定值。

### 与计划的偏差（附理由和 ADR 编号）
1. **新增环境变量 `LLM_THINKING`**（默认 `disabled`），CLAUDE.md §5 的变量清单里没有。原因：DeepSeek 当前模型默认开启思考模式，开启时多轮 tool call 必须回传 `reasoning_content`（ADR-018）。
2. **`JUDGE_MODEL` 默认 `deepseek-v4-pro`**，与被测的 `deepseek-flash` 不同源（ADR-018）。S8 才使用，统筹可改。
3. **MySQL 配置用 compose `command` 传参，没用 `conf.d`**，并关闭了 `performance_schema`（ADR-016）。
4. **mysql、milvus 镜像不是 `docker pull` 拉的**：在 Windows 侧按 registry API 下载后 `docker load`，镜像 digest 与 Docker Hub 清单一致（ADR-015，拉取报错原文在 ADR 里）。镜像版本本身没有偏离。
5. Java 未实现的包用 `package-info.java` 占位，没放 README（ADR-022）。
6. 新增 `reports/infra/` 类别存放 S0 的 infra 证据（CLAUDE.md §7 列举的类别里没有，属于「…」范围）。
选型本身（mysql 8.4 / redis 8 / ES 8 + IK / milvus 2.5 standalone）没有偏离 PLAN，IK 可用，没有退回 smartcn。

### 已知问题 / 技术债
1. ES 空载已占 mem_limit 的 94.9%（见「给统筹的问题」1）。
2. backend 在 Redis 恢复后要等 Lettuce 退避重连，最长约十几秒才回到 UP（ADR-019）。
3. Flyway 启动时警告「Flyway upgrade recommended: MySQL 8.4 is newer than this version of Flyway」，迁移本身成功（ADR-009）。
4. ai-service 单测有 1 条 `StarletteDeprecationWarning`（starlette TestClient 基于 httpx 的用法已弃用，提示改装 `httpx2`），不影响结果，S5 写 SSE 测试时一并处理。
5. CI 注解：`actions/checkout@v4`、`setup-python@v5`、`setup-java@v4`、`upload-artifact@v4` 基于 Node 20 已弃用（被强制跑在 Node 24 上），`setup-java@v4` 提示迁移到 v5。目前只是警告。
6. WSL 里 Docker 默认镜像加速（先走 docker.1ms.run）很慢，DaoCloud 对未缓存层返回 `unavailable`；没有改 `/etc/docker/daemon.json`，因为会影响 ticket-qa（SETUP §4.1 写了绕过办法）。
7. `pyproject.toml` 只写了依赖下限，没有锁文件（ADR-010）。

### 需要用户做的事
1. **ticket-qa 的容器仍在运行**（WSL 重启后自动恢复，我没有停）。S0 实测各占：mysql 442.0MiB、rabbitmq 177.1MiB、wiremock 169.7MiB、prometheus 44.4MiB、redis 10.7MiB，合计 843.9MiB。本项目 infra 空载约 2.0GiB，两者现在可以共存（WSL 可用内存 6.4Gi）。S5 起要加载本地模型，S7 要起全栈（预算 8.9g），到时建议先停：`wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/ticket-qa/ops && docker compose stop'`。停不停由你决定。
2. （可选）用自己的浏览器打开证监会基金电子披露网站，告诉统筹能否访问。

### 给统筹的问题
1. **ES 内存余量**：空载常驻 1457MiB / 1536MiB（94.9%）。堆 1g 是启动时整块预占的，non-heap 约 197MiB，direct buffer 目前 16MiB，但 ES 默认 direct 上限是堆的一半（512MiB）。S2 批量写入和 S4 检索时有被 OOM kill 的风险。可选：(a) 把 ES mem_limit 调到 1792m（infra 预算 5.3g → 约 5.55g，PLAN §3 要改）；(b) 在 `ES_JAVA_OPTS` 加 `-XX:MaxDirectMemorySize=256m`，上限不变（偏离提示词「ES_JAVA_OPTS=-Xms1g -Xmx1g」原文）；(c) 维持现状，S2 入库时用 `docker stats` 与 `memory.events` 观察。我倾向 (b)，但它改动了 PLAN 规定的参数，所以请你决定，本阶段没动。
2. **S1 导入 fund_data 需要写账号**：现在 `fund_data` 只有 root（全部权限）和 `fund_reader`（只读）。S1 的 `fund_pipeline.load` 我打算新增一个 `fund_loader` 账号（只有 fund_data 的 DDL/DML 权限），密码由 gen_env 生成（登记 ADR）。可以吗，还是直接用 root？
3. **`LLM_THINKING` 变量与 `JUDGE_MODEL=deepseek-v4-pro`**（偏差 1、2）是否同意？如同意，建议把 `LLM_THINKING` 补进 CLAUDE.md §5 的变量清单（执行者不改 CLAUDE.md）。
4. **Docker 镜像加速顺序**：要不要请用户同意把 `/etc/docker/daemon.json` 的 `registry-mirrors` 改为 DaoCloud 优先？这会影响 ticket-qa 的拉取（大概率是变快），需要 `systemctl restart docker`，会重启所有容器。

> 统筹答复（B1 提示词，2026-09-29）：1 → 1792m + MaxDirectMemorySize=256m；2 → 新增 fund_loader；3 → 维持；4 → 不改。已在 B1 落实，见 ADR-023～026 与 `reports/infra/20260928T174514Z/s0_followup.txt`。

---

## S1 基金池与数据采集 — 2026-09-29
- commit 范围：`69c90d2`..`d0ff86c`；CI：https://github.com/2181385109/fund-research-agent/actions/runs/36517568380 （✅ 5 个 job 全绿）
- 用户关卡：基金池 v1 于 **2026-09-29** 经用户确认冻结（LOF 按场外份额收录、011832 保留，ADR-030）

### 完成项
- 先落实 S0 遗留决定（ADR-023～026）：ES 1792m + direct 256m；`fund_loader` 账号（`deploy/mysql/init/02-fund-loader.sh`，`gen_env.py --add-missing`）。
- `fund_pipeline`：`net`（节流/缓存/UA）、`sources`（AkShare 1.18.97 + 缓存 + 临时错误重试）、`universe`（筛选/校验）、`docs`（公告匹配 + 断点续传下载 + 可提取性）、`structured`（9 张 AkShare 表 + 3 张 PDF 表）、`pdf_extract`/`pdf_tables`、`schema.sql`/`load`、`quality`。单测 83 个，全部离线（自造 reportlab PDF fixture）。
- 数据：DATA_AS_OF = **2026-09-28**（下载时北京时间 09-29 上午，当日未收盘）；100 份 PDF（6834 页，约 93MB，不入库）；12 张表导入 `fund_data`。

### 验收逐条
1. 用户确认基金池（记录日期）；MANIFEST 中 PDF 数量 ≈ 基金数 × 5，每个缺口都有原因 — ✅ — 2026-09-29 确认；`data/MANIFEST.json`：期望 100，下载且可提取 100，`documents_missing` 为空（首次运行有 2 个下载失败：连接中断和读超时，都记进了 missing；续传后补齐）。可提取性：平均每页字符数最低 461.6，乱码率最高 0.0038。
2. `fund_reader` 能查到所有表；各表行数原文 — ✅ — `reports/data_load/20260929T032617Z/summary.json`：12 张表 `fund_reader` COUNT(*) 与 CSV 完全一致（funds 20、share_classes 40、fees 40、purchase_fee_tiers 92、redemption_fee_tiers 137、nav_daily 72836、dividends 14、managers 24、fund_manager_tenures 34、holdings_top10 1200、fund_scale 40、period_returns 360）。
3. 质量报告原文：持仓比对一致率（写明分母）；每处不一致都给出解释 — ✅ — `reports/data_quality/20260929T032645Z/report.md`：持仓 **200/202 一致（20 只基金，分母为 PDF 明细行）**，2 行差异是同一发行人的 A 股和 H 股在季报里共用一个序号（013840 华虹宏力、012650 中芯国际），逐条解释见报告 §3；现任经理 19/20 一致（012650 于 2026-07-15 换人，晚于季报期末）；净值缺口 451 天全部在成立后 3 个月的建仓封闭期内；区间收益按分红再投资口径自算，**276/276 在 ±0.01pp 内**。
4. 爬取节流和缓存有测试或证据；重复执行不会重复下载 — ✅ — 单测：`test_throttle_enforces_min_interval`、`test_json_cache_second_call_does_not_fetch`、`test_akshare_source_throttles_and_caches`、`test_download_skips_existing_file_and_resumes_part`（Range 续传）。实跑证据：第三次 `docs fetch` 统计 `{'downloaded': 0, 'reused_local': 100, 'announcement_list_requests': 0, 'announcement_list_cache_hits': 40}`（写在 `data/MANIFEST.json` 的 `documents_fetch_stats`）；`structured fetch` 重跑 `AkShare 请求 0，缓存命中 283`。
5. 单测：文档类型与报告期匹配、截断到 as_of、费率表解析（fixture） — ✅ — `tests/test_docs.py`（含中文数字年份「二0二五年」）、`tests/test_structured.py`（净值/分红/持仓截断）、`tests/test_pdf_extract.py`（reportlab 生成的招募说明书：养老金表与一般表、C 类不收申购费；合表与两列并列版式；正文退路）。
6. CI 全绿；安全扫描确认仓库内没有真实 PDF 或快照 — ✅ — CI run 36517568380 全绿；`security_scan` tracked 与 `--history` 均 PASS（检查了 8 个密钥变量）；`git ls-files` 中没有 `.pdf`、`data/raw`、`data/snapshots`。

### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
|---|---|---|---|
| 候选筛选 | 474 组 → 193 组通过（规模不足 159、成立不足 2 年 119、成立日缺失 3） | 474 组 | reports/universe/20260928T181640Z/summary.json |
| PDF 匹配与可提取 | 100 / 100 | 20 只 × 5 份 | data/MANIFEST.json |
| 持仓一致率 | 200/202（99.01%） | PDF 明细 202 行，20 只 | reports/data_quality/20260929T032645Z/summary.json |
| 现任经理一致 | 19/20 | 20 只 | 同上 |
| 区间收益（分红再投资口径）在 ±0.01pp 内 | 276/276 | 276 对 | 同上 |
| 净值缺口（成立 3 个月后） | 0 天 | 40 个份额 | 同上 |
| 耗时 | 未精确计时（PDF 下载受代理限速，约每秒十几 KB） | — | — |

### 与计划的偏差（附理由和 ADR 编号）
1. 申购费从招募说明书解析，没用 `fund_fee_em`（AkShare 缺陷，用户确认；ADR-027、LIMITATIONS）。
2. 招募说明书和基金合同的公告列表直接调东方财富 JJGG 接口 type=1（AkShare 没封装；ADR-028）。
3. 表结构细化：`tier_text`、`fixed_fee`、`source_page`；单位统一（ADR-029）。
4. 新增 `fund_scale` 的来源是季报（各份额净值合计），不是 overview；`fund_manager_tenures` 另外用了经理变更公告（`data/raw/personnel/`，14 份，不入库，URL 和 sha256 记在 MANIFEST）。
5. data-pipeline 的 E501 行宽上限改为 120（中文按双宽计），formatter 仍是 100。

### 已知问题 / 技术债
1. 4 份 2025 年报的经理简介表没有解析出来（006113、110023、014193、002236）；任职信息由季报和公告覆盖，006113 郑磊的任职起始日为 NULL（报告 §6）。
2. PDF 表格解析依赖版式启发式规则（3 种申购费版式、跨页续表）；换一批基金可能遇到新版式，解析不出时会登记缺口，不会猜测。
3. `fund_overview_em` 的规模只是 A 类份额（见给统筹的问题 1）。
4. 早期的 3 次质量 run 保留在 `reports/data_quality/`（解析器修正过程），以最后一次为准。

### 需要用户做的事
- 无新增（基金池已确认）。

### 给统筹的问题
1. **universe.yaml 的规模口径注释**：注释写的是「全部份额合计」，实际是 A 类份额的规模（数值与入选结果不受影响，≥2 亿门槛仍然满足）。要不要批准只改这一行注释（按 §6 要写 CHANGELOG，已预登记）？不改也不影响后续阶段。

---

## S2 文档入库流水线 — 2026-09-29
- commit 范围：`d0ff86c`..本节所在提交；CI：见 `docs/HANDOFF.md`
- 证据目录：`reports/ingest/20260929T040733Z/`（`summary.json`、`idempotency_and_delete.txt`、`sample_chunks.json`、`docker_stats_after_ingest.txt`）

### 完成项
- ai-service：`ingest/parsers/pdf.py`（pdfplumber，页码、表格转 markdown、页眉页脚去除）、`ingest/chunking.py`（章节感知 + 递归切分、规范文本 char offset、表格独立成块、上下文头 `text_ctx`）、`embedding/`（`Embedder` 接口、`BgeEmbedder`、`FakeEmbedder`、factory）、`stores/`（Milvus、ES、内存 fake）、`ingest/pipeline.py`（parse → chunk → embed → 两边删 → 两边写 → 核对计数）、`ingest/cli.py`（按 MANIFEST 批量）、HTTP 接口 `POST /v1/documents/ingest`、`DELETE /v1/documents/{doc_id}`、`GET /v1/stats`（`docs/API.md`）。设计见 ADR-032、ADR-033。
- 测试：离线单测 22 个（reportlab 自造 PDF + FakeEmbedder + 内存存储）；`-m integration` 1 个（真实 Milvus + ES，独立的 `*_it` 集合，本机通过）。

### 验收逐条
1. 单测：表格块、页眉页脚去除、章节路径、char offset 能从原文切回 chunk 文本、空文档和超长段落 — ✅ — `ai-service/tests/test_ingest.py`（11 个）。
2. 全量入库后 Milvus 与 ES 的 chunk 数一致，并等于流水线统计；按 doc_type 分组的统计和实测耗时 — ✅ — `summary.json`：100/100 份、失败 0；流水线 18353 = Milvus 18353 = ES 18353；按类型两边都是 prospectus 6540、contract 4447、annual_report 5169、quarterly_report 2197；表格块 3655；6834 页。耗时（单次运行）：总 1517s（解析切块 339s、embedding 1016s、写入 105s、初始化 24s）。
3. 幂等：重复入库 chunk 数不变；删除后两边为 0 — ✅ — `idempotency_and_delete.txt`（经 HTTP 接口）：003095 季报连续两次入库都是 57/57，总数保持 18353；DELETE 后 `remaining {"milvus": 0, "elasticsearch": 0}`，总数 18296；重新入库后恢复 18353。
4. 展示 3 个 chunk 的完整元数据 — ✅ — `sample_chunks.json`：费率表格块 `040025_prospectus_2026-07-27#0108`（p76，「八、基金份额的申购、赎回与转换 > （六）申购费用和赎回费用」）；持仓表格块 `003095_quarterly_report_2026Q2#0028`（p9，5.3.1 前十名股票投资明细）；正文块 `003095_quarterly_report_2026Q2#0019`（p6–7，「§4 管理人报告 > 4.4 报告期内基金的投资策略和运作分析」）。
5. CI 全绿（FakeEmbedder） — 见 HANDOFF。

### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
|---|---|---|---|
| 入库文档 | 100/100，失败 0 | MANIFEST 中可提取的 100 份 | reports/ingest/20260929T040733Z/summary.json |
| chunk 数（流水线 / Milvus / ES） | 18353 / 18353 / 18353 | — | 同上 |
| 全量入库耗时 | 1517s（embedding 1016s） | 1 次运行，CPU，bge-small-zh-v1.5，正文与上下文头两组向量 | 同上 |
| embedding 吞吐（基准） | 约 23.8 条/秒（约 500 字的文本） | 256 条，1 次 | 未存文件（B1 对话内测得），仅供参考 |
| 入库后内存 | ES 1.531GiB/1.75GiB（87.5%）、Milvus 588.5MiB、MySQL 253.8MiB | 1 次快照 | docker_stats_after_ingest.txt |

### 与计划的偏差（附理由和 ADR 编号）
1. Milvus 存两个向量字段（正文、带上下文头），ES 存 `text` 与 `text_ctx`，S4 切上下文头开关时不用重新入库（ADR-032）。
2. `sentence-transformers` 放在 `[model]` extra，CI 不装（ADR-033）。
3. 全量入库时 S2 代码还没提交（`summary.json` 里 `git_commit=d0ff86c`、`git_dirty=true`）；入库代码随后在本节所在提交里原样提交，之后只改了 BGE 的离线加载开关和一处弃用 API。

### 已知问题 / 技术债
1. ES 入库后占 mem_limit 的 87.5%（空载 81%）；S4 检索压测时要继续观察。
2. bge-small-zh 最大 512 token，长表格块只有前约 500 字参与向量（ADR-032 后果）。
3. 跨页表格切成了两块（各页一块），第二块没有表头行；S4 若发现表格召回差，再考虑续表合并。
4. `scripts/ingest_evidence.py` 是依赖运行中服务的取证脚本，没有单测。

### 给统筹的问题
- 无。

---

## S3 评测集 v1 — 2026-09-29
- commit 范围：`4445b18`..本节所在提交（草案 `ee29d39`，按抽检意见修改 `b31982d`，冻结在本节所在提交）；CI：ee29d39 run 36523663880 全绿，冻结提交的 CI 见 `docs/HANDOFF.md`
- 证据：`reports/dataset_validation/20260929T074332Z/`（最终校验，git b31982d、干净工作区）、`reports/dataset_overlap/20260929T074333Z/`、`reports/data_quality/20260929T045331Z/`（费率核对）；抽检表与结论 `eval/datasets/review/spotcheck_v1.md`

### 完成项
- 统筹决定落实：universe.yaml 规模注释改为「A 类份额规模，截至 2026-06-30」，基金池本身不变，`data/CHANGELOG.md` 已登记，MANIFEST 的 `universe_sha256` 已同步（`4445b18`）。
- `eval/` 独立成包（ADR-034）：参考脚本 `reference/gold.py`（pandas 读快照）、`doc_facts.py`（pdfplumber 原文抽取）、出题模板与手写题、`build_datasets.py`（确定性构建，冻结后拒绝覆盖）、`validate_dataset.py`、`rules.py`（内容规则 a–d）、词面重叠、抽检表、冻结脚本。CI 矩阵加了 `eval`。
- `fund_qa_v1.jsonl` 112 题、`agent_tasks_v1.jsonl` 66 题；`SCHEMA.md`（字段、gold 约定、口径、内容规则、命中规则、第三期如何转训练对、test 不进训练）、`MANIFEST.json`（frozen=true）、`CHANGELOG.md`。
- 证据命中规则的参考实现与共享测试向量 `eval/reference/evidence_cases.json`，S4 在 ai-service 里的实现要通过同一组用例。

### 验收逐条
1. 校验通过，附分布表和词面重叠报告 — ✅ — `reports/dataset_validation/20260929T074332Z/report.md`：错误 0；PDF 逐字校验 152/152 条引文；gold_sql 35/35 在 fund_data 上的执行结果与 pandas 参考值一致；内容规则 a–d 错误 0；引文在入库 chunk 中可达 152/152（整条落在单个 chunk 内 135/152）。各 topic 的 dev/test 分布见同一报告。词面重叠见 `reports/dataset_overlap/20260929T074333Z/report.md`。
2. 用户抽检 ≥25 条（两个文件都抽到） — ✅ — 27 条（fund_qa 16、agent_tasks 11，按 `reference/spotcheck.py` 的固定规则分层抽样）：**通过 23，需修改 4（qa-0091、agent-0049、agent-0047、agent-0029），判错 0**。4 条都按用户给的方案修改了，结论写进了 `reference/items/spotcheck_v1_results.yaml` 和各题的 `verified_by`。
3. 抽检通过后冻结，sha256 写入 MANIFEST；冻结前没用它跑任何检索或 Agent — ✅ — `eval/datasets/MANIFEST.json`：fund_qa_v1 `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d`，agent_tasks_v1 `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77`。冻结前只从 ES 导出了 chunk 文本，用来做引文可达性诊断（只读索引内容，没有用题目做检索）。

### 全量扫描（按抽检暴露的问题，用户要求）
新增的 4 条规则都写进了 `validate_dataset.py`（`reference/rules.py`，单测 `eval/tests/test_rules.py`）：a 答案里的数字和实体必须有引文或 gold_sql 支撑；b 份额级表的 gold_sql 必须用字面量限定份额；c 题面日期必须是交易日，否则写明规则；d 收益、年化、回撤类题必须写明口径。
全量扫描另外修改了 **32 条**（不含抽检的 4 条；与草案逐题对比）。按类别计，一条可能属于多类：
| 类别 | 条数 | 条目 |
|---|---|---|
| a 支撑（补引文 / 延长引文 / SQL 附带支撑列 / 删去无出处的表述） | 18 | qa-0037、0050、0053、0056、0067、0073、0081；agent-0003、0004、0008、0011、0012、0013、0015、0022、0060、0062、0063 |
| b 份额限定 | 0 | 其余 gold_sql 原本都限定了 share_code / share_class（规则本身的单测发现 JOIN 条件被误当成限定，已修正规则） |
| c 非交易日 | 3 | agent-0024（2026-01-01）、0026（2025-09-28）、0027（2023-01-01、2023-12-31）：题面写明「遇非交易日取前一交易日净值」 |
| d 口径 | 16 | qa-0046、0048、0051、0056、0089、0093、0096；agent-0008、0024、0026、0027、0028、0030、0031、0043、0046 |
- 专考非交易日的收益计算题保留了 3 道（agent-0024、0026、0027）。
- 收益口径统一为「遇非交易日取前一交易日净值；年化 = (1+区间收益率)^(365/自然日天数)−1」，写进了 SCHEMA.md。快照中有 184 条周末披露的季末或年末净值，不算交易日（ADR-036）。agent-0027 实际使用的起止日因此变化，标准答案 -6.18% 不变。
- 剩下 5 条「文字要点词面覆盖低」的提示（qa-0092 三条、qa-0102 两条）已人工复核：股票名都在对应的持仓行引文里，是「一季度末：」这类前缀拉低了覆盖率，不需要修改。
- 改题面导致两对题的 dev/test 互换（qa-0079 与 qa-0089，agent-0024 与 agent-0025），每类 dev 条数不变。

### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
|---|---|---|---|
| fund_qa_v1 | 112 题（dev 33 / test 79），paraphrase 54，覆盖 20/20 只基金 | 各 topic 均达到 PLAN 最低题数 | reports/dataset_validation/20260929T074332Z |
| agent_tasks_v1 | 66 题（dev 21 / test 45），gold_sql 35，volatile 5 | 同上 | 同上 |
| 引文逐字校验 | 152/152 | 全部 evidence | 同上 |
| gold_sql 与参考值一致 | 35/35 | 全部带 gold_sql 的题 | 同上 |
| 引文可达（命中规则） | 152/152；整条落在单个 chunk 内 135/152 | 18353 个入库 chunk | 同上 |
| 词面重叠（问题 vs 引文，字符二元组覆盖率，p50 / 均值） | keyword 0.200 / 0.211；paraphrase 0.129 / 0.147 | n=64 / 59（有 evidence 的题） | reports/dataset_overlap/20260929T074333Z |
| 季报业绩原文 vs 快照净值自算 | 11/11 一致 | 带 nav_check 的业绩题 | 各题 notes |
| 招募说明书费率 vs 快照 fees | 59/60 一致 | 20 只 × 3 项 | reports/data_quality/20260929T045331Z |

### 与计划的偏差（附理由和 ADR 编号）
1. eval 独立成包（ADR-034）；quote 按「去空白与竖线」口径比对，出题方式见 ADR-035。
2. PLAN S5 写的是「就近取前一个净值日」，按用户的抽检意见改为「前一交易日」，周末季末净值不算交易日（ADR-036）。S5 实现 `calc_fund_return` 时按 SCHEMA.md「口径」一节执行。
3. 题目的 provenance 分 `template_reference_script`（90 题）和 `llm_draft`（88 题，其中抽检通过或按意见修改后标为 `llm_draft_human_verified` 的 14 题）。没有让 DeepSeek 起草（ADR-035）。

### 已知问题 / 技术债
1. 001551（天弘中证医药100 C）的销售服务费：快照 0.20%，招募说明书 0.25%，只做了登记，没有改快照，也没有就此出题。
2. 部分季报的经理表、持仓表单元格在 pdfplumber 文本里串行，这些基金没出对应的模板题；经理题 11 道只来自 6 只基金。
3. 最终的重叠报告 summary 里 `git_dirty=true`：原因只是刚写出的校验报告目录还没被跟踪。之后 `git_dirty` 改为只看已跟踪文件。
4. latest_nav 的 5 道题在评测时实时抓取，结果会随时间变化（PLAN §4.3 的设计）。

### 需要用户做的事
- 无（抽检已完成）。

### 给统筹的问题
- 无。S1 的「规模口径注释」问题已按统筹决定处理。

## S2 补记：切块碎片化修复与重新入库 — 2026-09-29（B3）
- 起因与设计：ADR-037（统筹决定：S2 缺陷修复，不算调参）。新参数 `CHUNK_MIN_CHARS=150`，不足的正文块与相邻正文块合并，表格块不参与；标题后接超长段落时标题并入第一片。`text == canonical[char_start:char_end]` 仍成立（新增 3 个单测，ai-service 共 66 个离线单测通过）。
- 全量重新入库：`reports/ingest/20260929T083321Z/summary.json`：100/100 份、失败 0、耗时 1297s；流水线 13812 = Milvus 13812 = ES 13812（原 18353）；按类型 prospectus 5222、contract 3035、annual_report 4218、quarterly_report 1337；表格块 3655（不变）。
- 长度分布（`chunk_length_before.json` / `chunk_length_after.json`，脚本 `scripts/chunk_stats.py`，分母见文件）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| chunk 总数 | 18353 | 13812 |
| 正文块数 | 14698 | 10157 |
| 正文块 < 50 字 | 2191（14.9%） | 921（9.1%） |
| 正文块 < 100 字 | 4644（31.6%） | 1220（12.0%） |
| 正文块 < 150 字 | 6234（42.4%） | 1389（13.7%） |
| 正文块中位数 / P90 / 最大 | 202 / 577 / 609 | 435 / 598 / 750 |

  修复后仍有 921 个不足 50 字的正文块，抽看是紧贴表格的小节标题或表注（如「7.4.7.9 …单位：人民币元」「注：…」），它们的相邻块是表格，按设计不合并；没有再为此加规则。
- 重新验证：幂等 / 删除 / 恢复、3 个样例块已按新索引重跑（本目录 `idempotency_and_delete.txt`、`sample_chunks.json`；首次入库的证据仍在 `reports/ingest/20260929T040733Z/`，其中的 chunk 数和 chunk_id 对应修复前的切块）。评测集引文可达性：`reports/dataset_validation/20260929T083359Z`，152/152 可达，数据集文件未动（sha256 不变）。

## S4 混合检索 + 重排 + 检索评测 — 2026-09-29（B2–B3）
- commit 范围：`671c858`..本节所在提交；CI：见 `docs/HANDOFF.md`
- 证据目录：`reports/retrieval/`（dev 各 run、test 两次 run、`ablation_entity_filter_20260929/`），调参全过程 `docs/tuning_log.md`。

### 完成项
- `retrieval/`（实体识别、RRF、Milvus / ES 两路、5 种模式的 `RetrievalService`）、`rerank/`（`Reranker` 接口、`CrossEncoderReranker`、`NoopReranker`）、`POST /v1/retrieve`（`docs/API.md`）、`eval/`（指标、配对 bootstrap、runner）。
- 调参：切块修复后从 dev run 3 重新开始，按预先登记的协议走完 4 项（run 3–8），最终配置：实体过滤**关**、上下文头**开**、指令前缀**关**、每路召回 **20**、重排候选 20、top_n 10、RRF k=60。写入 `config.py` 默认值与 `.env.example`（`RETRIEVAL_*`）。
- test 上各只跑一次：run 9（最终配置）、run 10（消融：实体过滤开）。

### 验收逐条
1. 单测：RRF 手算用例、实体识别（含 A/C 份额代码和别名）、reranker 可替换、证据匹配规则 — ✅ — `ai-service/tests/test_retrieval.py`（`test_rrf_hand_computed`、`test_entity_recognition`、`test_reranker_is_replaceable_and_changes_order`）、`tests/test_eval_metrics.py`（`test_evidence_rule_matches_reference_vectors` 用 `eval/reference/evidence_cases.json` 逐例核对）。
2. test 上 5 种模式的结果表，每个数字都可追溯 — ✅ — 下表；原文 `reports/retrieval/20260929T090548Z/{summary.json,report.md,per_query.jsonl}`。
3. 主对比结论带 CI，并按 topic 拆分；结果不理想也如实写 — ✅ — 见下「主对比」与「分析」；逐 topic 的 CI 在同一 `report.md`。
4. test 的每次运行都已登记；per_query.jsonl 已保留 — ✅ — `docs/tuning_log.md` run 9、10；两个目录都有 `per_query.jsonl`。
5. CI 全绿 — 见 HANDOFF（push 后确认）。

### 实测数字
test 集：`fund_qa_v1` test 79 题，排除 unanswerable 7 题，**n = 72**（实体覆盖 72/72：识别出的基金都覆盖了标准答案的基金；实体过滤在最终配置里关闭）。最终配置。指标为 0–1 的比例，分母 72。

| 模式 | hit@1 | hit@5 | hit@10 | recall@10 | MRR@10 | nDCG@10 | p50 / p95 延迟 ms |
|---|---|---|---|---|---|---|---|
| vector | 0.0972 | 0.2500 | 0.3472 | 0.3403 | 0.1683 | 0.2004 | 12 / 16 |
| bm25 | 0.2500 | 0.5000 | 0.5833 | 0.5625 | 0.3504 | 0.3854 | 51 / 57 |
| hybrid（RRF） | 0.2222 | 0.5000 | 0.5972 | 0.5625 | 0.3319 | 0.3633 | 66 / 72 |
| vector_rerank | 0.3056 | 0.4583 | 0.4583 | 0.4444 | 0.3773 | 0.3729 | 2641 / 3873 |
| **hybrid_rerank** | **0.4306** | **0.6528** | **0.6528** | **0.6458** | **0.5176** | **0.5321** | 3750 / 4091 |

（延迟为 CPU、单进程串行、不含预热；重排模式的耗时几乎全在 CrossEncoder。）

**主对比**（vector → hybrid_rerank，同一组开关，配对 bootstrap 10000 次，种子 20260929）：nDCG@10 +0.3317，95% CI [+0.2395, +0.4281]；hit@5 +0.4028 [+0.2917, +0.5139]；MRR@10 +0.3493 [+0.2513, +0.4522]。按 topic（nDCG@10，差与 95% CI）：commentary（13）+0.639 [+0.430, +0.835]；contract_clause（12）+0.265 [+0.058, +0.483]；cross_doc（8）+0.410 [+0.150, +0.666]；fee（12）+0.278 [+0.059, +0.528]；holdings（11）+0.094 [+0.000, +0.278]；manager（8）+0.169 [+0.044, +0.319]；performance（8）+0.423 [+0.125, +0.750]。

**次要消融**（hybrid_rerank，实体过滤 关 → 开，run 9 → run 10，`reports/retrieval/ablation_entity_filter_20260929/`）：nDCG@10 0.5321 → 0.5543，差 +0.0222，95% CI [−0.0097, +0.0652]（含 0）；72 题中 nDCG 不同的只有 5 题。dev 上得出的「关更好」（+0.067）在 test 上没有复现，方向相反且都不显著。

**dev 调参**（n=30，hybrid_rerank nDCG@10）：run 3 基线 0.2880 → 实体过滤关 0.3554 → 上下文头关 0.0844（保留开）→ 指令前缀开 0.3548（不变）→ 每路 20：0.3874 / 每路 100：0.3274。全表见 tuning_log。

### 分析（结果不理想的部分，如实写）
- **绝对水平不高**：最好的 hybrid_rerank 在 72 题上 hit@5 = 0.65，nDCG@10 = 0.53；holdings 只有 0.126，vector 单路只有 0.20。
- **向量路很弱，且 hybrid（RRF）没有超过 bm25**（nDCG@10 0.3633 vs 0.3854）：bge-small-zh-v1.5 在 20 只基金、年报/招募说明书大段雷同的语料上区分力有限；RRF 把弱的向量路和较强的 BM25 等权融合，被向量路拖累。重排是提升的主要来源（vector → vector_rerank +0.17，hybrid → hybrid_rerank +0.17）。这一段是从分模式数字读出的解释，没有单独做消融验证。
- **holdings 最差**：查看 3 道失败题（`qa-0032/0033/0034`）：前几名多是同一基金的别的文档或别的报告期里的「前十名股票投资明细」表头或表格碎片（如只有一行、84 字的持仓表块），而标准证据是二季报里第 1 名那一行；这些块语义几乎相同（上下文头有基金名、文档名含报告期、章节，但表格行本身是数字，缺少可供语义区分的文字）。这是**观察到的现象，不是已验证的原因**；季报中 699 个表格块里有 58 个不足 100 字（按 `data/raw/_chunks.jsonl` 统计）。
- **评测口径较严**：命中要求 doc_id 相同且引文有 50% 连续片段落在命中的块里。费率题里很多前几名来自年报或招募说明书的同一费率条款，内容等价但 doc_id 不同，计为未命中（例：`qa-0001` 的标准证据在招募说明书第 68 页，top-1 是同一基金年报里的费率块）。所以数字是「找到标准证据所在位置」的比例，低于「找到能回答问题的内容」的比例；后者没有另行度量。
- **实体过滤在 dev / test 上方向相反**：见次要消融。dev 只有 30 题、只有 3 题不同，是噪声主导的决定；test 上 +0.022 也不显著。按协议 test 不回头影响配置，保留默认「关」，并在此写明：若统筹想改，需要新的 dev 证据或明确的决定。
- **样本量**：dev 30 题、test 72 题；topic 级别只有 8–13 题，CI 很宽。

### 与计划的偏差（附理由和 ADR 编号）
- S2 切块修复与重新入库（统筹决定）：ADR-037。原先的 dev run 1–2 因此作废但保留在 tuning_log。

### 已知问题 / 技术债
- 持仓表格块的检索很弱（见分析），可能与季报持仓表被拆成碎片、表格行只有数字有关（未验证）。可能的改进（**未做**，做了会改变入库，需要统筹决定，且 test 已经用过一次）：合并同一页的相邻表格碎片；给表格块加自然语言标题行。
- 重排在 CPU 上每次约 2.7–4.6 秒（20 个候选），只适合离线评测；线上需要 GPU、缩小候选数或换更小的重排模型（S12–S13 再评估）。
- 921 个不足 50 字的正文块仍在（紧贴表格的小节标题与表注）。
- 001551 销售服务费快照与招募说明书不一致的数据质量登记仍在（S1，未改动）。

### 需要用户做的事
无。

### 给统筹的问题
1. 实体过滤：dev 选「关」、test 消融显示「开」略好但不显著。是否保持「关」？如果要重新决定，需要新的证据来源（不能再用 test）。
2. 持仓表格块检索很弱（holdings nDCG@10 0.126）。是否在 S5 之前做一次入库改动（合并表格碎片、给表格块加自然语言标题行）？这会使 test 需要重新评估，不属于本批的授权范围。

**统筹答复（B4 开工时转达）**：1. 实体过滤保持「关」，不因 test 上 +0.022（CI 含 0）改配置，S5 `search_fund_documents` 的 `fund_codes` 由 Agent 显式传入、与该开关独立 → ADR-038。2. 持仓表格入库方式不改，持仓题主路径是 `run_fund_sql` 查 `holdings_top10`，检索在持仓题上偏弱作为已知局限；「纯 RRF 混合检索不如单独 BM25」也如实记录 → ADR-039。两条都已写入 `docs/LIMITATIONS.md`「检索（S4）」一节。

## S5 前半 mcp-tools 的四个工具 — 2026-09-29（B4）
- commit 范围：`ae73c31`（ADR-038/039 与 LIMITATIONS）..`9afbf58`，代码主体 `8e54002`、守卫修复 `891a1de`；CI：https://github.com/2181385109/fund-research-agent/actions/runs/36553767558 （✅ 6 个 job 全部 success）。`891a1de` 的 CI（run 36553623209）曾因 `scripts/mcp_client_check.py` 未通过 `ruff format --check` 失败一次，`9afbf58` 修复
- 范围：PLAN §7 的 B4 = S5 交付第 1 项（mcp-tools），对应验收 1、2、6 和验收 4 中 mcp-tools 的部分。文档 MCP、LangGraph、出处、风险提示、SSE、live 冒烟（验收 3、5、4 的另一半）留给 B5。

### 完成项
- `mcp-tools/`（FastMCP，streamable HTTP，`http://127.0.0.1:8101/mcp`，`/health`）：`get_fund_db_schema`、`run_fund_sql`（sqlglot 守卫）、`calc_fund_return`、`get_latest_nav`；实现选型见 ADR-040，工具契约见 `mcp-tools/README.md`。
- 双保险：应用层 `sql_guard`（执行重新生成的 SQL，注释被丢弃）+ 数据库层（`fund_reader` 只读账号、只读事务、`MAX_EXECUTION_TIME`）。
- `scripts/verify_returns_vs_reference.py`（与 `eval/reference/gold.py` 逐位比对）、`scripts/mcp_client_check.py`（独立 MCP 客户端）。
- CI 的 python 矩阵早已包含 mcp-tools（ruff check、ruff format --check、pytest 排除 integration/slow/live），无需新增 job；`.env.example` 补了 `MCP_TOOLS_HOST`、`NAV_*`、`SQL_*`；README 进度表更新（此前停在 S0，属于遗漏）。
- 登记：ADR-038、ADR-039（统筹对 B3 遗留问题的两个决定）与 `docs/LIMITATIONS.md` 新增「检索（S4）」一节；ADR-040（mcp-tools 选型）。

### 验收逐条
1. SQL 守卫单测覆盖：DROP、DELETE、UPDATE、INSERT、多语句、注释绕过、系统库、INTO OUTFILE、结果截断 — ✅ — `mcp-tools/tests/test_sql_guard.py` 86 条（另有 `test_server.py` 经 MCP 协议验证「被拒绝的 SQL 不会发到数据库」「201 行探针 → 返回 200 行并标 truncated」）。命令：`cd mcp-tools && .venv/Scripts/python -m pytest -q` → `144 passed, 11 deselected`（CI 同款 marker）；含 integration 共 155 条全过。数据库侧的保险另由 `tests/test_integration_db.py` 证明：绕过守卫直接发 INSERT / DELETE / UPDATE / DROP / CREATE 都被 MySQL 拒绝，读 `mysql.user` 被拒绝，三表笛卡尔积在 `timeout_s=1` 时被服务端中断并报「查询超时」。
2. 收益计算单测：手算用例（一次分红、起止日为非交易日、含费 / 不含费），与参考脚本结果逐位一致 — ✅ — 手算用例：`tests/test_returns.py` 22 条（自造序列：一次分红复权得 +10.00% 而不是 −1%、周六 / 周日起止日取前一交易日且周六那条净值被忽略、节假日、同日窗口、百分比申购费档 / 固定费用档 / 无费档份额、除息日无交易日时写入 notes、各类参数错误）。逐位比对（真实链路：MySQL → `DbReturnData` → `calc_fund_return`，参考端读快照 CSV）：`reports/mcp_tools/20260929T100447Z/{summary.json,report.md}`，命令 `cd eval && python ../scripts/verify_returns_vs_reference.py`。结果：40 个份额、用例 4192 条（不含费 1522、含费 2670），两边都判为无法计算（起点早于首个净值日）250 条，**参与比较 3942 条，`==` 比较全部相同（起止日、区间收益、年化、最大回撤、区间内分红次数各 3942/3942，含费净收益 2465/2465），不一致 0 处**；数据集 `agent_tasks_v1` 的 calc_return 题 9/9 与 gold_params（8 位小数）一致。
4. （mcp-tools 部分）用独立 MCP 客户端列出并调用工具 — ✅（另一半文档 MCP 属于 B5）— `reports/mcp_tools/20260929T100658Z_client_check/client_check.md`：官方 SDK 的 streamable HTTP 客户端，`list_tools` 得 4 个工具，共调用 10 次（正常 6 次、预期被拒绝或参数错误 4 次），与预期不符 0 次；`get_latest_nav` 这次是真实调用东方财富接口（`stale=false`，`nav_date=2026-09-28`，`fetched_at` 有值）。
6. `get_latest_nav` 的故障回退有测试（mock 超时）— ✅ — `tests/test_nav_client.py` 20 条：`httpx.ConnectTimeout` → `stale=true`、回退快照日期、共 3 次请求（首次 + 重试 2 次）；HTTP 5xx / 非 JSON / 空数据 / 错误码 / 净值无法解析都回退；重试后成功；缓存 599 秒内命中、超过 10 分钟重新请求；回退结果不缓存（接口恢复后立即拿到实时数据）；份额不在基金池直接报错且不请求外部接口。全程用 `httpx.MockTransport`，不访问外网。
7. CI 全绿 — ✅ — run 36553767558（上面的链接），6 个 job：backend、python×4（ai-service / mcp-tools / data-pipeline / eval）、scripts + security scan；安全扫描 tracked 与 `--history` 两种模式本机均 PASS（findings=0）。

### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
|---|---|---|---|
| calc_fund_return 与参考实现逐位相同的用例 | 3942 | 3942（参与比较）；总用例 4192，250 条两边都判为无法计算 | `reports/mcp_tools/20260929T100447Z/summary.json` |
| 数据集 calc_return 题与 gold_params 一致 | 9 | 9 | 同上 |
| 独立 MCP 客户端调用与预期不符 | 0 | 10 次调用 | `reports/mcp_tools/20260929T100658Z_client_check/client_check.md` |
| mcp-tools 单测（CI 同款 marker / 含 integration） | 144 / 155 通过 | 全部 | 命令见验收 1 |
| `get_fund_db_schema` 返回体积 | 约 8.6 KB（缩排 JSON；字段写成一行文字之前为 26 KB） | 12 张表 | 本机 `SchemaProvider.describe()` 实测，ADR-040 |

### 与计划的偏差（附理由和 ADR 编号）
- MCP SDK 固定在 1.x（`mcp<2`）：ADR-040。
- 自查中发现并修复了守卫的两处遗漏（修复后才收尾，`8e54002` 之后的提交）：① 优化器提示 `/*+ MAX_EXECUTION_TIME(999999) */` 和 `SET_VAR(...)` 不是普通注释，sqlglot 会保留并原样生成，能覆盖服务端 5 秒超时——现在整个 `Hint` 节点一律拒绝；② 用反引号包住的函数名（`` `SLEEP`(5) ``）绕过了函数黑名单——现在按原名比较。两处都补了单测，客户端冒烟在修复后的代码上重跑（上面引用的是重跑的结果）。逐位比对不经过 `run_fund_sql`，不受这两处修复影响，没有重跑。
- 逐位比对的第一次运行（`ae73c31` 上、代码尚未提交）已删除并用提交后的代码重跑：两次的计数完全相同（seed 固定），只是第一次结果文件里的 git_commit 指向一个不含被测代码的提交，无法追溯，所以只保留重跑的一次。重跑时 `git_dirty=true` 的原因是当时工作区里只有文档（README / PROGRESS）尚未提交，被测代码即 `8e54002`。

### 已知问题 / 技术债
- 所有代码路径只在本机 MySQL 快照上验证过；`run_fund_sql` 的守卫是「根节点白名单 + 节点 / 函数黑名单」，没有做模糊测试（上面两处遗漏是人工试探出来的，说明黑名单可能还有没想到的写法）。数据库侧的保险（只读账号、只读事务、服务端超时）不依赖守卫。存储过程 / 触发器不适用（账号只有 SELECT）。
- `get_fund_db_schema` 每次调用仍约 8.6 KB，占 Agent 上下文；B5 可以只在需要时调用，或把 `conventions` 合并进 system prompt。
- 净值接口是非官方接口，字段含义（`JZZZL` 为百分数）是实测得到的，接口若改字段，工具会回退到快照并标 `stale`，不会静默出错。
- 没有做 Dockerfile（属于 S7）；本机服务用 `python -m fund_mcp_tools.server` 起。

### 需要用户做的事
无。

### 给统筹的问题
无阻塞。一处提示：README 进度表此前停在 S0（B1–B3 都没更新），这次已经更正到 S0–S4 完成、S5 进行中。

## S5 后半 文档 MCP + LangGraph Agent + 出处 + 风险提示 + SSE — 2026-09-29（B5）
- commit 范围：`ae92d44`（B4 HANDOFF）..CI 结论提交见 HANDOFF；CI：__CI__
- 范围：PLAN §7 的 B5 = S5 除 mcp-tools 之外的交付与验收（验收 3、验收 4 的另一半、验收 5、验收 7）。至此 S5 全部完成（验收 1、2、6 在 B4）。

### 完成项
- **选型验证在先**：`langchain-mcp-adapters==0.3.2`（要求 `mcp>=1.24,<2`）+ `mcp` 1.30.0 + `langgraph` 1.2.12 + `langchain-openai` 1.6.6。先用一个最小例子让一个 `MultiServerMCPClient` 同时连上 mcp-tools 与 ai-service `/mcp`（五个工具、各调一次、错误路径），再写图；结果与踩到的行为（默认会把 `isError` 吞成普通文本，必须 `handle_tool_errors=False`）见 ADR-041，固化为 `ai-service/tests/test_agent_integration.py`。
- `ai-service/src/fund_ai/mcp_server/server.py`：文档 MCP，`search_fund_documents(query, fund_codes?, doc_types?, top_n)`，挂在 FastAPI 的 `/mcp`（session manager 在 lifespan 里运行，Host 白名单）；检索用 `config.py` 默认（hybrid_rerank、实体过滤关），**没有改检索和入库代码**。
- `ai-service/src/fund_ai/agent/`：`graph.py`（StateGraph agent ⇄ tools，`max_steps=6`，SQL 报错回传并最多重试 2 次）、`citations.py`（四种出处 + 流式 `[n]` 过滤）、`compliance.py`（固定风险提示 + 输出守卫）、`mcp_client.py`、`prompts.py`（持仓走 SQL、`[n]` 引用、无依据拒答、荐基拒绝）、`llm.py`、`runner.py`、`fake.py`（测试替身）、`universe.py`；`api/chat.py`：`POST /v1/chat/stream`。
- 事件协议与出处字段写入 `docs/API.md`；`.env.example` 补 Agent 变量；README 进度表；`docs/LIMITATIONS.md` 新增「Agent（S5）」一节；ADR-041、ADR-042。
- `scripts/agent_smoke.py`（live 冒烟，保存 SSE 原文）；`scripts/mcp_client_check.py` 增加文档 MCP 的错误用例。

### 验收逐条
3. Agent 单测用 Fake LLM 覆盖：循环上限、工具报错后恢复、引用映射、非法编号丢弃、风险提示必定出现（含报错路径）— ✅ — `ai-service/tests/test_agent_graph.py`（27 条）+ `test_agent_units.py`（30 条）+ `test_agent_mcp_api.py`（11 条）。对应关系：循环上限 `test_loop_cap_forces_a_final_answer_without_tools`（max_steps=3 时恰好 3 轮工具，第 4 次调用不绑定工具）、`test_default_max_steps_is_six`；工具报错后恢复 `test_sql_error_is_returned_to_the_llm_and_the_retry_succeeds`、`test_sql_retries_are_capped_at_two`（首次 + 2 次重试后第 4 次不执行）、`test_unavailable_tool_does_not_break_the_request`；引用映射 `test_mixed_answer_cites_all_four_kinds`（database / document / computation / api，含 args 与实际起止日）；非法编号丢弃 `test_invalid_reference_numbers_are_dropped_and_logged`（用户流里看不到，日志有 warning，`done.dropped_citations`）；风险提示必定出现 `test_disclaimer_present_when_llm_fails_on_first_call`、`…_mid_run_…`、`…_when_tool_listing_fails`、`test_empty_model_answer_…`、`test_chat_stream_when_agent_cannot_be_built_still_sends_disclaimer`，且每个正常路径测试都断言 `disclaimer` 紧挨 `done`。命令：`cd ai-service && .venv/Scripts/python -m pytest -q -m "not integration and not slow and not live"` → 134 passed, 4 deselected。
4. （文档 MCP 部分）用独立 MCP 客户端列出并调用两个服务的全部工具，贴原文 — ✅ — `reports/mcp_tools/20260929T103458Z_client_check/client_check.md`：官方 SDK 的 streamable HTTP 客户端，`:8101/mcp` 4 个工具、`:8001/mcp` 1 个工具，共 14 次调用（含 mcp-tools 的守卫拒绝 3 次、参数错误 1 次，文档 MCP 的正常 2 次、参数错误 2 次），与期望不符 0 次。命令：`python scripts/mcp_client_check.py --url http://127.0.0.1:8101/mcp --url http://127.0.0.1:8001/mcp`。
5. live 冒烟：每种工具至少 2 题、综合题 3 题、荐基请求 2 题，贴 SSE 原文 — ✅ — 13 题，SSE 原文在 `reports/agent_smoke/20260929T103710Z/<题号>.sse`，汇总 `summary.json`。协议层面的结果（n=13，冒烟不是评测，没有标准答案）：13/13 `done.status=ok`；13/13 用到了预期工具；13/13 `disclaimer` 紧挨 `done`；输出守卫标记 0/13；无效编号丢弃 0 次；请求模型 `deepseek-flash`，响应模型 `deepseek-flash`。每类题的用法见下表。回答内容我读了原文（长回答只读了前 700–1400 字，数字没有逐项对账）：荐基题两题都先声明无法推荐、只陈述客观数据并标出处；**发现 1 处事实错误**——tool-calc-2 把 003095 写成「C 类」（003095 是 A 类，C 类是 003096；工具入参与结果都对，是最终回答的措辞错），没有任何自动检查会拦住这类错误，只能靠 S8 的回答评测量化。**没有做自动评分**，准确率数字要等 S8。
7. CI 全绿 — __CI7__

**冒烟明细**（第三次运行；工具 = 实际调用序列的去重）：

| 类别 | 题号 | 用到的工具 | 出处类型 |
|---|---|---|---|
| 文档检索 | tool-docs-1 / -2 | search_fund_documents | document |
| 数据库 | tool-sql-1 / -2 | run_fund_sql（+ get_fund_db_schema） | database |
| 收益计算 | tool-calc-1 / -2（含 include_fees、非交易日起止） | calc_fund_return | computation |
| 最新净值 | tool-nav-1 / -2 | get_latest_nav | api |
| 综合 | mixed-1 / -2 / -3 | 数据库 + 文档（+ 计算） | database、document、computation |
| 荐基请求 | advice-1 / -2 | 检索、SQL、计算都用了，回答先声明无法推荐，再列客观数据 | document、database、computation |

### 实测数字
| 指标 | 值 | n / 分母 | 结果文件 |
|---|---|---|---|
| 冒烟 done.status=ok | 13 | 13 题 | `reports/agent_smoke/20260929T103710Z/summary.json` |
| 首 token 延迟（从请求到第一个 token，含全部工具轮）中位 / 最小 / 最大 | 7.3 s / 2.3 s / 15.1 s | 13 题，单次运行 | 同上（`first_token_ms`）；含 160 字开场白扣留的延迟 |
| 总耗时中位 / 最大 | 7.9 s / 17.6 s | 13 题 | 同上（`total_ms`） |
| 13 题的 token 用量（输入 + 输出）合计 | 197,261 | 13 题 | 同上（`usage`）；**费用未测算**（没有核对当前单价） |
| 开场白被丢弃 / 漏出的题数 | 7 / 0 | 13 题 | 同上（`preamble_*_chars`） |
| ai-service 单测（CI 同款 marker） | 134 通过 | 全部（另有 4 个 integration，其中 3 个本轮新增，本机通过） | 验收 3 的命令 |

前两次运行（同一 13 题，保留）：`20260929T102752Z`（开场白处理之前：13 题中 11 题的答案里混入了工具调用前的开场白，含英文；输出守卫 1 题误报，见下）、`20260929T103200Z`（扣留长度 100：2 题的 118 / 127 字开场白漏出）。第三次是最终配置。第一、二次的 summary.json 没有 `preamble_*` 字段（脚本当时还没加），开场白的数字是从 SSE 原文里数出来的。三次都是 13/13 ok。

### 与计划的偏差（附理由和 ADR 编号）
- 无偏差。PLAN 里的「多服务器 MCP 客户端」用 langchain-mcp-adapters 0.3.2（ADR-041）；Agent 只支持 `LLM_THINKING=disabled`（ADR-018 / ADR-042）。
- 首次冒烟发现并处理了三个问题（ADR-042）：① 工具调用前的开场白混进答案（扣留 + prompt）；② 输出守卫把「无法判断是否适合加仓」误报成违规（补疑问词）；③ langchain 宽松解析会把被截断的 tool call 参数补成合法调用（改为严格解析）。

### 已知问题 / 技术债
- **首 token 延迟高**：中位 7.3 s。构成：每次 LLM 调用之间的工具轮（文档检索含重排，CPU 上数秒）+ 160 字扣留；文档检索第一次调用还要加载模型（约 20 s，冒烟脚本默认先预热）。没有在 GPU 或更小重排模型上测过；这是 S12–S13 的优化对象。
- 开场白扣留是启发式：超过 160 字的开场白仍会漏出（`preamble_leaked_chars` 如实上报）；值是看冒烟样本调的（n=13），换模型 / prompt 后要重看。
- 输出守卫是关键词启发式，会漏报也可能误报；拒答是否可靠要由 S8 的 advice_request 评测来回答，冒烟只有 2 题。
- 每次 MCP 工具调用新建一个会话（适配器行为），增加约几十毫秒；调用量小，暂不优化。
- 模型有时不先看表结构就猜表名 / 列名（冒烟里 1 次），靠错误回传 + 重试恢复，多耗一轮。
- `mcp_allowed_hosts` 默认只放回环；S7 容器互连时必须加 `ai-service:*`，否则 Agent 调自己的 `/mcp` 会被 421 拒绝。
- 没有做请求总超时（只有单次 LLM 调用 60 s 与单次工具调用 30 s 的超时）。

### 需要用户做的事
无。

### 给统筹的问题
无阻塞。
