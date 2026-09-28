# mcp-tools（包名 `fund_mcp_tools`）

MCP 工具服务（FastMCP，streamable HTTP，端口 `MCP_TOOLS_PORT`，默认 8101）。

| 文件 | 阶段 | 内容 |
|---|---|---|
| `config.py` | S0 ✅ | pydantic-settings 配置（只读账号、净值接口地址、DATA_AS_OF） |
| `sql_guard.py` | S5（占位） | sqlglot 解析，只放行单条 SELECT，自动 LIMIT 200，超时 5s |
| `returns.py` | S5（占位） | `calc_fund_return`：复权口径、非交易日就近取值、含费/不含费 |
| `nav_client.py` | S5（占位） | `get_latest_nav`：东方财富接口，超时/重试/缓存，失败回退快照并标 `stale` |
| `server.py` | S5（占位） | 注册四个工具的 MCP Server |

## 本地开发

```bash
cd mcp-tools
python -m venv .venv            # Windows 下用 Python 3.12
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -m "not integration and not slow and not live"
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
```
