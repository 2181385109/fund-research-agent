# 进度记录（PROGRESS）

> 由执行者在每个阶段结束时按 CLAUDE.md §11 的模板追加一节，旧内容不删除。统筹审查后会在对应小节末尾追加「统筹审查结论」。

## 当前状态
- 当前阶段：S0 已完成（2026-09-29），等待统筹审查；S1 未开始
- 最近一次 CI：https://github.com/2181385109/fund-research-agent/actions/runs/36454732880 （首次 push，5 个 job 全绿）
- 已完成的用户事项：`.env` 已由脚本生成（含 DeepSeek key）；`.wslconfig` 已设 `memory=10GB`、`swap=4GB` 并重启 WSL（用户授权执行者代办）
- 待用户处理：
  1. 决定是否停掉 ticket-qa 的容器（S0 实测合计约 844MiB，明细见 S0「需要用户做的事」）
  2. （可选）用自己的浏览器打开 http://eid.csrc.gov.cn/fund/disclose/index.html，告诉统筹能否访问
  3. S1 确认基金池；S3 抽检评测集；S8 盲标回答

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
