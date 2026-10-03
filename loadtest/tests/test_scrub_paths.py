"""结果文件里的本机绝对路径清理（离线单测）。

测试输入里的「盘符 + 路径」在运行时拼出来：仓库的安全扫描会拦字面量的本机绝对路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrub_paths as sp  # noqa: E402

DRIVE = "Q" + ":"  # 虚构的盘符
FWD = DRIVE + "/some/where/repo"
BS = FWD.replace("/", "\\")
BS2 = FWD.replace("/", "\\\\")  # JSON 里反斜杠要转义成两个
ROOT = Path(FWD)  # 只用来构造输入，不触碰文件系统


def test_scrub_handles_json_escaped_windows_paths():
    s = f'["{BS2}\\\\loadtest\\\\.venv\\\\python.exe", "--csv", "{BS2}\\\\reports\\\\perf\\\\x\\\\locust"]'
    out = sp.scrub_text(s, ROOT)
    assert "some" not in out
    assert '"<repo>/loadtest/.venv/python.exe"' in out and '"<repo>/reports/perf/x/locust"' in out


def test_scrub_handles_forward_slashes_and_plain_backslashes():
    assert sp.scrub_text(f"cd {FWD}/loadtest && x", ROOT) == "cd <repo>/loadtest && x"
    assert sp.scrub_text(f"{BS}\\reports\\a.json", ROOT) == "<repo>/reports/a.json"


def test_scrub_is_idempotent_and_leaves_other_text_alone():
    s = "没有路径的文本 <repo>/a/b 与 /mnt/x"
    assert sp.scrub_text(s, ROOT) == s


def test_scrub_replaces_interpreter_path():
    interp = "E" + ":\\python\\python.exe"
    assert sp.scrub_text(interp + " -m locust", ROOT) == "python -m locust"
    assert sp.scrub_text(interp.replace("\\", "\\\\"), ROOT) == "python"  # JSON 转义的写法
