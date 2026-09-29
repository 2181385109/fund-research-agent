import e2e_helpers as h

SSE = (
    'event: meta\ndata: {"request_id":"r"}\n\n'
    ": ping\n\n"
    'event: token\ndata: {"text":"阈值是"}\n\n'
    'event: token\ndata: {"text":"17.3%[1]"}\n\n'
    'event: citations\ndata: {"items":[{"id":1,"kind":"document","doc_title":"备忘录.pdf","kb_id":"11"}]}\n\n'
    'event: disclaimer\ndata: {"text":"风险提示"}\n\n'
    'event: done\ndata: {"status":"ok"}\n\n'
)


def test_summary_of_a_well_formed_stream():
    s = h.summarize(h.parse_sse(SSE))
    assert s["events"] == ["meta", "token", "token", "citations", "disclaimer", "done"]
    assert s["answer"] == "阈值是17.3%[1]"
    assert s["citations"][0]["kb_id"] == "11"
    assert s["disclaimer_right_before_done"] and s["done_status"] == "ok"


def test_disclaimer_must_be_right_before_done():
    bad = SSE.replace("event: disclaimer", "event: token").replace(
        '{"text":"风险提示"}', '{"text":"x"}'
    )
    s = h.summarize(h.parse_sse(bad))
    assert not s["disclaimer_right_before_done"]


def test_json_get_walks_lists_and_dicts():
    assert h._get({"data": [{"id": 5}]}, "data.0.id") == 5
