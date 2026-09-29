# mcp-tools（包名 `fund_mcp_tools`）

MCP 工具服务（FastMCP，streamable HTTP）。任何 MCP 客户端（MCP Inspector、Claude Desktop、B5 的 LangGraph Agent）都能直接调用。

| 文件 | 内容 |
|---|---|
| `server.py` | 注册四个工具的 MCP Server；`/health`；`python -m fund_mcp_tools.server` 启动 |
| `sql_guard.py` | sqlglot 解析，只放行单条 SELECT；执行的是从语法树重新生成的 SQL（注释被丢弃）；LIMIT 200 |
| `returns.py` | `calc_fund_return`：复权口径、非交易日取前一交易日、含费 / 不含费；口径见 ADR-036 |
| `nav_client.py` | `get_latest_nav`：东方财富接口，超时 3s / 重试 2 次 / 缓存 10 分钟，失败回退快照并标 `stale` |
| `schema_info.py` | `get_fund_db_schema`：表结构、字段含义、示例行、口径说明 |
| `db.py` / `data_access.py` | `fund_reader` 只读连接（只读事务 + 服务端超时）；把表读成各工具的输入 |
| `config.py` | pydantic-settings，变量见根目录 `.env.example` |

## 工具

地址 `http://127.0.0.1:8101/mcp`（`MCP_TOOLS_HOST` / `MCP_TOOLS_PORT`）。工具出错时 MCP 结果 `isError=true`，文本里说明原因，Agent 可据此修正后重试。

| 工具 | 入参 | 返回要点 |
|---|---|---|
| `get_fund_db_schema` | 无 | `tables`（含字段中文含义、行数、示例行）、`conventions`（比率用小数、份额代码 vs 基金代码…）、`source`、`as_of` |
| `run_fund_sql` | `sql` | `columns`、`rows`、`row_count`、`truncated`、`executed_sql`、`tables`、`source`、`as_of`。只允许单条 SELECT；禁止 DML/DDL、多语句、系统库、INTO OUTFILE、危险函数；最多 200 行；5s 超时 |
| `calc_fund_return` | `share_code, start, end, include_fees=false, amount=null` | `start_used` / `end_used`（实际使用的起止日）、`return`、`annualized_return`、`max_drawdown`、`dividends_in_range`、`net_return_with_fees`（含费时）、`display`（百分数字符串）、`notes`、`source`、`as_of` |
| `get_latest_nav` | `share_code` | `nav_date`、`unit_nav`、`fetched_at`、`source`、`stale`；回退快照时 `stale=true` 并带 `snapshot_as_of`、`fallback_reason` |

数值约定：所有比率（收益率、回撤、费率）都是小数，`0.012` = 1.20%；`display` 字段里才是带 `%` 的字符串。

## 本地开发

```bash
cd mcp-tools
python -m venv .venv            # Windows 下用 Python 3.12
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest -m "not integration and not slow and not live"   # CI 同款，不需要任何外部服务
NO_PROXY=127.0.0.1,localhost .venv/Scripts/python -m pytest -m integration       # 需要 infra 里的 MySQL
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
NO_PROXY=127.0.0.1,localhost .venv/Scripts/python -m fund_mcp_tools.server        # 启动服务
```

## 验证

- 与参考脚本逐位比对收益计算：`scripts/verify_returns_vs_reference.py`（结果在 `reports/mcp_tools/`）。
- 独立 MCP 客户端调用全部工具：`scripts/mcp_client_check.py`（原文在 `reports/mcp_tools/*_client_check/`）。
