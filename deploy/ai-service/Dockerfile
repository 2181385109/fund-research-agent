# ai-service：FastAPI + LangGraph Agent + 文档检索 MCP + 本地向量 / 重排模型（CPU 版 torch）。
# 构建上下文是仓库根：docker compose --profile app build ai-service
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 非 root（uid 10001）。backend 镜像用同一个 uid：上传目录是两个容器共享的命名卷，
# 卷第一次创建时继承镜像里该目录的属主，两边 uid 一致才都能读写。
# 代码放在 /app/ai-service，使 config.py 里的 REPO_ROOT 解析为 /app，与本机开发的目录布局一致
# （data/MANIFEST.json、data/raw、reports/ 都相对它）。
RUN useradd -u 10001 -m app \
    && mkdir -p /app/data/uploads /app/reports /models \
    && chown -R app:app /app /models

WORKDIR /app/ai-service

# 1) CPU 版 torch 先装：否则 sentence-transformers 会拉带 CUDA 的默认轮子（数 GB）
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# 国内网络访问 PyPI 很慢时可传 --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/（默认不设，行为不变）
ARG PIP_INDEX_URL
# 2) 第三方依赖只取决于 pyproject.toml：改源码不会让这一层缓存失效
COPY ai-service/pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['model']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

# 3) 源码（可编辑安装，仅注册包，不再解析依赖）
COPY --chown=app:app ai-service/src ./src
RUN pip install --no-deps -e .

USER app

# 模型缓存在命名卷 /models；数据目录 /app/data 由 compose 挂载（PDF 与快照不进镜像）
ENV MODEL_CACHE_DIR=/models \
    DATA_DIR=/app/data \
    AI_SERVICE_PORT=8001

EXPOSE 8001 50051
HEALTHCHECK --interval=15s --timeout=8s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=6)"

# 单 worker：文档入库任务队列与检索服务都是进程内单例
CMD ["uvicorn", "fund_ai.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
