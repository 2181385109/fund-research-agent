# ai-service（包名 `fund_ai`）

FastAPI 服务（端口 `AI_SERVICE_PORT`，默认 8001）：文档入库、混合检索与重排、LangGraph Agent、文档 MCP Server。S0 只有健康检查。

- `config.py`：所有配置从这里读（pydantic-settings，环境变量或仓库根目录 `.env`）。
- `api/health.py`：`GET /health` 并发探测 Milvus（RESTful v2 `collections/list`）、Elasticsearch（`_cluster/health`，green/yellow 为 UP）、Redis（PING）；每项带超时 `HEALTH_TIMEOUT_SECONDS`，任一 DOWN 返回 503。
- 其余子目录是占位，README 里写明所属阶段。

## 本地开发

```bash
cd ai-service
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -m "not integration and not slow and not live"
.venv/Scripts/python -m uvicorn fund_ai.api.app:app --port 8001
curl -s http://127.0.0.1:8001/health
```
