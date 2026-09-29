# backend（Java / Spring Boot 3.5，groupId `com.fundagent`）

主后端：用户、知识库、文档、会话、对话（SSE 代理 ai-service）。接口与错误码以 [docs/API.md](../docs/API.md) 为准，交互式文档 `/swagger-ui.html`。

## 包结构

| 包 | 内容 |
|---|---|
| `auth` | 注册 / 登录（BCrypt + JWT，无角色）；`AuthInterceptor` 校验 `Authorization: Bearer`，`InternalAuthInterceptor` 校验 `/internal/**` 的共享密钥 |
| `kb` | 公共库（`id=1`，只读）+ 用户私有库；`KbService.resolveScope` 算出每次提问的检索范围（越权在这里被拒） |
| `document` | 上传（魔数 / 大小 / sha256 去重）、状态机 PENDING→PROCESSING→READY/FAILED、ai-service 回调、超时清理 |
| `conversation` | 会话与消息持久化（答案、出处、风险提示、检索范围），取最近 N 轮上下文 |
| `chat` | `SseEmitter` 代理 `POST /v1/chat/stream`：原样转发事件、累积落库、心跳、客户端断开即取消上游 |
| `aiclient` | `AiServiceClient` 接口 + `HttpAiServiceClient`（第二期 S9 再加 gRPC 实现） |
| `common` `config` `health` | 统一响应体 / 错误码 / 全局异常 / requestId；配置属性；`GET /api/health` |

每个业务包的 `service` 子包放业务逻辑，`mapper` 放 MyBatis-Plus Mapper，`dto` 放请求 / 响应对象。Flyway 迁移在 `src/main/resources/db/migration`（只增不改）。

## 配置（环境变量，见根目录 `.env.example`）

`JWT_SECRET`、`INTERNAL_CALLBACK_SECRET`、`AI_SERVICE_BASE_URL`、`UPLOAD_DIR`、`MYSQL_*`、`REDIS_*`，以及可调项 `JWT_TTL`、`UPLOAD_STALE_AFTER`、`AI_CHAT_IDLE_TIMEOUT`、`CHAT_HISTORY_ROUNDS`、`CHAT_HEARTBEAT`。
本机开发时自动读取仓库根目录的 `.env`（`spring.config.import`）。

## 本地运行与测试

需要 JDK 17 与 Maven 3.9（本机位置与环境变量设置见 [docs/SETUP.md](../docs/SETUP.md)）。

```bash
cd backend
mvn -B verify                                  # 编译 + 单测 + Testcontainers 集成测试 + JaCoCo 检查
java -Dfile.encoding=UTF-8 -jar target/backend-0.0.1-SNAPSHOT.jar
curl -s http://127.0.0.1:8081/api/health
```

`mvn verify` 需要能连到 Docker（Testcontainers 起 `mysql:8.4.11`）；本机 Docker 在 WSL 里，见 [docs/SETUP.md §6.2](../docs/SETUP.md)。
JaCoCo 规则：每个 `*.service` 包的行覆盖率 ≥ 70%（S6 验收 1），报告在 `target/site/jacoco/`。

端到端冒烟（需要 ai-service、mcp-tools 与真实 LLM）：`bash scripts/e2e_smoke.sh`，说明见脚本顶部。
