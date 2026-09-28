# backend（Java / Spring Boot 3.5，groupId `com.fundagent`）

S0：`GET /api/health` 检查 MySQL（连接池取连接 + `isValid`）和 Redis（PING），每项给出 UP/DOWN 与耗时，任一 DOWN 返回 503；统一响应体 `ApiResponse<T>`、全局异常处理、requestId 过滤器、Flyway 基线（`V1__baseline.sql`）。其余包是 `package-info.java` 占位，注明所属阶段。

配置全部来自环境变量；本机开发时自动读取仓库根目录的 `.env`（`spring.config.import`）。

## 本地运行

需要 JDK 17 与 Maven 3.9（本机位置与环境变量设置见 [docs/SETUP.md](../docs/SETUP.md)）。

```bash
cd backend
mvn -B verify                                  # 编译 + 单测
java -Dfile.encoding=UTF-8 -jar target/backend-0.0.1-SNAPSHOT.jar
curl -s http://127.0.0.1:8081/api/health
```
