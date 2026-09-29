"""用 reportlab 生成的自造 PDF（CI 用，不含任何真实披露内容；基金名、人名、数字均为虚构）。

中文用 reportlab 自带的 CID 字体 STSong-Light，不依赖系统字体文件。
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
STYLE = ParagraphStyle("zh", fontName=FONT, fontSize=10, leading=14)
GRID = TableStyle(
    [("FONTNAME", (0, 0), (-1, -1), FONT), ("GRID", (0, 0), (-1, -1), 0.5, colors.black)]
)


def _table(rows: list[list[str]]) -> Table:
    t = Table(rows)
    t.setStyle(GRID)
    return t


def _p(text: str) -> Paragraph:
    return Paragraph(text, STYLE)


def build(path: Path, story: list) -> Path:
    SimpleDocTemplate(str(path), pagesize=A4).build(story)
    return path


def prospectus(path: Path) -> Path:
    """两张申购费表：第 1 页养老金客户优惠费率，第 2 页其他客户费率；另有 C 类不收申购费的表述和赎回费表。"""
    return build(
        path,
        [
            _p("假想医疗混合型证券投资基金更新招募说明书"),
            _p("本基金 C 类基金份额不收取申购费用，而是从本类别基金资产中计提销售服务费。" * 3),
            _p("通过基金管理人的直销中心申购本基金 A 类基金份额的养老金客户的优惠申购费率见下表："),
            _table(
                [
                    ["申购费率", "申购金额（M）", "费率"],
                    ["", "M＜100 万元", "0.15%"],
                    ["", "100 万元≤M＜500 万元", "0.10%"],
                    ["", "M≥500 万元", "每笔1000 元"],
                ]
            ),
            PageBreak(),
            _p("其他客户申购本基金A类基金份额的申购费率见下表："),
            _table(
                [
                    ["申购费率", "申购金额（M）", "费率"],
                    ["", "M＜100 万元", "1.50%"],
                    ["", "100 万元≤M＜500 万元", "1.00%"],
                    ["", "M≥500 万元", "每笔1000 元"],
                ]
            ),
            Spacer(1, 12),
            _table(
                [
                    ["赎回费率", "持有期限（N）", "费率"],
                    ["", "N＜7日", "1.50%"],
                    ["", "N≥7日", "0"],
                ]
            ),
        ],
    )


def quarterly_report(path: Path) -> Path:
    return build(
        path,
        [
            _p("假想医疗混合型证券投资基金2026年第2季度报告"),
            _p("3.1 主要财务指标"),
            _table(
                [
                    ["主要财务指标", "报告期（2026年4月1日-2026年6月30日）", ""],
                    ["", "假想医疗混合A", "假想医疗混合C"],
                    ["1.本期已实现收益", "-1,234.56", "-789.01"],
                    ["4.期末基金资产净值", "1,000,000,000.12", "234,567,890.00"],
                    ["5.期末基金份额净值", "1.2345", "1.2001"],
                ]
            ),
            _p("4.1 基金经理（或基金经理小组）简介"),
            _table(
                [
                    ["姓名", "职务", "任本基金的基金经理期限", "", "证券从业年限", "说明"],
                    ["", "", "任职日期", "离任日期", "", ""],
                    ["张三", "基金经理", "2016-09-29", "-", "15年", "虚构"],
                    ["李四", "基金经理", "2025-07-04", "2026-05-06", "9年", "虚构"],
                ]
            ),
            _p("以上内容为测试用虚构文本。" * 20),
        ],
    )
