"""py-spy 折叠栈与 JFR 文本的汇总函数（离线单测，样本是手写的小例子）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stack_summary as ss  # noqa: E402

RAW = """\
ThreadPoolExecutor-0_1 (123);run (x.py:1);rerank (r.py:10);forward (t.py:5) 80
ThreadPoolExecutor-0_2 (124);run (x.py:1);rerank (r.py:10);forward (t.py:5) 10
MainThread (1);serve (m.py:1);select (s.py:9) 10
"""

JFR = """\
jdk.ExecutionSample {
  startTime = 12:00:00.000
  sampledThread = "http-nio-8081-exec-3" (javaThreadId = 40)
  stackTrace = [
    com.fundagent.Foo.bar(int) line: 10
    com.fundagent.Foo.baz() line: 4
  ]
}

jdk.ExecutionSample {
  startTime = 12:00:00.010
  sampledThread = "http-nio-8081-exec-4" (javaThreadId = 41)
  stackTrace = [
    com.fundagent.Foo.bar(int) line: 10
  ]
}

jdk.ExecutionSample {
  startTime = 12:00:00.020
  sampledThread = "chat-stream-2" (javaThreadId = 42)
  stackTrace = [
    java.net.SocketInputStream.read() line: 1
  ]
}
"""


def test_thread_group_strips_indexes_and_pids():
    assert ss.thread_group("ThreadPoolExecutor-0_1 (123)") == "ThreadPoolExecutor"
    assert ss.thread_group("http-nio-8081-exec-3") == "http-nio-8081-exec"
    assert ss.thread_group("MainThread (1)") == "MainThread"
    assert ss.thread_group("thread (174): AnyIO worker thread") == "AnyIO worker thread"
    assert ss.thread_group("thread (1): MainThread") == "MainThread"


def test_pyspy_raw_self_and_inclusive_time():
    threads, self_t, incl, total = ss.parse_pyspy_raw(RAW)
    assert total == 100 and threads["ThreadPoolExecutor"] == 90 and threads["MainThread"] == 10
    assert self_t["forward (t.py:5)"] == 90 and self_t["select (s.py:9)"] == 10
    assert incl["rerank (r.py:10)"] == 90 and incl["run (x.py:1)"] == 90


def test_jfr_counts_top_frames_and_thread_groups():
    top, threads, total = ss.parse_jfr_print(JFR)
    assert total == 3
    assert top["com.fundagent.Foo.bar(int)"] == 2
    assert threads["http-nio-8081-exec"] == 2 and threads["chat-stream"] == 1


def test_jfr_window_filter_excludes_samples_outside_the_load_window():
    top, _threads, total = ss.parse_jfr_print(JFR, "12:00:00", "12:00:01")
    assert total == 3
    assert ss.parse_jfr_print(JFR, "12:00:05", "12:00:09")[2] == 0
    assert ss.parse_jfr_print(JFR, None, "12:00:00")[2] == 0  # 终点不含
