from datetime import date
from decimal import Decimal
from pathlib import Path

from fixture_pdfs import prospectus, quarterly_report

from fund_pipeline.docs import check_extractability, pdf_page_texts
from fund_pipeline.pdf_extract import (
    Tenure,
    extract_manager_tenures,
    extract_purchase_fees,
    extract_quarter_net_assets,
)

W = Decimal(10000)


def test_purchase_fee_table_skips_pension_table_and_finds_c_free(tmp_path: Path) -> None:
    res = extract_purchase_fees(prospectus(tmp_path / "p.pdf"))
    assert res.tables_found == 2  # 赎回费表不算
    assert [(t.min_amount, t.max_amount, t.rate, t.fixed_fee) for t in res.a_tiers] == [
        (Decimal(0), 100 * W, Decimal("0.015"), None),
        (100 * W, 500 * W, Decimal("0.01"), None),
        (500 * W, None, None, Decimal(1000)),
    ]
    assert all(t.page == 2 for t in res.a_tiers)
    assert res.c_free_page == 1
    assert "第 2 页" in res.note


def test_quarterly_report_scale_and_managers(tmp_path: Path) -> None:
    pdf = quarterly_report(tmp_path / "q.pdf")
    total, n, page = extract_quarter_net_assets(pdf)
    assert total == Decimal("1234567890.12") and n == 2 and page == 1
    tenures, page = extract_manager_tenures(pdf)
    assert tenures == [
        Tenure("张三", date(2016, 9, 29), None),
        Tenure("李四", date(2025, 7, 4), date(2026, 5, 6)),
    ]


def test_fixture_pdf_is_extractable(tmp_path: Path) -> None:
    texts = pdf_page_texts(quarterly_report(tmp_path / "q.pdf"))
    assert "期末基金资产净值" in "".join(texts)
    assert check_extractability(texts).ok


def test_fee_table_context_classification() -> None:
    from fund_pipeline.pdf_extract import FeeTable

    def special(ctx: str) -> bool:
        return FeeTable(1, ctx, [("M<100万元", "1.5%")]).is_special

    assert special(
        "通过基金管理人的直销中心申购本基金A类基金份额的养老金客户的优惠申购费率见下表："
    )
    assert special(
        "注：上述申购费率适用于除本公司直销柜台养老金客户以外的其他投资者。（2）特定申购费率"
    )
    assert not special("本基金对养老金客户与其他投资者实施差别的申购费率。具体如下：（1）申购费率")
    assert not special(
        "非养老金客户指除养老金客户外的其他投资人。其他投资人申购本基金基金份额申购费率如下所示："
    )
    assert not special("其他客户申购本基金A类基金份额的申购费率见下表：")


def test_purchase_rows_combined_ac_layout() -> None:
    from fund_pipeline.pdf_extract import purchase_rows

    rows = [
        ["费用种类", "A类基金份额", "", "C类基金份额"],
        ["", "情形", "费率", "费率"],
        ["申购费率", "M＜100万", "1.5%", "0%"],
        ["", "100万≤M＜300万", "1.0%", ""],
        ["", "M≥500万", "按笔收取，1,000元/笔", ""],
        ["赎回费率", "Y＜7天", "1.5%", "1.5%"],
    ]
    parsed, c_fees = purchase_rows(rows)
    assert parsed == [
        ("M＜100万", "1.5%"),
        ("100万≤M＜300万", "1.0%"),
        ("M≥500万", "按笔收取，1,000元/笔"),
    ]
    assert c_fees == ["0%"]


def test_net_assets_text_fallback_and_personnel_linebreaks() -> None:
    from fund_pipeline.pdf_extract import net_assets_from_text, parse_personnel_text

    text = (
        "1.本期已实现收益 58,056,590.14 29,099,221.10\n"
        "4.期末基金资产净\n值 1,000.50 2,000.25\n5.期末基金份额净值 1.2 1.1"
    )
    assert net_assets_from_text(text) == [Decimal("1000.50"), Decimal("2000.25")]
    ann = (
        "新任基金经理姓名 张某\n共同管理本基金的其\n他基金经理姓名\n郑某\n"
        "新任基金经\n理姓名 张某\n任职日期 2025 年 04 月 10 日\n证券从业年\n限 10 年\n"
    )
    new, left = parse_personnel_text(ann)
    assert [(t.name, t.start) for t in new] == [("张某", date(2025, 4, 10))] and left == []


def test_fee_table_text_fallback() -> None:
    from fund_pipeline.pdf_extract import fee_tables_from_text

    pages = [
        "（1）场外申购费率\n申购金额(M) 申购费率",
        "M＜50万 1.0%\n50万≤M＜100万 0.6%\nM≥100万 每笔1,000元\n本基金A类基金份额的申购费用由投资人承担",
    ]
    tables = fee_tables_from_text(pages)
    assert len(tables) == 1 and tables[0].page == 2
    assert tables[0].rows == [
        ("M＜50万", "1.0%"),
        ("50万≤M＜100万", "0.6%"),
        ("M≥100万", "每笔1,000元"),
    ]


def test_purchase_rows_side_by_side_pension_column() -> None:
    from fund_pipeline.pdf_extract import purchase_rows

    rows = [
        [
            "申购金额M（含申购\n费）",
            "申购费率（通过直销中\n心申购的养老金客户）",
            "申购费率（其他投资\n者）",
        ],
        ["M＜100万元", "0.12%", "1.20%"],
        ["100万元≤M＜200 万元", "0.06%", "0.60%"],
        ["M≥500万元", "每笔1,000元", None],
    ]
    parsed, _ = purchase_rows(rows)
    assert parsed == [
        ("M＜100万元", "1.20%"),
        ("100万元≤M＜200万元", "0.60%"),
        ("M≥500万元", "每笔1,000元"),
    ]
