# mcp-tools：基金库只读 SQL、收益计算、最新净值三个 MCP 工具（streamable HTTP）。
# 构建上下文是仓库根：docker compose --profile app build mcp-tools
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd -u 10001 -m app && mkdir -p /app && chown app:app /app
WORKDIR /app/mcp-tools

COPY mcp-tools/pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

COPY --chown=app:app mcp-tools/src ./src
RUN pip install --no-deps -e .

USER app

# 容器里必须监听 0.0.0.0（本机开发默认只监听回环）
ENV MCP_TOOLS_HOST=0.0.0.0 \
    MCP_TOOLS_PORT=8101

EXPOSE 8101
HEALTHCHECK --interval=15s --timeout=8s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8101/health', timeout=6)"

CMD ["python", "-m", "fund_mcp_tools.server"]
