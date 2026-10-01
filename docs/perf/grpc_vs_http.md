# S9：gRPC 与 HTTP+SSE 的对比实验（预实验，不是压测基线）

> 本文件的所有数字都来自 `reports/s9/` 下的原始结果文件（路径在每节开头）；条件、n、失败数写在表旁。
> **这是 S9 的预实验，不是 S12 的压测基线**：单机、回环、顺序（并发 1）、假 ai-service（无 LLM、无检索）。真实对话的延迟由 LLM（秒级）与检索（百毫秒～秒级）决定，传输层的差别在其中可以忽略。

## 1. 传输延迟：HTTP+SSE vs gRPC

- **结果目录**：`reports/s9/transport_latency/20261001T155512Z/`（8 个 JSON，字段见 `scripts/bench_transport.py` 文件头）
- **方法**：`scripts/bench_fake_ai.py` 起一个「假 ai-service」——HTTP（uvicorn）与 gRPC（grpc.aio）在同一进程里，Agent 换成脚本化实现：每次请求按真实协议发 `meta → tool_start → tool_end → N 个 token → citations（3 条）→ disclaimer → done`。客户端 `scripts/bench_transport.py`：
  - **direct**：Python 客户端直连 ai-service（HTTP：httpx 流式读 SSE；gRPC：同步桩），量服务端传输 + 序列化 + 客户端解析；
  - **backend**：Python 客户端连 backend 的 SSE 接口，backend 作为本机 JVM 进程（`AI_TRANSPORT=http|grpc`），上游是同一个假 ai-service，量「backend 的上游客户端」这一层（HTTP：JDK HttpClient 读 SSE；gRPC：`GrpcAiServiceClient` + protobuf → JSON 还原）。两种模式下 backend 对浏览器输出的协议相同；每次请求新建会话（上下文长度恒定），落库在两种模式里都发生。
  - 指标：`t_first` = 发出请求 → 收到第一个事件（`meta`）；`t_total` = 发出请求 → 收到 `done`。每个配置先 warmup（direct 20 / 3 次，backend 20 / 3 次）再记录 n 次顺序请求；direct 每种传输跑两轮并交换先后顺序（http → grpc → grpc → http），抵消顺序效应。
- **环境**：Windows 11 家庭中文版，本机进程（ai-service：Python 3.12、uvicorn、grpcio 1.84.0；backend：JDK 17、Spring Boot 3.5、grpc-java 1.84.0），回环地址，backend 的 MySQL / Redis 在 WSL2 的容器里；没有其他负载。**Windows 的 asyncio 定时器粒度约 15.6 ms**：假 agent 里 `sleep(20ms)` 实际约 32 ms，`sleep(10ms)` 会被当成立即返回（第一次尝试因此作废，已删除，见下）。
- **复现**：`ai-service/.venv/Scripts/python scripts/bench_fake_ai.py --tokens 300 --token-delay-ms 0`（另一个终端）→ `python scripts/bench_transport.py direct --n 200 --warmup 20 --out …`；backend 模式需要先 `AI_TRANSPORT=grpc|http BACKEND_PORT=8082 AI_SERVICE_BASE_URL=http://127.0.0.1:8011 AI_SERVICE_GRPC_TARGET=127.0.0.1:50061 java -jar backend/target/backend-*.jar`，再 `python scripts/bench_transport.py backend --backend http://127.0.0.1:8082 …`。

### 结果（单位 ms；`t_first` 与 `t_total` 都是「中位数 / P95」）

**A. 突发事件流**（每个请求 306 个事件：300 个 token 背靠背发出，无间隔）

| 场景 | 传输 | n（失败） | t_first | t_total | 备注 |
|---|---|---|---|---|---|
| direct（Python 客户端） | HTTP+SSE | 200 + 200（0） | 3.01 / 25.05；3.19 / 24.51 | 5.28 / 26.97；5.52 / 26.58 | 两轮 |
| direct | gRPC | 200 + 200（0） | 0.61 / 0.72；0.62 / 0.80 | 34.13 / 40.43；34.69 / 41.01 | 两轮 |
| backend（经 backend 的 SSE） | HTTP+SSE | 200（0） | 16.96 / 37.92 | 27.98 / 47.42 | 一轮 |
| backend | gRPC | 200 + 200（0） | 19.18 / 35.64；12.25 / 35.41 | 57.50 / 74.11；49.53 / 72.37 | 两轮（第二轮在 HTTP 之后） |
| backend | gRPC，接收窗口 16（**失败的尝试**） | 200（0） | 10.48 / 33.88 | 61.97 / 83.51 | 见下 |

**B. 有节奏的事件流**（每个请求 56 个事件：50 个 token，间隔 20 ms，实测约 32 ms/个）

| 场景 | 传输 | n（失败） | t_first | t_total |
|---|---|---|---|---|
| direct | HTTP+SSE | 30 + 30（0） | 1.77 / 16.55；9.86 / 16.79 | 1552.59 / 1568.90；1560.78 / 1571.18 |
| direct | gRPC | 30 + 30（0） | 0.62 / 0.76；0.66 / 1.37 | 1547.40 / 1553.46；1548.30 / 1552.52 |
| backend | HTTP+SSE | 30（0） | 14.53 / 37.46 | 1568.03 / 1596.13 |
| backend | gRPC | 30（0） | 14.71 / 36.98 | 1561.45 / 1593.82 |

### 结论（只说数据支持的）

1. **首个事件到达**：direct 里 gRPC 更快（0.6 ms vs 3.0 ms，两轮一致）；经 backend 后差别淹没在噪声里（12–19 ms vs 17 ms，各 200 次，P95 相当，backend 里还有落库和 SseEmitter 的开销）。
2. **突发流下 gRPC 的总耗时更高**：direct 5.3 ms vs 34 ms（306 个事件，≈ 0.11 ms / 事件）；经 backend 28 ms vs 50–57 ms。原因是 Python `grpc.aio` 每条消息一次异步发送，而 uvicorn 把 SSE 的小写入合并了。用一个什么都不做的 grpc.aio 服务端（只 `yield` 300 条预先构造好的 `ChatEvent`，`scripts/bench_grpc_aio_micro.py`，结果 `reports/s9/transport_latency/20261001T155512Z/grpc_aio_micro.txt`）做对照，同机、n = 100：同步客户端中位数 39.67 ms、aio 客户端 40.09 ms——所以这是 Python gRPC 流式发送本身的成本，不是本项目的序列化代码（`token` 事件已走快速路径，不经 `json_format`）。
3. **有节奏的流（接近真实 LLM 的输出）下两者没有可分辨的差别**：direct 1547–1548 ms（gRPC）vs 1553–1561 ms（HTTP），经 backend 1561 vs 1568 ms，差值在 P95 的离散度以内（< 0.6%）。真实 DeepSeek 的 token 间隔通常大于这里的 32 ms，所以 gRPC 的每事件开销（0.11 ms）在实际对话里不会成为瓶颈。
4. **失败的尝试（保留）**：怀疑 Java 客户端逐条 `request(1)` 的流控导致每条消息一次线程切换，试了 16 条的接收窗口（滑动窗口，仍然有界）：`t_total` 中位数 61.97 ms，与 49.5–57.5 ms 没有改善（反而偏高，n = 200，在噪声范围内），已回退，代码保持逐条补要。
5. **作废的运行（已删除原始文件，原因如下）**：第一次「有节奏」预实验用 10 ms 间隔，发现 HTTP 路径上 100 个 token 在 58 ms 内全部发完（Windows 定时器粒度 15.6 ms，小于粒度的 `sleep` 不起作用），而 gRPC 路径却被拖到每个 15 ms，两种传输的条件不一致，所以改用 20 ms 间隔重做；旧结果文件已删除、没有用于任何结论。
6. **局限**：并发 1、单机、回环；backend 的 HTTP 模式只跑了一轮（gRPC 两轮）；没有 TLS（两种模式都没有）；没有测内存 / CPU；不能外推到网络环境或高并发（S12 做）。

## 2. 客户端断开 → ai-service 取消的延迟（S6 遗留项，HTTP 与 gRPC 对比）

- **结果目录**：`reports/s9/cancel_latency/20261001T161858Z/`（4 个 jsonl，每行一次尝试，含 request_id、断开时刻、两端日志时间戳换算出的延迟）；汇总表由 `scripts/summarize_cancel_latency.py` 生成。
- **方法**（`scripts/cancel_latency.py`）：真实全栈（compose 里的 backend、ai-service 容器，真实 DeepSeek；问题「中银创新医疗混合C 的销售服务费率是多少？」会触发一次文档检索工具调用）。客户端对 backend 的 `POST /api/conversations/{id}/chat`（SSE）发请求，读到指定事件后**直接关闭 TCP 连接**（模拟浏览器关页面），记下关闭时刻 `t_disc`，再从两个容器的日志里取这个 request_id 的 `chat client gone`（backend 发现客户端已断开）和 `chat_stream_cancelled … transport=…`（ai-service 的 Agent 生成器被取消）的时间戳，相减。**脚本在 WSL 里运行**，与容器共用同一个时钟（Windows 与 WSL 的时钟有 ~100 ms 偏差）。断开点：`meta`（收到 `meta` 就断：Agent 在等第一次 LLM 响应，没有事件流动）、`tool_start`（收到第一个 `tool_start` 就断：检索工具在执行，没有事件流动）、`token`（收到第 3 个 token 就断：事件持续流动）。
- **条件**：backend 容器以 `(AI_TRANSPORT, CHAT_HEARTBEAT)` 的 4 种组合各启动一次（grpc / http × 心跳 5 s / 1 s），每个组合 × 3 个断开点各 10 次，顺序执行，n = 120，**全部成功取消（0 个未取消 / 0 个流先结束）**；`ai-service` 在 compose 里（CPU 版 BGE 重排），环境同第 1 节但容器化。每次请求都调用真实 LLM（几十次，费用可忽略）。
- **复现**：在 WSL 里 `TS=$(date -u +%Y%m%dT%H%M%SZ) N=10 bash scripts/cancel_latency_matrix.sh`（循环四个组合：重启 backend 容器 → 等健康 → `python3 scripts/cancel_latency.py …`；前置：compose 全栈在跑、ai-service 有 `LLM_API_KEY`）；汇总 `python scripts/summarize_cancel_latency.py reports/s9/cancel_latency/<TS>`。本次运行的驱动脚本是同一内容的临时版本（含本机路径，未提交），它的 stdout 保存在结果目录的 `driver_stdout.txt`。

### 结果（延迟 = 日志时间戳 − 断开时刻，单位 ms）

| 配置 | 断开点 | 成功 / 计划 | 断开→ai-service 取消 ms：中位数 / P90 / 最小 / 最大 | 断开→backend 发现 中位数 ms | backend 发现原因 |
|---|---|---|---|---|---|
| grpc_hb1s | meta | 10 / 10 | 1988 / 2047 / 1472 / 2280 | 1941 | send failed 6, 心跳 / emitter 错误 4 |
| grpc_hb1s | tool_start | 10 / 10 | 3624 / 4279 / 3250 / 4913 | 3609 | 心跳 / emitter 错误 10 |
| grpc_hb1s | token | 10 / 10 | 3 / 21 / 2 / 52 | 1 | send failed 9, 心跳 / emitter 错误 1 |
| grpc_hb5s | meta | 10 / 10 | 6149 / 6634 / 5408 / 7437 | 6147 | 心跳 / emitter 错误 7, send failed 3 |
| grpc_hb5s | tool_start | 10 / 10 | 4210 / 5081 / 4173 / 5144 | 4208 | send failed 6, 心跳 / emitter 错误 4 |
| grpc_hb5s | token | 10 / 10 | 4 / 17 / 2 / 40 | 0 | send failed 6, 心跳 / emitter 错误 4 |
| http_hb1s | meta | 10 / 10 | 1997 / 2916 / 1220 / 2992 | 1990 | send failed 2, 心跳 / emitter 错误 8 |
| http_hb1s | tool_start | 10 / 10 | 3788 / 4676 / 3549 / 4871 | 3786 | 心跳 / emitter 错误 10 |
| http_hb1s | token | 10 / 10 | 4 / 13 / 1 / 16 | 4 | send failed 8, 心跳 / emitter 错误 2 |
| http_hb5s | meta | 10 / 10 | 4998 / 5879 / 4939 / 5968 | 4996 | 心跳 / emitter 错误 9, send failed 1 |
| http_hb5s | tool_start | 10 / 10 | 4211 / 4298 / 4166 / 5180 | 4210 | 心跳 / emitter 错误 4, send failed 6 |
| http_hb5s | token | 10 / 10 | 6 / 15 / 0 / 22 | 5 | 心跳 / emitter 错误 1, send failed 9 |

### 结论

1. **取消本身在两种传输下都是即时的**：每一次尝试里，backend 发现客户端断开的时刻与 ai-service 记录取消的时刻只差几毫秒到一百多毫秒（120 次里差值中位数 2 ms，111 次 < 20 ms，最大 113 ms）——HTTP 是关闭连接，gRPC 是 `ClientCall.cancel`（RST_STREAM），到 Python 的 Agent 生成器被取消都不需要等心跳。**所以 HTTP 与 gRPC 在「断开 → Python 取消」上没有可分辨的差别**；延迟几乎全部来自 backend 发现浏览器断开得有多晚（Tomcat 只有写失败才知道，S6-4）。
2. **事件正在流动时（`token` 断开点）几乎立刻取消**：中位数 3–6 ms，最大 52 ms，四个组合一致（下一个 token 的写失败即触发）。
3. **安静期（还没有事件流动）的延迟由心跳间隔决定**：`meta` 断开点，心跳 5 s 时中位数 gRPC 6149 / HTTP 4998 ms（范围 4939–7437），心跳 1 s 时 1988 / 1997 ms（范围 1220–2992）——**把 `CHAT_HEARTBEAT` 从 5 s 改成 1 s，把这一类延迟缩短了约 3 倍**（5–7 s → 1.2–3.0 s）。gRPC 与 HTTP 的差（6149 vs 4998）在 n = 10 下不能解读为传输差别：5 s 心跳下延迟取决于断开发生在心跳周期的哪个相位和 LLM 首个事件何时到达，两组的范围重叠，而 1 s 心跳下两者相同。
4. **工具执行期（`tool_start` 断开点）几乎没有改善**：心跳 5 s 时中位数 4210 / 4211 ms（gRPC / HTTP），1 s 时 3624 / 3788 ms。逐条看，检测发生在请求开始后约 5.0 s（或 6.0 s）——与 `tool_end` 事件（检索工具约 3.7 s）的写入时刻重合，而 1 s 的心跳在这之前并没有检测到断开。**原因没有查清**（怀疑与 docker 的用户态端口代理处理半关闭的方式有关，没有在直连环境下复验），登记在 LIMITATIONS S9-5。
5. **取舍与已采取的改动**：`CHAT_HEARTBEAT` 默认值从 5 s 改为 1 s（每个活跃对话每秒多写一行 `: ping`，约 8 字节，可忽略）；这是「顺手缩短」，不是对该延迟的根治——根治需要 backend 在不依赖写失败的情况下发现断开（例如 Servlet 容器的断开事件），不在 S9 范围内。
6. **局限**：n = 10 / 单元格，只有 LLM 给出的那一种问题；断开点是代码里固定的三个，真实用户可能在任何时刻断开；容器网络里多了一层 docker 端口代理，直连部署的数字可能不同；没有测断开后 LLM 费用的节省（上游一旦取消，LangGraph 与 LLM 流式请求随之中止，但供应商侧已生成的 token 仍会计费）。

## 3. 对 S9 验收的意义

- 取消传播：Python 服务端有单测（`test_client_cancel_reaches_the_agent_generator_quickly`：客户端 `cancel()` 后 Agent 生成器在 500 ms 内收到 `CancelledError`，不依赖心跳；`test_deadline_exceeded_cancels_the_agent…`），Java 客户端有 in-process 单测（`cancelReachesTheServerQuicklyAndSilencesTheListener` 等），全栈上有上表的 120 次实测与 e2e 的 `chat_stream_cancelled … transport=grpc` 日志。
- 传输延迟预实验：gRPC 在突发事件流下总耗时更高（Python grpc.aio 的每事件开销），在有节奏的流下与 HTTP 无差别，首事件更早；对真实对话没有实际影响。
