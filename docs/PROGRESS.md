# 进度记录（PROGRESS）

> 由执行者在每个阶段结束时按 CLAUDE.md §11 的模板追加一节，旧内容不删除。统筹审查后会在对应小节末尾追加「统筹审查结论」。

## 当前状态
- 当前阶段：批次 B2 进行中。**S3 评测集 v1 已完成并冻结**（2026-09-29）。S4 的代码已完成，dev 调参因发现 S2 切块碎片化而中止（统筹决定先修 S2 切块、重新入库，再从 dev run 1 重新开始），见 `docs/HANDOFF.md` 与 `docs/tuning_log.md`
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
