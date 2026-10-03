# loadtest — S12 压测

压测数字与结论在 [`docs/perf/baseline.md`](../docs/perf/baseline.md)；每个数字都能追溯到 `reports/perf/<UTC 时间戳>_<场景>/summary.json`。
**所有压测数字都注明是 mock LLM 还是真实 LLM**（场景 E 之外一律是 mock）。

## 组成

| 文件 | 作用 |
|---|---|
| `mock_llm/server.py` | mock 服务：OpenAI 兼容的流式聊天补全 + 东方财富净值接口 stub（`get_latest_nav` 的上游）+ `/admin/stats` |
| `mock_llm/replay.py` | 从 S8 的真实运行记录里提取**回放脚本**（`replay_v1.json`，gitignored，可确定性重建）：每题真实走过的工具路线与参数、真实回答文本、真实的首 token 延迟与输出速度 |
| `compose.loadtest.yml` / `stack.sh` | 压测栈：把 LLM、净值接口换成 mock，放宽限流与配额，关掉批量入库消费者；`PERF_CACHE=true` 开语义缓存（场景 D） |
| `locustfile.py` | 场景 A–E（见文件头表格）；闭环负载（think time = 0，并发数 = 用户数），逐请求写原始 JSONL |
| `perf_common.py` | 问题池、SSE 解析、分位数与窗口统计、docker stats 解析（有单测） |
| `run_perf.py` | 编排：场景 × 并发档 × 重复 N 次，窗口统计，docker stats / 宿主机 CPU 采样，写 `reports/perf/…` |
| `make_users.py` | 批量准备压测账号（令牌写进 gitignored 的 `.secrets/`） |
| `perf_prime_cache.py` | 场景 D 预热：写入缓存并确认哪些同义改写能命中 |
| `profile_pyspy.sh` / `dockerstats.sh` | py-spy 旁路采样（火焰图）、docker stats 采样 |

## 复现（Windows + WSL2 Docker；以下命令在仓库根目录的 Git Bash 里运行）

```bash
# 0. 一次性：压测用的 Python 环境（Locust 只装在这里，不进 CI 和各服务镜像）
python -m venv loadtest/.venv   # Python 3.12
loadtest/.venv/Scripts/python -m pip install locust httpx psutil fastapi uvicorn pytest ruff

# 1. 回放脚本（来自 reports/answer_eval/20260929T171529Z/hybrid_rerank/answers.jsonl）
ai-service/.venv/Scripts/python loadtest/mock_llm/replay.py

# 2. 起压测栈（缓存关）。恢复正常配置：stack.sh restore
WSLREPO="/mnt/$(pwd | sed -E 's|^/([a-z])/|\1/|')"   # Git Bash 路径 → WSL 路径
MSYS2_ARG_CONV_EXCL='*' wsl.exe -d Ubuntu-24.04 -u root -- bash "$WSLREPO/loadtest/stack.sh" up
loadtest/.venv/Scripts/python loadtest/make_users.py -n 100

# 3. 跑一个场景（Windows 上 Locust 要读 UTF-8 的 pyproject，所以设 PYTHONUTF8=1）
cd loadtest && PYTHONUTF8=1 .venv/Scripts/python run_perf.py --scenario B --users 1,2,4,8,16 --reps 3 --warmup 15 --duration 60
```

场景 D：`stack.sh up cache` → `perf_prime_cache.py --out <文件>` → `run_perf.py --scenario D --d-pool <文件>`。
场景 E（真实 DeepSeek，可选）：`stack.sh up real`（从 `.env` 读 LLM 配置，不打印密钥；净值接口仍是 stub）→ `run_perf.py --scenario E --users 1,3,5`；并发不超过 5，费用从 `done.usage` 的 token 数按价目表估算（见 `reports/perf/*_E/cost.json`）。
全部场景的战役脚本是 `campaign.sh`（A / B / C / D，约 3 小时）；定位：`profile_pyspy.sh`（ai-service）、backend 的 JFR 见 ADR-050；`scrub_paths.py` 把结果文件里的本机绝对路径替换成 `<repo>`（`run_perf.py` 写文件时已自动做）。

## 测量口径（读数字前先看这里）

- **闭环负载**：每个虚拟用户读完整条流之后立刻发下一个请求，所以 QPS 随并发上升直到容量上限，之后延迟线性增长（排队）。这不是「固定到达率」的开环压测——排队延迟因此被低估（协调遗漏），拐点以 QPS 不再上升为准。
- **窗口**：每次运行先 `warmup` 秒不计入，再取 `duration` 秒；统计「窗口内完成」的请求；QPS = 窗口内完成数 / 窗口长度；错误率的分母 = 窗口内完成数；延迟只统计成功的请求。
- **重复**：每档并发独立跑 3 次，报告的是**三次各自指标的中位数**（三次的原值都在 summary.json 里）。
- **环境**：Locust 与 Docker（WSL2，10 GB / 16 逻辑核）在同一台笔记本上，会互相抢 CPU；`summary.json` 的 `host_cpu_*`、`locust_cpu_*` 记录了压测机自身的占用，用来判断压测端是否成为瓶颈。
- **mock 的真实度**：工具执行（检索、SQL、MCP、净值缓存）是真的，LLM 是回放。首 token 延迟和输出速度取自 S8 的真实 DeepSeek 记录，**并发下真实 API 的延迟可能变化，mock 不会**；`usage` 的 token 数不是计费数字。
