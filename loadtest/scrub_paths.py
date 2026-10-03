"""把结果文件里的本机绝对路径替换成 ``<repo>``（CLAUDE.md §5：入库文件不许带本机绝对路径）。

``run_perf.py`` 写 run.json / summary.json 时已经自动做这一步；本脚本处理更早生成的文件，可重复运行（幂等）。
用法：``python loadtest/scrub_paths.py reports/perf``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".json", ".md", ".txt", ".csv"}


def scrub_text(s: str, root: Path = ROOT) -> str:
    """仓库根目录的各种写法（正斜杠、反斜杠、JSON 转义的双反斜杠）→ ``<repo>``，其后的反斜杠分隔符统一成正斜杠。"""
    fwd = str(root).replace("\\", "/")  # 不依赖运行的操作系统：三种写法都从正斜杠形式推出来
    bs = fwd.replace("/", "\\")
    bs2 = fwd.replace("/", "\\\\")
    for variant in (bs2, bs, fwd):
        i = s.find(variant)
        while i != -1:
            j = i + len(variant)
            # 紧随其后的路径片段里，反斜杠（含 JSON 的双反斜杠）换成正斜杠，直到遇到引号 / 空白
            k = j
            while k < len(s) and s[k] not in " \t\r\n\"'":
                k += 1
            tail = s[j:k].replace("\\\\", "/").replace("\\", "/")
            s = s[:i] + "<repo>" + tail + s[k:]
            i = s.find(variant, i + len("<repo>") + len(tail))
    # 解释器路径：只保留命令名
    s = re.sub(r"[A-Za-z]:[\\/]+python[\\/]+python\.exe", "python", s)
    return s


def scrub_tree(top: Path) -> int:
    n = 0
    for p in top.rglob("*"):
        if p.is_file() and p.suffix in SUFFIXES:
            with open(p, encoding="utf-8", newline="") as f:
                s = f.read()
            t = scrub_text(s)
            if t != s:
                with open(p, "w", encoding="utf-8", newline="") as f:
                    f.write(t)
                n += 1
    return n


if __name__ == "__main__":
    print(f"改写了 {scrub_tree(Path(sys.argv[1]))} 个文件")
