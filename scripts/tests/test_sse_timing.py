"""sse_timing 的时间线切分（离线，不发请求）。"""

import sse_timing as st


def _chunks(*parts):
    return [(t, s.encode("utf-8")) for t, s in parts]


def test_events_are_timestamped_when_complete():
    chunks = _chunks(
        (10, 'event:meta\ndata:{"a":1}\n\n'),
        (5010, ":ping\n\n"),
        (5020, 'event:token\ndata:{"text":"x"}\n\nevent:tok'),
        (5030, 'en\ndata:{"text":"y"}\n\n'),
    )
    ev = st.split_events(chunks)
    assert [(e["event"], e["t_ms"]) for e in ev] == [
        ("meta", 10),
        ("ping", 5010),
        ("token", 5020),
        ("token", 5030),
    ]


def test_multibyte_char_split_across_chunks():
    raw = 'event:token\ndata:{"text":"基金"}\n\n'.encode()
    cut = raw.index("基".encode()) + 1
    ev = st.split_events([(1, raw[:cut]), (2, raw[cut:])])
    assert [(e["event"], e["t_ms"]) for e in ev] == [("token", 2)]


def test_streaming_vs_buffered_summary():
    streamed = _chunks(
        (20, "event:meta\ndata:{}\n\n"),
        (3000, 'event:token\ndata:{"text":"a"}\n\n'),
        (5000, ":ping\n\n"),
        (6000, 'event:token\ndata:{"text":"b"}\n\n'),
        (6100, "event:done\ndata:{}\n\n"),
    )
    s = st.summarize(streamed, total_ms=6100)
    assert s["first_byte_ms"] == 20 and s["first_token_ms"] == 3000
    assert s["token_spread_ms"] == 3000 and s["ping_ms"] == [5000]
    assert s["first_byte_over_total"] < 0.01

    buffered = _chunks((6099, "".join(f'event:token\ndata:{{"text":"{i}"}}\n\n' for i in range(5))))
    b = st.summarize(buffered, total_ms=6100)
    assert b["first_byte_over_total"] > 0.99 and b["token_spread_ms"] == 0
