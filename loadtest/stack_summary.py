"""把 py-spy 的折叠栈（``-f raw``）和 JFR 的 ``ExecutionSample`` 汇总成 Markdown 表，写进 baseline 报告。

- ``pyspy <raw.txt>``：每行 ``thread;frame;frame;… <samples>``。输出：线程组样本占比、自身时间（栈顶函数）Top N、
  包含时间（出现在栈里的函数）Top N。py-spy 只看得到 Python 帧；torch 的 C++ 算子在栈顶表现为调用它的 Python 函数
  （例如 ``forward`` / ``linear``），所以「自身时间」高的 Python 帧 = 正在原生代码里计算。
- ``jfr <jfr print 输出>``：统计 ``jdk.ExecutionSample`` 的栈顶方法 Top N 与线程名前缀。

用法::

    python loadtest/stack_summary.py pyspy reports/perf/profiles/<目录>/ai_raw.txt --top 15
    jfr print --events jdk.ExecutionSample backend.jfr > backend_samples.txt
    python loadtest/stack_summary.py jfr backend_samples.txt --top 15
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

THREAD_PREFIX = re.compile(r"[-_ ]?\d+$|\s*\(.*\)$")


def thread_group(name: str) -> str:
    """``ThreadPoolExecutor-1_0 (123)`` → ``ThreadPoolExecutor``：去掉序号和 pid，把同一类线程归在一起。"""
    name = re.sub(
        r"^thread \(\d+\):\s*", "", name.strip()
    )  # py-spy --threads 的格式：thread (174): AnyIO worker thread
    name = re.sub(r"\s*\(\d+\)\s*$", "", name)
    name = re.sub(r"[-_]\d+(_\d+)?$", "", name)
    return name or "(unnamed)"


def parse_pyspy_raw(text: str) -> tuple[Counter, Counter, Counter, int]:
    """返回 (线程组样本数, 自身时间计数, 包含时间计数, 总样本数)。每个函数在一条栈里只算一次包含时间。"""
    threads: Counter = Counter()
    self_t: Counter = Counter()
    incl: Counter = Counter()
    total = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        stack, _, n = line.rpartition(" ")
        try:
            count = int(n)
        except ValueError:
            continue
        frames = stack.split(";")
        total += count
        threads[thread_group(frames[0])] += count
        body = frames[1:] or frames
        self_t[body[-1]] += count
        for f in set(body):
            incl[f] += count
    return threads, self_t, incl, total


def parse_jfr_print(
    text: str, t_from: str | None = None, t_to: str | None = None
) -> tuple[Counter, Counter, int]:
    """``jfr print --events jdk.ExecutionSample`` 的文本 → (栈顶方法计数, 线程组计数, 样本数)。

    ``t_from`` / ``t_to``（``HH:MM:SS``，jfr 工具按本机时区打印的 startTime，同一天内比较）只统计这个窗口内的样本：
    JFR 从 JVM 启动就开始录，启动、注册用户等阶段要排除，只留压测窗口。
    """
    top: Counter = Counter()
    threads: Counter = Counter()
    total = 0
    for block in re.split(r"\n(?=jdk\.ExecutionSample)", text):
        ts = re.search(r"startTime = ([0-9]{2}:[0-9]{2}:[0-9]{2})", block)
        if ts and ((t_from and ts.group(1) < t_from) or (t_to and ts.group(1) >= t_to)):
            continue
        m = re.search(r"sampledThread = \"([^\"]+)\"", block)
        st = re.search(r"stackTrace = \[\s*\n\s*([^\n]+)", block)
        if not (m and st):
            continue
        total += 1
        threads[thread_group(m.group(1))] += 1
        top[re.sub(r"\s+line:.*$", "", st.group(1).strip())] += 1
    return top, threads, total


def table(title: str, c: Counter, total: int, top: int) -> str:
    lines = [f"**{title}**（总样本 {total}）", "", "| 占比 | 样本 | 名称 |", "|---|---|---|"]
    for name, n in c.most_common(top):
        lines.append(f"| {n / total:.1%} | {n} | `{name[:140]}` |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("kind", choices=["pyspy", "jfr"])
    ap.add_argument("path", type=Path)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--from", dest="t_from", help="jfr：窗口起点 HH:MM:SS（本机时区）")
    ap.add_argument("--to", dest="t_to", help="jfr：窗口终点 HH:MM:SS")
    a = ap.parse_args(argv)
    text = a.path.read_text(encoding="utf-8", errors="replace")
    if a.kind == "pyspy":
        threads, self_t, incl, total = parse_pyspy_raw(text)
        print(table("线程组", threads, total, a.top), "", sep="\n")
        print(table("自身时间 Top（栈顶函数）", self_t, total, a.top), "", sep="\n")
        print(table("包含时间 Top（栈中出现的函数）", incl, total, a.top))
    else:
        top, threads, total = parse_jfr_print(text, a.t_from, a.t_to)
        print(table("线程组", threads, total, a.top), "", sep="\n")
        print(table("栈顶方法 Top", top, total, a.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
