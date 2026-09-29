"""S5 验收 2：mcp-tools 的 calc_fund_return 与参考脚本 eval/reference/gold.py 逐位比对。

被测端走真实链路：fund_reader 账号读 MySQL fund_data → `DbReturnData` → `calc_fund_return`；
参考端只读冻结快照 CSV（pandas），两条路径互相独立。比较用 `==`（不是近似相等）。

运行环境：eval 的 venv，并且已安装 mcp-tools（只为 import；参考脚本自身不 import mcp-tools）：
    cd eval && .venv/Scripts/python -m pip install -e ../mcp-tools
    cd eval && .venv/Scripts/python ../scripts/verify_returns_vs_reference.py
需要 infra 里的 MySQL 在跑（NO_PROXY=127.0.0.1,localhost），结果写入 reports/mcp_tools/<UTC 时间戳>/。

比对内容：
1. 全部 40 个份额 × 多种起止日组合（季末、周末 / 节假日起止、成立至今、每次分红前后、seed 固定的随机窗口），
   不含费：区间收益、年化、最大回撤、实际起止日、区间内分红次数；
2. 含费：每个份额抽取的窗口 × 5 档申购金额（含固定费用档），净收益；
3. `agent_tasks_v1.jsonl` 里全部 calc_return 题的 gold_params（数据集里保存的是 8 位小数）。
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import date, timedelta

from fund_mcp_tools.config import Settings
from fund_mcp_tools.data_access import DbReturnData
from fund_mcp_tools.db import MySQLFundDB
from fund_mcp_tools.returns import ReturnsError, calc_fund_return
from reference import gold
from reference.common import DATASETS_DIR, table
from reference.reporting import env_block, new_report_dir, write_json, write_text

SEED = 20260929
RANDOM_WINDOWS_PER_SHARE = 25
AMOUNTS = [1_000.0, 50_000.0, 999_999.0, 1_000_000.0, 5_000_000.0]
FIELDS = ["start_used", "end_used", "ret", "annualized", "max_drawdown", "dividends_in_range"]


class CachedData(DbReturnData):
    """同一份额只从数据库读一次（数据路径仍然是真实的 MySQL 查询）。"""

    def __init__(self, db) -> None:
        super().__init__(db)
        self._c: dict = {}

    def _memo(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    def has_share(self, s):
        return self._memo(("h", s), lambda: super(CachedData, self).has_share(s))

    def nav_rows(self, s):
        return self._memo(("n", s), lambda: super(CachedData, self).nav_rows(s))

    def dividends(self, s):
        return self._memo(("d", s), lambda: super(CachedData, self).dividends(s))

    def purchase_tiers(self, s):
        return self._memo(("p", s), lambda: super(CachedData, self).purchase_tiers(s))

    def redemption_tiers(self, s):
        return self._memo(("r", s), lambda: super(CachedData, self).redemption_tiers(s))


def windows_for(share: str, rng: random.Random) -> list[tuple[str, str]]:
    n = gold.nav_series(share)
    first, last = date.fromisoformat(n.nav_date.iloc[0]), date.fromisoformat(n.nav_date.iloc[-1])
    ws: set[tuple[str, str]] = set()
    # 成立（首个净值日）至今；含首个净值日之前的起始日（两边都应报错）
    ws.add((first.isoformat(), last.isoformat()))
    ws.add(((first - timedelta(days=5)).isoformat(), last.isoformat()))
    # 常用的季末窗口，其中很多起止日是周末 / 节假日
    for s, e in [
        ("2023-12-31", "2024-12-31"),
        ("2024-12-31", "2025-06-30"),
        ("2025-06-30", "2026-06-30"),
        ("2025-12-31", "2026-06-30"),
        ("2026-03-31", "2026-06-30"),
        ("2026-01-01", "2026-09-28"),
        ("2022-12-31", "2023-12-31"),
        ("2026-09-26", "2026-09-28"),  # 起点是周六
        ("2026-09-28", "2026-09-28"),  # 起止同一天
        ("2026-09-20", "2030-01-01"),  # 止日晚于快照
    ]:
        ws.add((s, e))
    # 每一次分红前后各 10 个自然日
    d = gold.dividends(share)
    for ex in d.ex_date:
        x = date.fromisoformat(ex)
        ws.add(((x - timedelta(days=10)).isoformat(), (x + timedelta(days=10)).isoformat()))
        ws.add(((x - timedelta(days=1)).isoformat(), ex))
        ws.add((ex, (x + timedelta(days=30)).isoformat()))
    # seed 固定的随机窗口（起止日不限于交易日）
    span = (last - first).days
    for _ in range(RANDOM_WINDOWS_PER_SHARE):
        a = first + timedelta(days=rng.randint(0, max(span - 2, 0)))
        b = a + timedelta(days=rng.randint(0, max((last - a).days, 0)))
        ws.add((a.isoformat(), b.isoformat()))
    return sorted(ws)


def run_pair(data, share, start, end, fees, amount):
    """返回 (tool_result | None, gold_result | None, tool_err, gold_err)。"""
    t = g = None
    te = ge = None
    try:
        t = calc_fund_return(data, share, start, end, include_fees=fees, amount=amount)
    except ReturnsError as e:
        te = str(e)
    try:
        g = gold.calc_return(share, start, end, include_fees=fees, amount=amount)
    except ValueError as e:
        ge = str(e)
    return t, g, te, ge


def compare(t: dict, g) -> list[tuple[str, object, object]]:
    diffs = []
    pairs = {
        "start_used": (t["start_used"], g.start_used),
        "end_used": (t["end_used"], g.end_used),
        "ret": (t["return"], g.ret),
        "annualized": (t["annualized_return"], g.annualized),
        "max_drawdown": (t["max_drawdown"], g.max_drawdown),
        "dividends_in_range": (t["dividends_in_range"], g.dividends_in_range),
    }
    if g.net_ret_with_fees is not None or t["net_return_with_fees"] is not None:
        pairs["net_ret_with_fees"] = (t["net_return_with_fees"], g.net_ret_with_fees)
    for k, (a, b) in pairs.items():
        if a != b:  # 逐位相等，不用近似比较
            diffs.append((k, a, b))
    return diffs


def main() -> int:
    data = CachedData(MySQLFundDB(Settings()))
    rng = random.Random(SEED)
    shares = sorted(table("share_classes").share_code)

    stats: Counter = Counter()
    mismatches: list[dict] = []
    field_ok: Counter = Counter()
    field_n: Counter = Counter()
    per_share: dict[str, dict] = {}

    def check(share, start, end, fees, amount, tag):
        t, g, te, ge = run_pair(data, share, start, end, fees, amount)
        stats[f"cases_{tag}"] += 1
        if te or ge:
            if te and ge:
                stats["both_raise"] += 1  # 都判为无法计算（起点早于首个净值日等）
                return
            mismatches.append(
                {
                    "share": share,
                    "start": start,
                    "end": end,
                    "fees": fees,
                    "amount": amount,
                    "kind": "raise_mismatch",
                    "tool_error": te,
                    "gold_error": ge,
                }
            )
            return
        diffs = compare(t, g)
        keys = ["start_used", "end_used", "ret", "annualized", "max_drawdown", "dividends_in_range"]
        if fees:
            keys.append("net_ret_with_fees")
        for k in keys:
            field_n[k] += 1
        bad = {d[0] for d in diffs}
        for k in keys:
            if k not in bad:
                field_ok[k] += 1
        stats["compared"] += 1
        if not diffs:
            stats["all_fields_identical"] += 1
        for k, a, b in diffs:
            mismatches.append(
                {
                    "share": share,
                    "start": start,
                    "end": end,
                    "fees": fees,
                    "amount": amount,
                    "field": k,
                    "tool": a,
                    "gold": b,
                }
            )

    for share in shares:
        ws = windows_for(share, rng)
        n0 = stats["compared"]
        for i, (s, e) in enumerate(ws):
            check(share, s, e, False, None, "no_fee")
            if i % 3 == 0:
                for amt in AMOUNTS:
                    check(share, s, e, True, amt, "with_fee")
        per_share[share] = {"windows": len(ws), "compared": stats["compared"] - n0}

    # 数据集 calc_return 题：gold_params 是 8 位小数
    ds = [
        json.loads(x)
        for x in (DATASETS_DIR / "agent_tasks_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if x
    ]
    ds_items = [r for r in ds if r.get("topic") == "calc_return"]
    ds_results = []
    for r in ds_items:
        p = r["gold_params"]
        t = calc_fund_return(
            data, p["share_code"], p["start"], p["end"], p["include_fees"], p["amount"]
        )
        got = {
            "start_used": t["start_used"],
            "end_used": t["end_used"],
            "ret": round(t["return"], 8),
            "annualized": round(t["annualized_return"], 8),
            "max_drawdown": round(t["max_drawdown"], 8),
            "net_ret_with_fees": None
            if t["net_return_with_fees"] is None
            else round(t["net_return_with_fees"], 8),
        }
        want = {k: p[k] for k in got}
        ds_results.append({"id": r["id"], "match": got == want, "got": got, "want": want})

    summary = {
        "what": "mcp-tools calc_fund_return 与 eval/reference/gold.py::calc_return 逐位比对（== 比较）",
        **env_block([DATASETS_DIR / "agent_tasks_v1.jsonl"]),
        "command": "cd eval && python ../scripts/verify_returns_vs_reference.py",
        "params": {
            "seed": SEED,
            "random_windows_per_share": RANDOM_WINDOWS_PER_SHARE,
            "amounts": AMOUNTS,
            "n_shares": len(shares),
        },
        "counts": dict(stats),
        "field_identical": {k: {"identical": field_ok[k], "compared": field_n[k]} for k in field_n},
        "n_mismatches": len(mismatches),
        "dataset_calc_return": {
            "n": len(ds_results),
            "matched": sum(1 for x in ds_results if x["match"]),
            "items": ds_results,
        },
    }
    out = new_report_dir("mcp_tools")
    write_json(out / "summary.json", summary)
    write_json(out / "mismatches.json", mismatches)
    lines = [
        "# calc_fund_return 与参考脚本逐位比对",
        "",
        f"- 份额 {len(shares)} 个；用例共 {stats['cases_no_fee'] + stats['cases_with_fee']} 条"
        f"（不含费 {stats['cases_no_fee']}、含费 {stats['cases_with_fee']}）；"
        f"两边都判为无法计算（起点早于首个净值日）{stats['both_raise']} 条；参与逐位比较 {stats['compared']} 条",
        f"- 全部字段逐位相同的用例：{stats['all_fields_identical']} / {stats['compared']}",
        f"- 不一致：{len(mismatches)} 处（见 mismatches.json）",
        "",
        "| 字段 | 逐位相同 | 参与比较 |",
        "|---|---|---|",
        *[f"| {k} | {field_ok[k]} | {field_n[k]} |" for k in field_n],
        "",
        f"- 数据集 calc_return 题（gold_params，8 位小数）：匹配 {summary['dataset_calc_return']['matched']}"
        f" / {summary['dataset_calc_return']['n']}",
        "",
        f"复现：`{summary['command']}`（seed={SEED}）",
        "",
    ]
    write_text(out / "report.md", "\n".join(lines))
    print(
        json.dumps(
            {k: summary[k] for k in ("counts", "field_identical", "n_mismatches")},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        "dataset calc_return:",
        summary["dataset_calc_return"]["matched"],
        "/",
        summary["dataset_calc_return"]["n"],
    )
    print("report:", out)
    return 0 if not mismatches and all(x["match"] for x in ds_results) else 1


if __name__ == "__main__":
    sys.exit(main())
