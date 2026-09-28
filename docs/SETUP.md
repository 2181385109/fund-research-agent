# 本地环境搭建（SETUP）

目标机器：Windows 11 + WSL2（16GB 笔记本，用户非管理员，没有 Docker Desktop）。Docker Engine 跑在 WSL2 的 `Ubuntu-24.04` 发行版里；Java / Python 应用在开发期直接跑在 Windows 本机，通过 `127.0.0.1:<端口>` 访问 WSL 里的容器。

## 1. 前置条件

| 组件 | 位置 / 版本 | 说明 |
|---|---|---|
| WSL2 + Ubuntu-24.04 | 发行版里装了 Docker Engine 29 + compose v5 | 安装过程见 ticket-qa 项目的 `ops/wsl/`；镜像加速在 `/etc/docker/daemon.json`（docker.1ms.run、docker.m.daocloud.io） |
| WSL 常驻脚本 | `D:\tools\wsl-keepalive.vbs`（登录时由启动文件夹里的副本自动运行） | 没有任何 `wsl.exe` 会话时 WSL 会在几秒内停掉发行版，这个脚本挂一个隐藏的 `sleep infinity` 会话 |
| JDK 17 | `D:\tools\jdk-17`（Temurin，不在 PATH） | 使用前设置 `JAVA_HOME`；Java 进程加 `-Dfile.encoding=UTF-8` |
| Maven 3.9 | `D:\tools\maven`（不在 PATH，已配阿里云镜像） | |
| Python 3.12 | `E:\python\python.exe` | 每个 Python 包各自一个 `.venv`（ADR-017）；输出中文前设 `PYTHONIOENCODING=utf-8` |
| gh CLI | `C:\Program Files\GitHub CLI\gh.exe`（不在 PATH） | 已登录 `2181385109` |

## 2. WSL2 内存上限：`%USERPROFILE%\.wslconfig`

```ini
[wsl2]
vmIdleTimeout=604800000   # ticket-qa 加的，保留
memory=10GB
swap=4GB
```

**为什么是 10GB**：本机 16GB 内存。PLAN §3 的预算是 infra ≈ 5.3g + 应用 ≈ 3.6g，合计上限约 8.9g，10GB 留出约 1GB 给 WSL 内核与 page cache；Windows 侧保留约 6GB 给 IDE、浏览器和本机跑的 Java/Python 进程。4GB swap 用来吸收加载模型等瞬时峰值，避免直接 OOM。

修改后必须重启 WSL 才生效（会停掉 WSL 里**所有**容器，包括 ticket-qa 的）：

```bash
wsl --shutdown
```

```bash
wscript.exe D:\tools\wsl-keepalive.vbs
```

验证（S0 实测：`free -h` 的 total 为 9.7Gi，Swap 4.0Gi）：

```bash
wsl -l -v
```

```bash
wsl -d Ubuntu-24.04 -u root -- free -h
```

## 3. 生成 `.env`

```bash
python scripts/gen_env.py --llm-key-file "D:\个人\api(Deepseek).txt"
```

- 从 `.env.example` 生成 `.env`：标了 `[secret]` 的变量随机生成 24 位字母数字串，`LLM_API_KEY` 从 key 文件读取；脚本只打印变量名，不打印值。
- `.env` 已存在时默认拒绝覆盖（`--force` 才覆盖，**会换掉所有密码**，已初始化的 MySQL 数据卷里的旧密码不会随之改变，需要 `docker compose down -v` 重建）。
- 验证没被跟踪：`git check-ignore -v .env` 应输出 `.gitignore:2:.env	.env`。
- `.env.example` 以后新增变量时，用 `python scripts/gen_env.py --add-missing` 只追加缺少的变量（新的 `[secret]` 变量随机生成），已有值不动。

### 3.1 已有 MySQL 数据卷时补建 `fund_loader`（S1 起，ADR-023）

`deploy/mysql/init/02-fund-loader.sh` 只在数据卷为空时自动执行。S0 就建好数据卷的环境，先 `gen_env.py --add-missing`，再重建 mysql 容器让它拿到新环境变量，然后手动执行一次（幂等）：

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose up -d mysql && sleep 20 && docker exec fra-mysql bash /docker-entrypoint-initdb.d/02-fund-loader.sh'
```

## 4. 启动 infra（经 wsl）

在 Windows（PowerShell 或 Git Bash）里直接复制运行：

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose up -d'
```

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose ps'
```

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'docker stats --no-stream'
```

首次 `up` 会构建 `fra/elasticsearch-ik:8.19.22`（在官方 ES 镜像上安装 IK，约 15 秒，需要 WSL 能访问 release.infinilabs.com）。停止与清理：

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose stop'
```

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/fund-research-agent && docker compose down -v'
```

（`down -v` 会删掉数据卷，MySQL 会在下次启动时按 `.env` 重新初始化。）

多行命令不要直接塞进 `wsl.exe ... bash -c`（PowerShell 传参会混入 CRLF）：先写成 `.sh` 文件，再 `bash /mnt/d/...` 执行。用 Git Bash 读 `wsl.exe` 的输出时加 `| tr -d '\0'`。

### 4.1 拉镜像很慢或卡住时

S0 实测（2026-09-29）：daemon 默认先走 docker.1ms.run，只有约 0.1–0.4 MB/s，`mysql:8.4.11` 拉了 10 分钟没有进度；显式从 DaoCloud 拉取可达 8–11 MB/s，但 DaoCloud 对尚未缓存的层会先返回 `error from registry: unavailable`。可选做法：

```bash
wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'docker pull docker.m.daocloud.io/library/mysql:8.4.11 && docker tag docker.m.daocloud.io/library/mysql:8.4.11 mysql:8.4.11'
```

仍然失败时，可以在 Windows 侧下载镜像（DaoCloud 直连，失败回退 Docker Hub 经本机代理），打成 OCI 归档后在 WSL 里 `docker load -i`。S0 就是这样拿到 `mysql:8.4.11` 和 `milvusdb/milvus:v2.5.27` 的，加载后的镜像 digest 与 Docker Hub 清单一致（见 ADR-015）。

## 5. 端口

全部可在 `.env` 修改，默认值按 PLAN §2.2，避开 ticket-qa 已占用的 3306 / 6379 / 8080 / 8089 / 9090 / 5672。

| 服务 | 宿主机端口 | 变量 | 阶段 |
|---|---|---|---|
| MySQL 8.4 | 3307 | `MYSQL_PORT` | S0 |
| Redis 8 | 6380 | `REDIS_PORT` | S0 |
| Elasticsearch 8 | 9201 | `ES_PORT` | S0 |
| Milvus（gRPC/REST） | 19530 | `MILVUS_PORT` | S0 |
| Milvus 管理（/healthz、/metrics） | 9091 | `MILVUS_METRICS_PORT` | S0 |
| backend | 8081 | `BACKEND_PORT` | S0 |
| ai-service HTTP | 8001 | `AI_SERVICE_PORT` | S0 |
| ai-service gRPC | 50051 | `AI_GRPC_PORT` | S9 |
| mcp-tools | 8101 | `MCP_TOOLS_PORT` | S5 |
| Kafka | 9094 | `KAFKA_PORT` | S11 |
| frontend | 8088 | `FRONTEND_PORT` | S7 |

## 6. 运行应用（开发期在 Windows 本机）

```bash
export JAVA_HOME=/d/tools/jdk-17 PATH=/d/tools/jdk-17/bin:/d/tools/maven/bin:$PATH
cd backend && mvn -B verify && java -Dfile.encoding=UTF-8 -jar target/backend-0.0.1-SNAPSHOT.jar
```

```bash
cd ai-service && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m uvicorn fund_ai.api.app:app --port 8001
```

健康检查（本机 curl 访问 127.0.0.1 时绕过代理）：

```bash
curl -s --noproxy '*' http://127.0.0.1:8081/api/health
```

```bash
curl -s --noproxy '*' http://127.0.0.1:8001/health
```

LLM 冒烟（会真实调用 DeepSeek，每次约 400 token）：

```bash
ai-service/.venv/Scripts/python scripts/llm_smoke.py
```

push 前的安全扫描：

```bash
python scripts/security_scan.py && python scripts/security_scan.py --history
```

## 7. 排障

| 现象 | 原因 / 处理 |
|---|---|
| 端口连不上，`wsl -l -v` 显示 `Stopped` | 常驻会话没了，WSL 停掉了发行版。运行 `wscript.exe D:\tools\wsl-keepalive.vbs`；容器都带 `restart: unless-stopped`，会随 Docker 自动恢复 |
| `wsl.exe` 输出里有一行乱码 `wsl: ...localhost ... NAT ...` | WSL 的提示「检测到 localhost 代理配置，但未镜像到 WSL；NAT 模式不支持 localhost 代理」，无害；它也说明 WSL 里访问不到 Windows 的 127.0.0.1:7897 代理 |
| MySQL 配置不生效 | `/mnt/d` 下文件权限是 777，MySQL 会忽略 world-writable 的配置文件；本项目用 compose `command` 传参（ADR-016） |
| Redis 里 `FT._LIST` 报 unknown command | 启动命令没有经过官方 `docker-entrypoint.sh redis-server ...`，自带模块没加载（ADR-007） |
| backend health 在 Redis 恢复后仍有十几秒报 DOWN | Lettuce 自动重连按指数退避重试，S0 实测 Redis 停约 1 分钟后恢复，约 15 秒后重连成功 |
| ES 容器内存接近 mem_limit | 堆 1g 在启动时整块预占（AlwaysPreTouch），S0 实测常驻约 1.42GiB / 1.5GiB，没有 OOM；见 PROGRESS「给统筹的问题」 |
| **与 ticket-qa 共存** | ticket-qa 的 5 个容器 S0 实测合计约 843MiB（mysql 442、rabbitmq 177、wiremock 170、prometheus 44、redis 11）。只起本项目 infra（约 2GiB）时两者可以共存；**S5 起加载本地模型、S7 起全栈（预算 8.9g）之前，先停 ticket-qa**：`wsl.exe -d Ubuntu-24.04 -u root -- bash -c 'cd /mnt/d/xiangmu/ticket-qa/ops && docker compose stop'`（由用户决定） |
