# HANDOFF — B12（S12 压测基线 + 瓶颈定位）完成（2026-10-03）

写给下一个执行者对话（B13 = S13 优化 + 前后对比 + 第二期收尾，打 tag `v0.2-phase2`）。已写进 CLAUDE.md / PLAN / DECISIONS / API / LIMITATIONS 的内容只给指针。

## 1. 当前进度
- 第一期（S0–S8）完成并打了 tag `v0.1-phase1`；第二期 **S9 gRPC、S10 Redis、S11 Kafka、S12 压测基线完成**。S12 提交 `ec24cab`（工具）、`2a7ea84`（报告 / 数据 / PROGRESS），CI run [37111259811](https://github.com/2181385109/fund-research-agent/actions/runs/37111259811) **10 个 job 全绿**（含新增的 `loadtest` job：mock 与统计函数的离线单测）。本文件所在的提交只改文档，推送后再确认一次 CI。
- **S12 没有改任何服务代码**（只新增 `loadtest/`、CI job、`.gitignore`），所以 S4 / S8 的 test 结果不受影响；S13 才会动服务代码。
- S12 的产出（证据在 `docs/perf/baseline.md`、`docs/PROGRESS.md`「S12」、`reports/perf/`、ADR-050、LIMITATIONS「压测（S12）」）：
  - **mock LLM + 净值 stub**（`loadtest/mock_llm/`）：回放 S8 的真实运行（真实工具路线与参数、回答文本、首 token 延迟、输出速度）；压测栈用 `loadtest/compose.loadtest.yml` + `loadtest/stack.sh` 切换（不改默认 compose）。
  - **Locust 场景 A–E**（`loadtest/locustfile.py`）、编排 `run_perf.py`、表格生成 `report_tables.py`、定位脚本 `profile_pyspy.sh` / `stack_summary.py`；战役脚本 `campaign.sh`（A / B / C / D，约 3 小时）。
  - **基线与定位结论**：①检索里**重排占 98.9%**（1 并发 3.52 / 3.56 s），一个检索吃 6.5 个核，吞吐封顶 0.34 QPS（A）、0.24 QPS（B 全链路）；②**16 并发检索 OOM**：ai-service 容器上限 2560 MiB，8 并发峰值已顶满，12–16 并发之间被内核杀死（B 的 16 并发三次全失败，已保留）；③backend 不是瓶颈（JFR 用户态 CPU 1.1%，C 场景 64 并发 backend 14% CPU），backend 的**同时对话流上限 64**（`maxConcurrentChats`）在 128 并发时给出 98% 的 503；④缓存命中路径 P50 80 ms、吞吐封顶 ≈ 43 QPS，py-spy 显示 66% 样本在查询向量化（`embed_documents`）；⑤真实 DeepSeek（E，≤ 5 并发，费用上界 0.92 美元）与 mock 按路线对比相差 +9% / −12%。
- 测试：ai-service 331（`-m "not integration…"`，**跑之前要 `SEMANTIC_CACHE_ENABLED=false`**）+ 13 个 messaging 集成 + 8 个 S10 Redis 集成；backend 172；frontend 32；scripts 29；**loadtest 33**（`cd loadtest && .venv/Scripts/python -m pytest -q`）。
- **本机状态**：fra 的全部容器在跑，**已恢复到正常配置**（`stack.sh restore`：真实 DeepSeek、默认限流与配额、Kafka 消费者开；本机 `.env` 末尾仍有 `SEMANTIC_CACHE_ENABLED=true`，所以语义缓存是开的）。mock 容器已删除，B11 的一次性测试容器（`fra-test-redis` / `fra-test-mysql`）已清掉。ticket-qa 的容器仍是停止状态（统筹补充，第二期期间保持）。`loadtest/.venv`（装有 locust）、`loadtest/mock_llm/replay_v1.json`、`loadtest/.secrets/users.json` 在本机（都 gitignored）；`fra_app` 库里有 `perf_u001…perf_u200` 账号和它们的会话，Redis 里有语义缓存预热条目，没有清理。

## 2. 下一步：B13 = S13 优化 + 前后对比 + 第二期收尾（PLAN §5 S13、§7）
- PLAN 要求：基于 S12 的证据做**至少 3 项优化，每次只改一个变量，前后对比；失败的尝试也要记录**；每项优化后重跑 **S4 的 test 检索评测**，指标下降超过 0.01 的优化须写明取舍理由并**经用户同意**；产出 `docs/perf/optimization.md`、README 补第二期指标、打 tag `v0.2-phase2` 并推送；验收：每项都有前后数字和原始数据、有质量回归结果、CI 全绿、tag 已推送。
- **候选清单在 `docs/perf/baseline.md` §5**（都只是候选，没做、没验证收益）：降低重排计算量（`retrieval_rerank_candidates` 当前 20、量化 / ONNX，**会影响检索质量，要走质量回归**）；给并发检索加上限 / 排队（保护型，解决 OOM）；重排与嵌入移出事件循环所在进程；命中路径减少查询向量化开销（每次 `encode` 的模块转换，py-spy 样本是被采样拖慢时取的，改之前先无采样复测）；提高 `mem_limit`。统筹在 PROGRESS「给统筹的问题」里被问到：保护型与提速型是否都算进「至少 3 项」，开工前先看统筹有没有回复，没有就按「两类都做、分开记录」推进。
- **前后对比要用同一套方法**：基线是 `git 978d099` 的服务代码 + `loadtest/` 工具；对比时用同样的场景、档位、预热 / 窗口和 3 次重复（A / B 窗口 120 s、预热 20 s；C / D 窗口 60 s、预热 15 s）；每次战役约 3 小时，S13 可以只重跑与该项优化相关的场景和档位（例如重排优化重跑 A 的 1、2、4 和 B 的 1、2、4、8），在 optimization.md 里写明范围。**优化前先把 Locust 的 `--prewarm-s` 保持开着**（冷启动时的模型加载会污染第一档）。
- **别踩**：①ai-service 重启后模型是懒加载的，冷启动期间遇到并发会再被 OOM 杀——压测前先预热（`run_perf.py` 默认 `--prewarm-s 40`）；②任何改 Agent / 检索 / prompt 的改动要重新评估 test 时必须按红线 2 登记（不改题、不挑 run、test 不反复调参）；③S13 改了服务代码后，镜像要重建（ai-service 见 §3 的 `PIP_INDEX_URL` 做法，约 6 分钟；backend 约 8 分钟）；④压测数字一律注明 mock 还是真实 LLM。

## 3. 如何拉起环境
- 全栈：README「一键启动全栈」。已在跑时不需要动。重建单个服务：`MSYS2_ARG_CONV_EXCL='*' wsl.exe -d Ubuntu-24.04 -u root -- bash <脚本>`，脚本里 `cd /mnt/d/xiangmu/fund-research-agent && docker compose --profile app up -d --no-deps --build <服务>`（**不要 `down -v`**）。**Git Bash 会把 `/mnt/...` 参数改写**：调用前加 `MSYS2_ARG_CONV_EXCL='*'`。backend 镜像重建约 8 分钟；**ai-service 镜像从 PyPI 装依赖在本机网络上极慢：用 `docker compose --profile app build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ ai-service`**，约 6 分钟。改了 `proto/` 要同时重生成 Python 桩（`ai-service/.venv/Scripts/python scripts/gen_proto.py`）并重建 backend 与 ai-service。
- **压测栈**：`stack.sh up [cache] [real]` 用 mock（或真实 LLM）重建 mock-llm / mcp-tools / ai-service / backend，`stack.sh restore` 恢复；命令写法见 `loadtest/README.md`（Git Bash → WSL 路径转换、`PYTHONUTF8=1`）。跑完压测要 `restore`，并记得压测前 `loadtest/make_users.py -n 100` 刷新令牌（JWT 12 小时过期；> 100 并发要更多账号）。
- py-spy：`loadtest/profile_pyspy.sh <WSL 输出目录> <秒数>`（旁路容器共享 ai-service 的 PID 命名空间，目标 pid 恒为 1；现装 py-spy 要走国内镜像）。backend 的 JFR：`PERF_BACKEND_JAVA_OPTS='-XX:StartFlightRecording=filename=/tmp/backend.jfr,settings=profile,dumponexit=true'` 传给 `stack.sh up`，跑完 `docker stop -t 40 fra-backend` 后 `docker cp` 出来，用 `D:\tools\jdk-17\bin\jfr.exe print --events jdk.ExecutionSample` 解析、`stack_summary.py jfr --from/--to` 按压测窗口过滤。
- Kafka、第二个消费者实例、本机跑 Redis / MySQL / Kafka 相关测试（一次性容器与 `FRA_TEST_*` 环境变量）、本机跑 `BackendIntegrationTest` 的替代做法：见 git 历史里 B11 的 HANDOFF（`git show 978d099:docs/HANDOFF.md` §3）。
- 访问本机端口：curl 用 `--noproxy '*'`，Python 客户端用 `trust_env=False` 或设 `NO_PROXY=127.0.0.1,localhost`。

## 4. 会再踩的坑（B12 新增；B11 及更早的旧坑仍有效，见 `git show 978d099:docs/HANDOFF.md` §4）
- **Windows 上 Locust 读 `pyproject.toml` 用 GBK 会失败**（里面有中文注释）：运行 Locust 前设 `PYTHONUTF8=1`（`run_perf.py` 已为子进程设置）。
- **Bash 工具里的 `python - <<EOF`**：不带引号的 heredoc（为了展开 `$变量`）会让中文字面量变成乱码（`cost.json` 的 note 被写坏过，用 Write 重写）；带 `\n` / `\\` 的替换在 heredoc 里会被二次转义（`run_perf.py` 的两处替换静默没生效，用 Edit）；`sed` 里写 `\n` 会变成真换行。**改含中文或反斜杠的文件用 Write / Edit**（CLAUDE.md §8 早就写了）。
- **`Path.read_text` / `write_text` 在 Python 3.12 没有 `newline` 参数**（3.13 才有）：要控制换行用 `open(..., newline="")`。
- **安全扫描的 `local-path` 规则会拦字面量的盘符路径**（含测试里的虚构路径）和形如 `\d\d:\d\d` 的正则：测试输入在运行时拼；时间戳正则写 `[0-9]{2}`；结果文件里的本机路径用 `loadtest/scrub_paths.py` 清成 `<repo>`（`run_perf.py` 写文件时已自动做）。
- **ai-service 镜像里没有 `pgrep`**；找进程用 `/proc/*/cmdline`。`docker stats --no-stream` 的 CPU% 是 ~1 s 窗口的值；`docker events` 的 `--format` 取属性容易写错，直接看 `dmesg | grep "Memory cgroup out of memory"` 和 `docker inspect … RestartCount`。
- **`loadtest` 的 Locust 虚拟用户数要小于账号数**，每个用户固定一个账号（`make_users.py -n` 要 ≥ 最大并发）；backend 同时对话流上限 64，> 64 并发会得到 503（这是设计，不是故障）。
- **Monitor 重复通知**：同时挂几个监视器会收到重复事件；用一个 `tail -F | grep` 就够。
- 旧坑摘要（B11）：本机 `.env` 的 `SEMANTIC_CACHE_ENABLED=true` 会让 ai-service 3 个老测试失败（pytest 前加 `SEMANTIC_CACHE_ENABLED=false`）；注册用户名不能含 `-`；CI 里偶发失败的老测试 `HttpAiServiceClientTest.chatGivesUpWhenTheUpstreamGoesSilent`（先重跑）；同一会话的第二个问题起不走语义缓存；`scripts/build_cache_pairs.py` 的已知缺陷（8 对畸形问题）v1 保持原样。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |
| `eval/datasets/cache_pairs_v1.jsonl`（S10，不在冻结的 MANIFEST 里） | v1，472 对（dev 208 / test 264），**test 已用掉** | `ebca9f77ac47b8e4717d25155b06f6e6d9162e081f1bea3cc5af4b9c7fab5a8f` |
| `loadtest/mock_llm/replay_v1.json`（S12，gitignored，可重建） | 124 题，源 `answers.jsonl` sha256 前缀 `a0eebf8c8cfe` | 由 `loadtest/mock_llm/replay.py` 确定性生成 |

S12 没有改 MANIFEST、评测集、Agent / 检索 / prompt、任何服务代码。**跑任何评测前确认 `SEMANTIC_CACHE_ENABLED` 是关的**（本机 `.env` 现在是开的！）。S13 的质量回归规则见 PLAN。

## 6. 等用户 / 统筹处理的事
1. **统筹**：`docs/PLAN.md` §7 表标记 B12 完成；PROGRESS「S12 · 给统筹的问题」里 S13 的「保护型 / 提速型」计数口径；CLAUDE.md §5 的 `JUDGE_MODEL` 那一行按用户决定改成 deepseek-flash（B8 遗留）；前端「公共库文件清单页」仍没做。
2. **统筹（可选，B11 遗留）**：2026Q3 季报发布后（预计 10 月下旬）要先由数据管道把新季报登记进 `data/MANIFEST.json`（已冻结，需统筹与用户批准），再 `POST /api/ingest-batches {"reportPeriod":"2026Q3"}`；批次接口要不要加管理员限制；DLQ 重投工具；校准集 v2；令牌桶是否推广到注册 / 登录 / 上传；工具执行期断开的取消延迟（LIMITATIONS S9-5）是否在 S13 顺带复验。
3. **用户**：语义缓存是否在本机 / 演示环境里打开（B10 遗留）；S13 若有影响检索质量的优化，要征得同意（PLAN 规则）。
4. 数据质量登记（未改动）：001551 销售服务费快照与招募说明书不一致（`reports/data_quality/20260929T045331Z/`）；已登记未查明的真实错误（不阻塞）见 B8 的 `numeric_any_vs_first.md`。
