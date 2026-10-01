# proto/fundagent/v1

`ai_service.proto`：ai-service 的 gRPC 接口（S9）。字段说明、状态码映射、与 HTTP 的差别见 [docs/API.md](../../../docs/API.md) 的「gRPC」一节；文件头的注释是约定的权威描述。

兼容规则（CLAUDE.md §4）：字段号不重用；删除字段用 `reserved` 保留字段号与名字；新增字段只用新的字段号。

## 生成桩代码

- **Python**（已提交，`ai-service/src/fundagent/v1/`）：`python scripts/gen_proto.py`（需要 `grpcio-tools==1.84.0`，ai-service 的 dev 依赖里已固定）；CI 的 `proto` job 用 `--check` 校验已提交的与 proto 一致。
- **Java**（构建时生成，不提交）：`backend/pom.xml` 里的 `protobuf-maven-plugin` 在 `generate-sources` 阶段从本目录生成消息类与 gRPC 桩代码，所以 `mvn verify` 同时校验 proto 能编译。
- 跨语言样例 `proto/testdata/chat_events.jsonl`：改了 proto 或事件样例（`ai-service/tests/golden_events.py`）后用
  `UPDATE_GOLDEN=1 python -m pytest tests/test_grpc_server.py -k golden`（在 `ai-service/` 下）重新生成。
