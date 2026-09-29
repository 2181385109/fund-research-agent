"""S5 验收 3：Agent 单测（Fake LLM + Fake 工具后端）。

覆盖：循环上限、SQL 报错后恢复与重试上限、工具不可用、引用映射（四种出处）、非法编号丢弃、
风险提示必定出现（含各类报错路径）、事件顺序与 done 里的用量 / 模型名。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from fund_ai.agent.compliance import DISCLAIMER
from fund_ai.agent.fake import FakeChatModel, FakeToolBackend, FakeTurn
from fund_ai.agent.runner import AgentRunner
from fund_ai.agent.tools import ToolOutcome

SQL_OK = {
    "columns": ["share_code", "management_fee"],
    "rows": [["003095", "0.012"]],
    "row_count": 1,
    "truncated": False,
    "max_rows": 200,
    "executed_sql": "SELECT share_code, management_fee FROM fees WHERE share_code='003095' LIMIT 201",
    "tables": ["fees"],
    "source": "fund_data（MySQL 快照库）表 fees",
    "as_of": "2026-09-28",
}
DOC_OK = {
    "query": "q",
    "count": 2,
    "results": [
        {
            "ref": i,
            "fund_code": "003095",
            "fund_name": "中欧医疗健康混合",
            "doc_type": "annual_report",
            "doc_title": "2025年年度报告",
            "report_period": "2025",
            "page_start": 10 + i,
            "page_end": 10 + i,
            "section": "管理费",
            "doc_id": "003095_annual_report_2025",
            "chunk_id": f"c{i}",
            "text": f"管理费按 1.2% 年费率计提（片段{i}）",
        }
        for i in (1, 2)
    ],
}
CALC_OK = {
    "share_code": "003095",
    "start_used": "2025-12-31",
    "end_used": "2026-06-30",
    "return": 0.0386,
    "display": {"return": "3.86%"},
    "source": "fund_data.nav_daily",
    "as_of": "2026-09-28",
}
NAV_OK = {
    "share_code": "003095",
    "unit_nav": 2.1,
    "nav_date": "2026-09-28",
    "fetched_at": "2026-09-29T08:00:00Z",
    "source": "东方财富",
    "stale": False,
}


def _backend(**over: Any) -> FakeToolBackend:
    handlers: dict[str, Any] = {
        "run_fund_sql": lambda a: SQL_OK,
        "search_fund_documents": lambda a: DOC_OK,
        "calc_fund_return": lambda a: CALC_OK,
        "get_latest_nav": lambda a: NAV_OK,
        "get_fund_db_schema": lambda a: {"tables": {"fees": "费率"}},
    }
    handlers.update(over)
    return FakeToolBackend(handlers)


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


async def _run(
    turns: list[Any],
    backend: FakeToolBackend | None = None,
    *,
    question: str = "中欧医疗健康的管理费是多少？",
    history: list[dict[str, str]] | None = None,
    max_steps: int = 6,
    repeat_last: bool = False,
    holdback_chars: int = 0,
) -> tuple[list[dict[str, Any]], FakeChatModel, FakeToolBackend]:
    llm = FakeChatModel(turns=turns, repeat_last=repeat_last)
    be = backend or _backend()
    runner = AgentRunner(
        llm, be, "SYSTEM", "fake-request-model", max_steps=max_steps, holdback_chars=holdback_chars
    )
    events = [ev async for ev in runner.run(question, history, request_id="rid1")]
    return events, llm, be


def _names(events: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in events]


def _answer(events: list[dict[str, Any]]) -> str:
    return "".join(e["data"]["text"] for e in events if e["event"] == "token")


def _one(events: list[dict[str, Any]], name: str) -> dict[str, Any]:
    (ev,) = [e for e in events if e["event"] == name]
    return ev["data"]


def _assert_tail_is_disclaimer_then_done(events: list[dict[str, Any]]) -> None:
    names = _names(events)
    assert names[-2:] == ["disclaimer", "done"], names
    assert events[-2]["data"]["text"] == DISCLAIMER
    assert names.count("disclaimer") == 1 and names.count("done") == 1


# ---------------------------------------------------------------- 正常路径与事件顺序


async def test_document_flow_events_answer_and_citations():
    events, llm, be = await _run(
        [
            FakeTurn(
                tool_calls=[_call("search_fund_documents", query="管理费", fund_codes=["003095"])]
            ),
            FakeTurn(text="管理费率为 1.2%[1]，按前一日净值计提[2]。"),
        ]
    )
    assert _names(events) == [
        "meta",
        "tool_start",
        "tool_end",
        *(["token"] * _names(events).count("token")),
        "citations",
        "disclaimer",
        "done",
    ]
    assert _answer(events) == "管理费率为 1.2%[1]，按前一日净值计提[2]。"
    assert be.calls == [("search_fund_documents", {"query": "管理费", "fund_codes": ["003095"]})]

    assert _one(events, "meta")["model"] == "fake-request-model"
    end = _one(events, "tool_end")
    assert end["status"] == "ok" and end["citation_ids"] == [1, 2] and "2 个片段" in end["summary"]

    items = _one(events, "citations")["items"]
    assert [i["id"] for i in items] == [1, 2]
    assert all(i["kind"] == "document" and i["fund_code"] == "003095" for i in items)
    assert items[0]["page_start"] == 11 and "片段1" in items[0]["snippet"]

    _assert_tail_is_disclaimer_then_done(events)
    done = events[-1]["data"]
    assert done["status"] == "ok" and done["tool_rounds"] == 1
    assert done["usage"] == {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240}
    assert done["request_model"] == "fake-request-model"
    assert done["response_models"] == ["fake-model"]
    assert len(done["llm_calls"]) == 2 and done["timings_ms"]["first_token"] is not None
    assert done["dropped_citations"] == [] and done["compliance_flags"] == []
    # 第二轮 LLM 收到了带 [n] 的工具结果
    tool_msgs = [m for m in llm.calls[1]["messages"] if isinstance(m, ToolMessage)]
    assert "[1] 中欧医疗健康混合（003095）｜2025年年度报告｜第11页｜管理费" in tool_msgs[0].content


async def test_mixed_answer_cites_all_four_kinds():
    events, _, _ = await _run(
        [
            FakeTurn(
                tool_calls=[
                    _call("run_fund_sql", sql="SELECT ..."),
                    _call("search_fund_documents", query="q"),
                ]
            ),
            FakeTurn(
                tool_calls=[
                    _call(
                        "calc_fund_return",
                        share_code="003095",
                        start="2025-12-31",
                        end="2026-06-30",
                    ),
                    _call("get_latest_nav", share_code="003095"),
                ]
            ),
            # 编号：SQL=1，文档=2、3，计算=4，净值=5；故意不引用 3
            FakeTurn(text="费率[1][2]，收益 3.86%[4]，最新净值[5]。"),
        ]
    )
    items = _one(events, "citations")["items"]
    assert [(i["id"], i["kind"]) for i in items] == [
        (1, "database"),
        (2, "document"),
        (4, "computation"),
        (5, "api"),
    ]
    assert items[0]["tables"] == ["fees"] and items[0]["as_of"] == "2026-09-28"
    assert items[2]["args"] == {"share_code": "003095", "start": "2025-12-31", "end": "2026-06-30"}
    assert items[2]["start_used"] == "2025-12-31" and items[2]["end_used"] == "2026-06-30"
    assert items[3]["nav_date"] == "2026-09-28" and items[3]["fetched_at"].startswith("2026-09-29")
    _assert_tail_is_disclaimer_then_done(events)


async def test_stale_nav_marker_reaches_the_llm_and_the_citation():
    stale = {**NAV_OK, "stale": True, "nav_date": "2026-09-26", "source": "快照"}
    events, llm, _ = await _run(
        [
            FakeTurn(tool_calls=[_call("get_latest_nav", share_code="003095")]),
            FakeTurn(text="快照净值 2.1（非最新）[1]。"),
        ],
        _backend(get_latest_nav=lambda a: stale),
    )
    msg = [m for m in llm.calls[1]["messages"] if isinstance(m, ToolMessage)][0]
    assert "非最新" in msg.content
    assert _one(events, "citations")["items"][0]["stale"] is True
    assert "非最新" in _one(events, "tool_end")["summary"]


async def test_no_tools_answer_still_gets_disclaimer_and_empty_citations():
    events, _, be = await _run([FakeTurn(text="基金池中没有收录该基金。")])
    assert be.calls == []
    assert _one(events, "citations")["items"] == []
    _assert_tail_is_disclaimer_then_done(events)


async def test_history_is_passed_with_stale_refs_removed():
    events, llm, _ = await _run(
        [FakeTurn(text="好的")],
        history=[
            {"role": "user", "content": "管理费"},
            {"role": "assistant", "content": "1.2%[1]"},
        ],
        question="托管费呢",
    )
    contents = [m.content for m in llm.calls[0]["messages"]]
    assert contents == ["SYSTEM", "管理费", "1.2%", "托管费呢"]
    assert _names(events)[-1] == "done"


# ---------------------------------------------------------------- 非法编号


async def test_invalid_reference_numbers_are_dropped_and_logged(caplog):
    with caplog.at_level("WARNING", logger="fund_ai.agent.citations"):
        events, _, _ = await _run(
            [
                FakeTurn(tool_calls=[_call("run_fund_sql", sql="SELECT ...")]),
                FakeTurn(text="费率 1.2%[1]，另一个数据[7]，还有[3]。", chunk_size=2),
            ]
        )
    assert _answer(events) == "费率 1.2%[1]，另一个数据，还有。"  # 用户看不到 [7] 和 [3]
    assert [i["id"] for i in _one(events, "citations")["items"]] == [1]
    assert events[-1]["data"]["dropped_citations"] == [7, 3]
    assert "dropped invalid citation [7]" in caplog.text


async def test_references_before_any_tool_result_are_all_invalid():
    events, _, _ = await _run([FakeTurn(text="管理费 1.2%[1]。")])
    assert _answer(events) == "管理费 1.2%。"
    assert events[-1]["data"]["dropped_citations"] == [1]


# ---------------------------------------------------------------- 循环上限


async def test_loop_cap_forces_a_final_answer_without_tools():
    events, llm, be = await _run(
        [FakeTurn(text="继续查。", tool_calls=[_call("run_fund_sql", sql="SELECT 1")])],
        max_steps=3,
        repeat_last=True,
    )
    assert len(be.calls) == 3  # 恰好 max_steps 轮工具
    bound = [c["tools_bound"] for c in llm.calls]
    assert bound == [True, True, True, False]  # 第 4 次（强制作答）不带工具
    assert "工具调用次数已达上限" in llm.calls[3]["messages"][-1].content
    done = events[-1]["data"]
    assert done["status"] == "ok" and done["tool_rounds"] == 3 and done["max_steps_reached"] is True
    assert _names(events).count("tool_start") == 3
    _assert_tail_is_disclaimer_then_done(events)


async def test_default_max_steps_is_six():
    events, _, be = await _run(
        [FakeTurn(text="x", tool_calls=[_call("get_latest_nav", share_code="003095")])],
        repeat_last=True,
    )
    assert len(be.calls) == 6 and events[-1]["data"]["tool_rounds"] == 6


# ---------------------------------------------------------------- 工具报错后恢复


async def test_sql_error_is_returned_to_the_llm_and_the_retry_succeeds():
    attempts = []

    def sql(args: dict[str, Any]) -> Any:
        attempts.append(args["sql"])
        if len(attempts) == 1:
            return ToolOutcome(
                False, "Error executing tool run_fund_sql: 未知列 fee", kind="tool_error"
            )
        return SQL_OK

    events, llm, _ = await _run(
        [
            FakeTurn(tool_calls=[_call("run_fund_sql", sql="SELECT fee FROM fees")]),
            FakeTurn(tool_calls=[_call("run_fund_sql", sql="SELECT management_fee FROM fees")]),
            FakeTurn(text="管理费率 1.2%[1]。"),
        ],
        _backend(run_fund_sql=sql),
    )
    ends = [e["data"] for e in events if e["event"] == "tool_end"]
    assert [e["status"] for e in ends] == ["error", "ok"]
    assert "未知列 fee" in ends[0]["error"]
    second_call_msgs = [m for m in llm.calls[1]["messages"] if isinstance(m, ToolMessage)]
    assert second_call_msgs[0].status == "error"
    assert (
        "工具调用失败" in second_call_msgs[0].content
        and "未知列 fee" in second_call_msgs[0].content
    )
    assert "还可以修改后重试 2 次" in second_call_msgs[0].content
    assert _one(events, "citations")["items"][0]["kind"] == "database"  # 失败的那次没有编号
    assert events[-1]["data"]["status"] == "ok"


async def test_sql_retries_are_capped_at_two():
    calls = []

    def sql(args: dict[str, Any]) -> Any:
        calls.append(args)
        return ToolOutcome(False, "Error executing tool run_fund_sql: 语法错误", kind="tool_error")

    events, llm, be = await _run(
        [
            *[FakeTurn(tool_calls=[_call("run_fund_sql", sql=f"BAD {i}")]) for i in range(4)],
            FakeTurn(text="数据库中查不到该数据。"),
        ],
        _backend(run_fund_sql=sql),
    )
    assert len(calls) == 3  # 首次 + 2 次重试；第 4 次没有真正执行
    ends = [e["data"] for e in events if e["event"] == "tool_end"]
    assert len(ends) == 4 and all(e["status"] == "error" for e in ends)
    assert "重试上限" in ends[3]["error"]
    msgs = [m.content for m in llm.calls[3]["messages"] if isinstance(m, ToolMessage)]
    assert "请不要再调用 run_fund_sql" in msgs[2]
    assert events[-1]["data"]["status"] == "ok"
    _assert_tail_is_disclaimer_then_done(events)


async def test_sql_failure_counter_resets_after_a_success():
    state = {"n": 0}

    def sql(args: dict[str, Any]) -> Any:
        state["n"] += 1
        if state["n"] in (1, 2, 4, 5):
            return ToolOutcome(False, "Error: x", kind="tool_error")
        return SQL_OK

    events, _, be = await _run(
        [FakeTurn(tool_calls=[_call("run_fund_sql", sql=f"S{i}")]) for i in range(6)]
        + [FakeTurn(text="完成[1]。")],
        _backend(run_fund_sql=sql),
        max_steps=8,
    )
    assert len(be.calls) == 6  # 2 次失败 → 成功 → 2 次失败 → 成功：计数清零，没有被拦下
    assert events[-1]["data"]["status"] == "ok"


async def test_unavailable_tool_does_not_break_the_request():
    events, llm, _ = await _run(
        [
            FakeTurn(tool_calls=[_call("get_latest_nav", share_code="003095")]),
            FakeTurn(text="暂时无法获取最新净值。"),
        ],
        _backend(
            get_latest_nav=lambda a: ToolOutcome(
                False, "工具服务暂时不可用（ConnectError）", kind="unavailable"
            )
        ),
    )
    end = _one(events, "tool_end")
    assert end["status"] == "error" and end["error_kind"] == "unavailable"
    assert events[-1]["data"]["status"] == "ok"
    _assert_tail_is_disclaimer_then_done(events)


async def test_invalid_tool_call_json_is_reported_back():
    events, llm, be = await _run(
        [
            FakeTurn(tool_calls=[{"name": "run_fund_sql", "args": "{不是json", "id": "bad1"}]),
            FakeTurn(text="抱歉，没能查询。"),
        ]
    )
    assert be.calls == []
    end = _one(events, "tool_end")
    assert end["status"] == "error" and "不是合法 JSON" in end["error"]
    msgs = [m for m in llm.calls[1]["messages"] if isinstance(m, ToolMessage)]
    assert msgs[0].tool_call_id == "bad1" and msgs[0].status == "error"
    _assert_tail_is_disclaimer_then_done(events)


# ---------------------------------------------------------------- 风险提示必定出现（含报错路径）


async def test_disclaimer_present_when_llm_fails_on_first_call():
    events, _, _ = await _run([FakeTurn(error=RuntimeError("upstream 500"))])
    assert _names(events)[:2] == ["meta", "error"]
    assert "citations" not in _names(events)
    _assert_tail_is_disclaimer_then_done(events)
    assert events[-1]["data"]["status"] == "error"
    assert "upstream 500" in _one(events, "error")["message"]


async def test_disclaimer_present_when_llm_fails_mid_run_and_partial_tokens_were_sent():
    events, _, _ = await _run(
        [
            FakeTurn(text="先说一半", tool_calls=[_call("run_fund_sql", sql="S")]),
            FakeTurn(error=TimeoutError("llm timeout")),
        ]
    )
    assert "token" in _names(events) and "error" in _names(events)
    _assert_tail_is_disclaimer_then_done(events)
    done = events[-1]["data"]
    assert done["status"] == "error" and done["llm_calls"] and len(done["llm_calls"]) == 1


async def test_disclaimer_present_when_tool_listing_fails():
    class BrokenBackend(FakeToolBackend):
        async def specs(self) -> list[dict[str, Any]]:
            raise ConnectionError("mcp down")

    events, _, _ = await _run([FakeTurn(text="x")], BrokenBackend({}))
    assert _names(events) == ["meta", "error", "disclaimer", "done"]
    assert events[-1]["data"]["status"] == "error"


async def test_empty_model_answer_is_an_error_but_keeps_the_disclaimer():
    events, _, _ = await _run([FakeTurn(text="")])
    assert "error" in _names(events)
    _assert_tail_is_disclaimer_then_done(events)


async def test_disclaimer_is_not_part_of_the_token_stream_or_model_output():
    events, _, _ = await _run([FakeTurn(text="管理费 1.2%。")])
    assert DISCLAIMER not in _answer(events)  # 由服务端单独用 disclaimer 事件追加


# ---------------------------------------------------------------- 输出守卫


async def test_output_guard_flags_but_does_not_rewrite():
    events, _, _ = await _run([FakeTurn(text="这只基金稳赚，建议买入。")])
    assert _answer(events) == "这只基金稳赚，建议买入。"  # 第一期只标记，不改写
    assert events[-1]["data"]["compliance_flags"] == ["稳赚", "建议买入"]


async def test_refusal_wording_is_not_flagged():
    events, _, _ = await _run(
        [FakeTurn(text="我无法推荐购买或建议买入任何基金，只能提供客观数据。")]
    )
    assert events[-1]["data"]["compliance_flags"] == []


# ---------------------------------------------------------------- 取消


async def test_cancellation_propagates_and_stops_the_run():
    gate = asyncio.Event()

    class Slow(FakeToolBackend):
        async def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
            gate.set()
            await asyncio.sleep(30)
            return ToolOutcome(True, "{}", {})

    llm = FakeChatModel(turns=[FakeTurn(tool_calls=[_call("run_fund_sql", sql="S")])])
    runner = AgentRunner(llm, Slow({"run_fund_sql": lambda a: {}}), "S", "m")

    async def consume() -> None:
        async for _ in runner.run("q"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(gate.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_truncated_tool_arguments_are_not_executed():
    # langchain 的宽松解析会把被截断的 JSON 补全成合法调用；这里必须当作无效调用，不能去执行半条 SQL
    events, _, be = await _run(
        [
            FakeTurn(
                tool_calls=[
                    {"name": "run_fund_sql", "args": '{"sql": "SELECT a FROM fe', "id": "t1"}
                ]
            ),
            FakeTurn(text="抱歉。"),
        ]
    )
    assert be.calls == []
    assert _one(events, "tool_end")["status"] == "error"


# ---------------------------------------------------------------- 工具轮开场白


async def test_preamble_before_tool_calls_is_dropped_but_the_final_answer_streams():
    events, _, _ = await _run(
        [
            FakeTurn(
                text="I'll look up the fee rates first.",
                tool_calls=[_call("run_fund_sql", sql="SELECT 1")],
            ),
            FakeTurn(text="管理费 1.2%[1]。"),
        ],
        holdback_chars=100,
    )
    assert _answer(events) == "管理费 1.2%[1]。"  # 开场白不在答案里
    done = events[-1]["data"]
    assert done["preamble_dropped_chars"] == len("I'll look up the fee rates first.")
    assert done["preamble_leaked_chars"] == 0
    assert done["answer_chars"] == len("管理费 1.2%[1]。")


async def test_final_answer_longer_than_the_holdback_streams_in_pieces():
    text = "基金的管理费按前一日基金资产净值计提，年费率 1.2%[1]。" * 4  # 远超 100 字
    events, _, _ = await _run(
        [FakeTurn(tool_calls=[_call("run_fund_sql", sql="S")]), FakeTurn(text=text, chunk_size=5)],
        holdback_chars=40,
    )
    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert "".join(tokens) == text
    assert len(tokens) > 3  # 超过扣留长度后是逐片流出，不是整段一次性发
    assert events[-1]["data"]["preamble_dropped_chars"] == 0


async def test_preamble_longer_than_the_holdback_leaks_and_is_reported():
    long_pre = "让我先查一下基金的费率信息，" * 6
    events, _, _ = await _run(
        [
            FakeTurn(text=long_pre, tool_calls=[_call("run_fund_sql", sql="S")], chunk_size=4),
            FakeTurn(text="管理费 1.2%[1]。"),
        ],
        holdback_chars=20,
    )
    done = events[-1]["data"]
    assert done["preamble_leaked_chars"] > 0  # 漏出去的部分如实上报，不假装没有
    assert _answer(events).endswith("管理费 1.2%[1]。")


async def test_holdback_disabled_streams_everything():
    events, _, _ = await _run(
        [
            FakeTurn(text="先说一句。", tool_calls=[_call("run_fund_sql", sql="S")]),
            FakeTurn(text="答案[1]。"),
        ],
        holdback_chars=0,
    )
    assert _answer(events) == "先说一句。答案[1]。"
    assert events[-1]["data"]["preamble_leaked_chars"] == 5
