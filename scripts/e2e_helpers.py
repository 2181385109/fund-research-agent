"""e2e_smoke.sh 的辅助工具（Python 部分）。

- ``make-pdf OUT``：生成一份**自造**的私有文档 PDF（虚构内容；需要 reportlab，本机用 ai-service 的 venv）；
- ``sse-summary FILE``：解析 curl 保存的 SSE 原文，打印事件顺序 / 答案 / 出处摘要，并检查协议不变量；
- ``json-get FILE PATH``：从 JSON 文件取值（点号路径，数字下标用数字）。

不打印任何密钥。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PRIVATE_FACTS = [
    "星河研究院 内部备忘录（虚构材料，仅用于系统演示）",
    "青鸾项目的止盈阈值定为17.3%，复核周期为每周三，负责人是研究员林深。",
    "赤霄项目的仓位上限为组合净值的8.6%，超过上限需要在两个交易日内完成再平衡。",
    "本备忘录的保密等级为内部，不得对外披露。",
]


def make_pdf(out: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setFont("STSong-Light", 12)
    y = A4[1] - 80
    for line in PRIVATE_FACTS:
        c.drawString(72, y, line)
        y -= 28
    c.showPage()
    c.save()


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        name = next((ln[6:].strip() for ln in lines if ln.startswith("event:")), "message")
        data = "\n".join(ln[5:].lstrip() for ln in lines if ln.startswith("data:"))
        events.append((name, json.loads(data) if data else {}))
    return events


def summarize(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    names = [n for n, _ in events]
    answer = "".join(d.get("text", "") for n, d in events if n == "token")
    cits = next((d.get("items", []) for n, d in events if n == "citations"), [])
    disclaimer = next((d.get("text", "") for n, d in events if n == "disclaimer"), "")
    done = next((d for n, d in events if n == "done"), {})
    return {
        "events": names,
        "answer": answer,
        "citations": cits,
        "disclaimer": disclaimer,
        "done_status": done.get("status"),
        "disclaimer_right_before_done": len(names) >= 2 and names[-2:] == ["disclaimer", "done"],
    }


def _get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make-pdf")
    p.add_argument("out", type=Path)
    p = sub.add_parser("sse-summary")
    p.add_argument("file", type=Path)
    p.add_argument("--expect-private-doc", metavar="FILENAME")
    p = sub.add_parser("json-get")
    p.add_argument("file", type=Path)
    p.add_argument("path")
    args = ap.parse_args(argv)

    if args.cmd == "make-pdf":
        make_pdf(args.out)
        print(f"wrote {args.out}")
        return 0
    if args.cmd == "json-get":
        print(_get(json.loads(args.file.read_text(encoding="utf-8")), args.path))
        return 0

    s = summarize(parse_sse(args.file.read_text(encoding="utf-8")))
    print("事件顺序：", " → ".join(s["events"]))
    print("答案：", s["answer"])
    print("出处：")
    for c in s["citations"]:
        label = c.get("doc_title") or c.get("tables") or c.get("tool") or c.get("share_code")
        print(f"  [{c['id']}] kind={c['kind']} {label} kb_id={c.get('kb_id', '-')}")
    print("风险提示：", s["disclaimer"])
    ok = s["done_status"] == "ok" and s["disclaimer_right_before_done"] and bool(s["disclaimer"])
    ok = ok and bool(s["citations"])
    if args.expect_private_doc:
        private = [
            c
            for c in s["citations"]
            if c.get("kind") == "document" and c.get("doc_title") == args.expect_private_doc
        ]
        print(f"引用了私有文档《{args.expect_private_doc}》的出处条数：{len(private)}")
        ok = ok and bool(private)
    print("CHECK", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
