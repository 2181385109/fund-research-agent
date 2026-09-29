"""reportlab 生成的自造 PDF（CI 用；基金名、数字、文字全部虚构）。

- ``report_like``：模仿季报——每页相同的页眉「假想医疗混合型证券投资基金2026年第2季度报告」
  和页脚「第 N 页 共 M 页」、目录行、§ 与 4.4 这类章节标题、一张费率表、一张持仓表、
  一个超长段落（强制切块）；
- ``empty_pdf``：只有一页空白。
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
STYLE = ParagraphStyle("zh", fontName=FONT, fontSize=10, leading=14)
HEADER = "假想医疗混合型证券投资基金2026年第2季度报告"
LONG_PARA = "本基金坚持长期投资理念，重点关注创新药产业链的结构性机会" * 40 + "。"
STRATEGY = "报告期内，本基金维持较高仓位，增配了创新药板块，减持了部分医疗服务标的。"


def _table(rows: list[list[str]]) -> Table:
    t = Table(rows)
    t.setStyle(
        TableStyle(
            [("FONTNAME", (0, 0), (-1, -1), FONT), ("GRID", (0, 0), (-1, -1), 0.5, colors.black)]
        )
    )
    return t


def _p(text: str) -> Paragraph:
    return Paragraph(text, STYLE)


def report_like(path: Path) -> Path:
    total = 3

    def on_page(c: canvas.Canvas, doc) -> None:
        c.setFont(FONT, 9)
        c.drawString(72, A4[1] - 40, HEADER)
        c.drawString(260, 30, f"第 {doc.page} 页 共 {total} 页")

    story = [
        _p("§1 重要提示......................................2"),
        _p("§4 管理人报告"),
        _p("4.4 报告期内基金的投资策略和运作分析"),
        _p(STRATEGY),
        _p(LONG_PARA),
        PageBreak(),
        _p("§5 投资组合报告"),
        _p("5.3 报告期末前十名股票投资明细"),
        _table(
            [
                ["序号", "股票代码", "股票名称", "占基金资产净值比例（%）"],
                ["1", "600001", "假想药业", "9.87"],
                ["2", "600002", "假想医疗", "8.10"],
            ]
        ),
        Spacer(1, 12),
        _p("注：以上为虚构数据。"),
        PageBreak(),
        _p("§6 费用"),
        _p("6.1 申购费率"),
        _table(
            [["申购金额（M）", "申购费率"], ["M＜100万元", "1.50%"], ["M≥100万元", "每笔1000元"]]
        ),
    ]
    SimpleDocTemplate(str(path), pagesize=A4).build(
        story, onFirstPage=on_page, onLaterPages=on_page
    )
    return path


def empty_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.showPage()
    c.save()
    return path
