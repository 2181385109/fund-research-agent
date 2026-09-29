"""get_fund_db_schema：表结构、字段中文含义、示例值（PLAN S5）。

字段含义来自建表时写在库里的 COMMENT（`fund_pipeline/schema.sql`），行数与示例值实时查询；
结果在进程内缓存（快照冻结，库结构不会变）。
"""

from __future__ import annotations

import threading

from fund_mcp_tools.db import FundDB, json_value

# 给 Agent 的口径说明：这些是写 SQL 时最容易出错的地方
CONVENTIONS = [
    "所有比率（费率、占净值比例 weight、收益率 ret、日增长率 daily_return）都存小数：0.012 表示 1.20%。",
    "金额单位是元（net_assets、market_value）；持股数单位是股；净值单位是元/份。",
    "份额级的表（share_classes、fees、purchase_fee_tiers、redemption_fee_tiers、nav_daily、dividends、"
    "period_returns）用 share_code（6 位份额代码，A/C 类各一个）；基金级的表（funds、holdings_top10、"
    "fund_scale、fund_manager_tenures）用 fund_code（主代码）。两者通过 share_classes 关联。",
    "查份额级的表时必须用 share_code 或 share_class 限定到具体份额（A 类和 C 类费率、净值都不同）。",
    "每张表都有 source（数据来源）和 as_of（快照截止日）；引用数据时要带上这两项。",
    "holdings_top10.report_period 形如 '2026Q2'；只有前十大重仓股，没有全部持仓。",
    "period_returns.period 取值：1w 1m 3m 6m 1y 2y 3y ytd since_inception；是来源方给出的区间收益，"
    "口径与 calc_fund_return 的复权口径可能有差异。",
    "fund_manager_tenures.end_date 为 NULL 表示截至 as_of 仍在任。",
    "fund_scale.net_assets 是全部份额合计的基金资产净值；只有报告期末的数据点。",
    "nav_daily 里带有少量周末日期（季末 / 年末披露的净值），它们不是交易日。收益、年化、最大回撤请用 "
    "calc_fund_return 计算，不要自己用 SQL 或心算；最新净值用 get_latest_nav。",
    "只能写单条 SELECT；结果最多 200 行，超出会标记 truncated=true；统计类问题请用聚合而不是拉全量。",
]


def _short(v, limit: int = 24):
    v = json_value(v)
    if isinstance(v, str) and len(v) > limit:
        return v[:limit] + "…"
    return v


class SchemaProvider:
    def __init__(self, db: FundDB, *, sample_rows: int = 1) -> None:
        self._db = db
        self._n = sample_rows
        self._cache: dict | None = None
        self._lock = threading.Lock()

    def describe(self, *, as_of: str = "") -> dict:
        with self._lock:
            if self._cache is None:
                self._cache = self._build()
        return {**self._cache, "as_of": as_of or self._cache.get("as_of", "")}

    def _build(self) -> dict:
        db = self._db
        tables = db.query(
            "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
        ).rows
        cols = db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY, IS_NULLABLE, COLUMN_COMMENT "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        ).rows
        # 字段写成一行文字（名称 类型 [PK] — 含义）：完整 JSON 结构在 Agent 上下文里太占 token
        by_table: dict[str, list[str]] = {}
        for t, name, ctype, key, _nullable, comment in cols:
            line = f"{name} {ctype}{' [PK]' if key == 'PRI' else ''}"
            by_table.setdefault(t, []).append(f"{line} — {comment}" if comment else line)
        out = []
        for name, comment in tables:
            # 表名来自 information_schema，不是用户输入；仍然用反引号包起来
            count = db.query(f"SELECT COUNT(*) FROM `{name}`").rows[0][0]
            sample = db.query(f"SELECT * FROM `{name}` LIMIT {int(self._n)}")
            out.append(
                {
                    "name": name,
                    "meaning": comment or "",
                    "row_count": int(count),
                    "columns": by_table.get(name, []),
                    "sample": [
                        "; ".join(
                            f"{c}={_short(v)}" for c, v in zip(sample.columns, row, strict=True)
                        )
                        for row in sample.rows
                    ],
                }
            )
        as_of = ""
        try:
            r = db.query("SELECT MAX(as_of) FROM funds").rows
            as_of = json_value(r[0][0]) if r and r[0][0] else ""
        except Exception:  # noqa: BLE001 - 缺 funds 表时不影响结构说明
            pass
        return {
            "database": "fund_data",
            "source": "fund_data（MySQL 快照库，只读）",
            "as_of": as_of,
            "conventions": CONVENTIONS,
            "tables": out,
        }
