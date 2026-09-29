"""独立 MCP 客户端：列出并调用各 MCP 服务的全部工具，把原文写入 reports/mcp_tools/<ts>/。

S5 验收 4 用。走真实的 streamable HTTP（官方 mcp SDK 的客户端，不经任何 fund_mcp_tools 代码）：
    python scripts/mcp_client_check.py --url http://127.0.0.1:8101/mcp
文档检索 MCP（ai-service，B5）起来后再加一个 `--url http://127.0.0.1:8001/mcp`。

需要 `mcp` 包（mcp-tools 的 venv 里有）；本机访问 127.0.0.1 时设置 NO_PROXY=127.0.0.1,localhost。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO_ROOT = Path(__file__).resolve().parents[1]

# 每个工具的冒烟调用：(说明, 入参, 期望是否报错)。没登记的工具只列出、不调用。
CALLS: dict[str, list[tuple[str, dict, bool]]] = {
    "get_fund_db_schema": [("表结构", {}, False)],
    "run_fund_sql": [
        (
            "正常查询",
            {"sql": "SELECT fund_code, fund_name, theme FROM funds ORDER BY fund_code LIMIT 3"},
            False,
        ),
        (
            "聚合：每个主题的基金数",
            {"sql": "SELECT theme, COUNT(*) AS n FROM funds GROUP BY theme ORDER BY theme"},
            False,
        ),
        ("被守卫拒绝：DROP", {"sql": "DROP TABLE funds"}, True),
        ("被守卫拒绝：多语句", {"sql": "SELECT 1; DELETE FROM funds"}, True),
        ("被守卫拒绝：系统库", {"sql": "SELECT * FROM mysql.user"}, True),
    ],
    "calc_fund_return": [
        (
            "不含费（起止日 2025-12-31 是周三，2026-06-30 是周二）",
            {"share_code": "003095", "start": "2025-12-31", "end": "2026-06-30"},
            False,
        ),
        (
            "含费 + 起止日是周末（2026-06-27 周六）",
            {
                "share_code": "003095",
                "start": "2026-03-01",
                "end": "2026-06-27",
                "include_fees": True,
                "amount": 10000,
            },
            False,
        ),
        (
            "参数错误：含费但没给金额",
            {
                "share_code": "003095",
                "start": "2026-03-01",
                "end": "2026-06-27",
                "include_fees": True,
            },
            True,
        ),
    ],
    "get_latest_nav": [("最新净值", {"share_code": "003095"}, False)],
    "search_fund_documents": [
        (
            "文档检索",
            {"query": "中欧医疗健康混合A 管理费率是多少", "fund_codes": ["003095"], "top_n": 3},
            False,
        )
    ],
}


def clip(text: str, n: int = 900) -> str:
    return text if len(text) <= n else text[:n] + f"…（共 {len(text)} 字符，已截断）"


async def check(url: str, out: list[str]) -> tuple[int, int]:
    called = mismatched = 0
    async with (
        streamablehttp_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        out.append(f"## 服务 {url}")
        out.append(
            f"server: {init.serverInfo.name} {init.serverInfo.version}；协议 {init.protocolVersion}"
        )
        tools = (await session.list_tools()).tools
        out.append(f"list_tools → {len(tools)} 个工具：{', '.join(t.name for t in tools)}")
        for t in tools:
            props = ", ".join(
                f"{k}:{v.get('type', '?')}" for k, v in t.inputSchema.get("properties", {}).items()
            )
            out.append(f"- `{t.name}`({props}) — {(t.description or '').splitlines()[0][:80]}")
        for t in tools:
            for label, args, expect_error in CALLS.get(t.name, []):
                res = await session.call_tool(t.name, args)
                called += 1
                ok = bool(res.isError) == expect_error
                mismatched += 0 if ok else 1
                text = res.content[0].text if res.content else ""
                out.append("")
                out.append(f"### {t.name} — {label}")
                out.append(f"入参：{json.dumps(args, ensure_ascii=False)}")
                out.append(f"isError={res.isError}（期望 {expect_error}）{'✅' if ok else '❌'}")
                out.append("```")
                out.append(clip(text))
                out.append("```")
        uncalled = [t.name for t in tools if t.name not in CALLS]
        if uncalled:
            out.append(f"\n未登记冒烟调用的工具（只列出）：{uncalled}")
    return called, mismatched


async def main_async(urls: list[str]) -> int:
    out = [
        "# MCP 客户端冒烟（独立客户端，streamable HTTP）",
        f"时间：{datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
    ]
    total_called = total_bad = 0
    for url in urls:
        c, b = await check(url, out)
        total_called += c
        total_bad += b
        out.append("")
    out.append(f"合计：调用 {total_called} 次，与期望不符 {total_bad} 次")
    text = "\n".join(out) + "\n"
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    d = REPO_ROOT / "reports" / "mcp_tools" / f"{ts}_client_check"
    d.mkdir(parents=True, exist_ok=True)
    (d / "client_check.md").write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print("saved:", d.relative_to(REPO_ROOT).as_posix())
    return 1 if total_bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", action="append", default=[], help="MCP 服务地址，可重复")
    args = ap.parse_args()
    return asyncio.run(main_async(args.url or ["http://127.0.0.1:8101/mcp"]))


if __name__ == "__main__":
    sys.exit(main())
