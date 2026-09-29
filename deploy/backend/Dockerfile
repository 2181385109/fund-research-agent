# backend：Spring Boot（JDK 17）。两阶段构建：Maven 打包 → 只带 JRE 的运行镜像。
# 构建上下文是仓库根：docker compose --profile app build backend
# 镜像构建跳过测试（-DskipTests）：单测与 Testcontainers 集成测试在 CI 的 backend job 里跑（mvn verify）。
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /build
# 依赖只取决于 pom.xml：改源码不会让依赖下载这一层失效
COPY backend/pom.xml ./
RUN mvn -B -ntp -q dependency:go-offline
COPY backend/src ./src
RUN mvn -B -ntp -q -DskipTests package \
    && cp "$(ls target/backend-*.jar | grep -v original | head -n 1)" /build/app.jar

FROM eclipse-temurin:17-jre-jammy
# 非 root（uid 10001，与 ai-service 镜像一致）。上传目录 /app/data/uploads 是与 ai-service 共享的命名卷，
# 且路径在两个容器里必须完全一致（ai-service 按这个路径读文件）。
RUN useradd -u 10001 -m app \
    && mkdir -p /app/data/uploads \
    && chown -R app:app /app
WORKDIR /app
COPY --from=build --chown=app:app /build/app.jar ./app.jar
USER app

ENV JAVA_TOOL_OPTIONS="-Dfile.encoding=UTF-8" \
    UPLOAD_DIR=/app/data/uploads

EXPOSE 8081
# 运行镜像没有 curl / wget：用 bash 的 /dev/tcp 发一个 HTTP 请求，状态行必须是 200
HEALTHCHECK --interval=15s --timeout=8s --start-period=60s --retries=5 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/8081 && printf "GET /api/health HTTP/1.0\r\n\r\n" >&3 && head -n 1 <&3 | grep -q " 200 "'

# 堆 512m（PLAN §3：容器上限 768m，其余给元空间、线程栈与直接内存）
ENTRYPOINT ["java", "-Xmx512m", "-XX:+ExitOnOutOfMemoryError", "-jar", "/app/app.jar"]
