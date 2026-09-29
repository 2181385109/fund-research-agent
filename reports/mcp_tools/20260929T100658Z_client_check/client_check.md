# MCP 客户端冒烟（独立客户端，streamable HTTP）
时间：2026-09-29T10:06:50+00:00

## 服务 http://127.0.0.1:8101/mcp
server: fund-tools 1.30.0；协议 2025-11-25
list_tools → 4 个工具：get_fund_db_schema, run_fund_sql, calc_fund_return, get_latest_nav
- `get_fund_db_schema`() — 返回基金数据库（fund_data）的全部表结构：每张表的中文含义、行数、字段名 / 类型 / 中文含义、
- `run_fund_sql`(sql:string) — 在基金数据库上执行**只读**的单条 SELECT 查询（MySQL 语法）。禁止写操作、多语句、系统库、
- `calc_fund_return`(share_code:string, start:string, end:string, include_fees:boolean, amount:?) — 确定性地计算某个份额在 [start, end] 的收益：区间收益率、年化收益率、最大回撤（分红按除息日
- `get_latest_nav`(share_code:string) — 查询某个份额的最新单位净值（实时调用东方财富接口，缓存 10 分钟）。返回 nav_date（净值日期）、

### get_fund_db_schema — 表结构
入参：{}
isError=False（期望 False）✅
```
{
  "database": "fund_data",
  "source": "fund_data（MySQL 快照库，只读）",
  "as_of": "2026-09-28",
  "conventions": [
    "所有比率（费率、占净值比例 weight、收益率 ret、日增长率 daily_return）都存小数：0.012 表示 1.20%。",
    "金额单位是元（net_assets、market_value）；持股数单位是股；净值单位是元/份。",
    "份额级的表（share_classes、fees、purchase_fee_tiers、redemption_fee_tiers、nav_daily、dividends、period_returns）用 share_code（6 位份额代码，A/C 类各一个）；基金级的表（funds、holdings_top10、fund_scale、fund_manager_tenures）用 fund_code（主代码）。两者通过 share_classes 关联。",
    "查份额级的表时必须用 share_code 或 share_class 限定到具体份额（A 类和 C 类费率、净值都不同）。",
    "每张表都有 source（数据来源）和 as_of（快照截止日）；引用数据时要带上这两项。",
    "holdings_top10.report_period 形如 '2026Q2'；只有前十大重仓股，没有全部持仓。",
    "period_returns.period 取值：1w 1m 3m 6m 1y 2y 3y ytd since_inception；是来源方给出的区间收益，口径与 calc_fund_return 的复权口径可能有差异。",
    "fund_manager_tenures.end_date 为 NULL 表示截至 as_of 仍在任。",
    "fund_scale.net_assets 是全部份额合计的基金资产净值；只有报告期末的数据点。…（共 8651 字符，已截断）
```

### run_fund_sql — 正常查询
入参：{"sql": "SELECT fund_code, fund_name, theme FROM funds ORDER BY fund_code LIMIT 3"}
isError=False（期望 False）✅
```
{
  "columns": [
    "fund_code",
    "fund_name",
    "theme"
  ],
  "rows": [
    [
      "000452",
      "南方医药保健混合",
      "医药医疗"
    ],
    [
      "001513",
      "易方达信息产业混合",
      "科技"
    ],
    [
      "001550",
      "天弘中证医药100",
      "医药医疗"
    ]
  ],
  "row_count": 3,
  "truncated": false,
  "max_rows": 200,
  "executed_sql": "SELECT fund_code, fund_name, theme FROM funds ORDER BY fund_code LIMIT 3",
  "tables": [
    "funds"
  ],
  "source": "fund_data（MySQL 快照库）表 funds",
  "as_of": "2026-09-28"
}
```

### run_fund_sql — 聚合：每个主题的基金数
入参：{"sql": "SELECT theme, COUNT(*) AS n FROM funds GROUP BY theme ORDER BY theme"}
isError=False（期望 False）✅
```
{
  "columns": [
    "theme",
    "n"
  ],
  "rows": [
    [
      "医药医疗",
      10
    ],
    [
      "科技",
      10
    ]
  ],
  "row_count": 2,
  "truncated": false,
  "max_rows": 200,
  "executed_sql": "SELECT theme, COUNT(*) AS n FROM funds GROUP BY theme ORDER BY theme LIMIT 201",
  "tables": [
    "funds"
  ],
  "source": "fund_data（MySQL 快照库）表 funds",
  "as_of": "2026-09-28"
}
```

### run_fund_sql — 被守卫拒绝：DROP
入参：{"sql": "DROP TABLE funds"}
isError=True（期望 True）✅
```
Error executing tool run_fund_sql: 只允许 SELECT 查询，收到 DROP 语句
```

### run_fund_sql — 被守卫拒绝：多语句
入参：{"sql": "SELECT 1; DELETE FROM funds"}
isError=True（期望 True）✅
```
Error executing tool run_fund_sql: 只允许单条语句，检测到 2 条（不能用分号拼接多条 SQL）
```

### run_fund_sql — 被守卫拒绝：系统库
入参：{"sql": "SELECT * FROM mysql.user"}
isError=True（期望 True）✅
```
Error executing tool run_fund_sql: 只能访问 fund_data 库中的表，不能访问 mysql （含 information_schema、mysql、performance_schema、sys）
```

### calc_fund_return — 不含费（起止日 2025-12-31 是周三，2026-06-30 是周二）
入参：{"share_code": "003095", "start": "2025-12-31", "end": "2026-06-30"}
isError=False（期望 False）✅
```
{
  "share_code": "003095",
  "requested": {
    "start": "2025-12-31",
    "end": "2026-06-30"
  },
  "start_used": "2025-12-31",
  "end_used": "2026-06-30",
  "start_unit_nav": 1.7737,
  "end_unit_nav": 1.8421,
  "calendar_days": 181,
  "trading_days": 116,
  "return": 0.03856345492473401,
  "annualized_return": 0.07929072229253142,
  "max_drawdown": -0.233666410453497,
  "dividends_in_range": 0,
  "dividend_events": [],
  "net_return_with_fees": null,
  "display": {
    "return": "3.86%",
    "annualized_return": "7.93%",
    "max_drawdown": "-23.37%"
  },
  "unit": "所有比率均为小数（0.012 = 1.20%）；display 字段已换算成百分数字符串",
  "method": "分红在除息日按单位净值再投资（复权）；遇非交易日取前一交易日净值；年化=(1+区间收益)^(365/自然日天数)-1；最大回撤按复权净值相对此前最高点计算",
  "notes": [],
  "source": "fund_data.nav_daily / fund_data.dividends（快照）",
  "as_of": "2026-09-28"
}
```

### calc_fund_return — 含费 + 起止日是周末（2026-06-27 周六）
入参：{"share_code": "003095", "start": "2026-03-01", "end": "2026-06-27", "include_fees": true, "amount": 10000}
isError=False（期望 False）✅
```
{
  "share_code": "003095",
  "requested": {
    "start": "2026-03-01",
    "end": "2026-06-27"
  },
  "start_used": "2026-02-27",
  "end_used": "2026-06-26",
  "start_unit_nav": 1.753,
  "end_unit_nav": 1.703,
  "calendar_days": 119,
  "trading_days": 80,
  "return": -0.028522532800912326,
  "annualized_return": -0.08493206544294096,
  "max_drawdown": -0.20587298215802863,
  "dividends_in_range": 0,
  "dividend_events": [],
  "net_return_with_fees": -0.04766494594769233,
  "display": {
    "return": "-2.85%",
    "annualized_return": "-8.49%",
    "max_drawdown": "-20.59%",
    "net_return_with_fees": "-4.77%"
  },
  "unit": "所有比率均为小数（0.012 = 1.20%）；display 字段已换算成百分数字符串",
  "method": "分红在除息日按单位净值再投资（复权）；遇非交易日取前一交易日净值；年化=(1+区间收益)^(365/自然日天数)-1；最大回撤按复权净值相对此前最高点计算",
  "notes": [
    "起始日 2026-03-01 不是交易日，取前一交易日 2026-02-27 的净值",
    "结束日 2026-06-27 不是交易日，取前一交易日 2026-06-26 的净值"
  ],
  "sourc…（共 1276 字符，已截断）
```

### calc_fund_return — 参数错误：含费但没给金额
入参：{"share_code": "003095", "start": "2026-03-01", "end": "2026-06-27", "include_fees": true}
isError=True（期望 True）✅
```
Error executing tool calc_fund_return: include_fees=true 需要同时给出 amount（申购金额，单位元）
```

### get_latest_nav — 最新净值
入参：{"share_code": "003095"}
isError=False（期望 False）✅
```
{
  "share_code": "003095",
  "share_name": "中欧医疗健康混合A",
  "nav_date": "2026-09-28",
  "unit_nav": 1.9515,
  "accum_nav": 2.1895,
  "daily_growth": -0.0031,
  "unit": "daily_growth 是小数（0.0085 = 0.85%）；净值单位为元/份",
  "stale": false,
  "source": "东方财富 api.fund.eastmoney.com/f10/lsjz（非官方接口）",
  "fetched_at": "2026-09-29T10:06:58+00:00",
  "cached": false
}
```

合计：调用 10 次，与期望不符 0 次
