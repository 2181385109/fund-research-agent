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
