from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from fund_pipeline.sources import FakeSource
from fund_pipeline.universe import (
    Universe,
    base_name,
    check_constraints,
    classify_theme,
    parse_scale_yi,
    pre_filter,
    screen,
)

AS_OF = date(2026, 9, 30)


def test_classify_theme() -> None:
    assert classify_theme("假想医疗健康混合A") == "医药医疗"
    assert classify_theme("假想半导体产业股票C") == "科技"
    assert classify_theme("假想医疗科技混合") == "歧义"
    assert classify_theme("假想消费精选混合") is None


def test_base_name_and_scale_parsing() -> None:
    assert base_name("假想医疗健康混合A") == ("假想医疗健康混合", "A")
    assert base_name("假想医疗健康混合") == ("假想医疗健康混合", "")
    assert parse_scale_yi("127.36亿元（截止至：2026年06月30日）") == (
        Decimal("127.36"),
        date(2026, 6, 30),
    )
    assert parse_scale_yi("5000.00万元（截止至：2026年06月30日）")[0] == Decimal("0.5")
    assert parse_scale_yi("---")[0] is None


NAMES = pd.DataFrame(
    {
        "基金代码": ["900001", "900002", "900003", "900004", "900005", "900006", "900007"],
        "基金简称": [
            "假想医疗健康混合A",
            "假想医疗健康混合C",
            "假想半导体ETF",
            "假想半导体产业股票",
            "假想医药一年持有混合",
            "假想消费混合",
            "假想中证医疗指数A",
        ],
        "基金类型": [
            "混合型-偏股",
            "混合型-偏股",
            "指数型-股票",
            "股票型",
            "混合型-偏股",
            "混合型-偏股",
            "指数型-股票",
        ],
    }
)


def _overview(est: str, scale: str, company: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "成立日期/规模": f"{est} / 2.0亿份",
                "净资产规模": f"{scale}（截止至：2026年06月30日）",
                "基金管理人": company,
                "基金经理人": "张三",
                "跟踪标的": "该基金无跟踪标的",
                "业绩比较基准": "x",
            }
        ]
    )


def test_pre_filter_groups_share_classes_and_excludes_structures() -> None:
    cands, stats = pre_filter(NAMES)
    by_name = {c.name: c for c in cands}
    assert set(by_name) == {"假想医疗健康混合", "假想半导体产业股票", "假想中证医疗指数"}
    med = by_name["假想医疗健康混合"]
    assert med.share_codes == {"A": "900001", "C": "900002"} and med.has_ac
    assert med.primary_code == "900001" and med.style == "active"
    assert by_name["假想中证医疗指数"].style == "index"
    assert stats["after_name_exclusion_rows"] == 5  # ETF、一年持有被排除


def test_screen_applies_age_scale_and_counts_manager_changes() -> None:
    src = FakeSource(
        {
            "fund_name_em": NAMES,
            ("fund_overview_em", frozenset({("symbol", "900001")})): _overview(
                "2016年09月29日", "127.36亿元", "甲基金"
            ),
            ("fund_overview_em", frozenset({("symbol", "900004")})): _overview(
                "2025年01月10日", "10.00亿元", "乙基金"
            ),
            ("fund_overview_em", frozenset({("symbol", "900007")})): _overview(
                "2018年01月10日", "1.50亿元", "丙基金"
            ),
            ("fund_announcement_personnel_em", frozenset({("symbol", "900001")})): pd.DataFrame(
                {
                    "公告标题": [
                        "x基金经理变更公告",
                        "x基金经理变更公告",
                        "x分红公告",
                        "x基金经理变更公告",
                    ],
                    "公告日期": ["2025-07-04", "2024-10-01", "2025-01-01", "2023-01-01"],
                }
            ),
        }
    )
    cands, stats = screen(src, AS_OF, log=lambda *_: None)
    by_code = {c.primary_code: c for c in cands}
    assert by_code["900004"].reject_reason == "成立不足2年"
    assert by_code["900007"].reject_reason == "规模<2亿"
    ok = by_code["900001"]
    assert ok.reject_reason == "" and ok.company == "甲基金" and ok.scale_yi == "127.36"
    assert ok.manager_changes_2y == 2  # 2023 年那条在两年窗口外，分红公告不算
    assert stats["passed_groups"] == 1
    # 被拒的不再查公告
    assert ("fund_announcement_personnel_em", {"symbol": "900004"}) not in src.calls


def _fund(i: int, theme: str, style: str, company: str, ac: bool, changed: bool) -> dict:
    code = f"{900100 + i}"
    shares = [{"code": code, "share_class": "A" if ac else ""}]
    if ac:
        shares.append({"code": f"{910100 + i}", "share_class": "C"})
    return {
        "code": code,
        "name": f"假想{i}",
        "share_classes": shares,
        "theme": theme,
        "style": style,
        "company": company,
        "established": "2018-01-01",
        "scale_yi": "10",
        "scale_date": "2026-06-30",
        "manager_changed_2y": changed,
        "reason": "测试",
    }


def _universe(funds: list[dict]) -> Universe:
    return Universe.model_validate(
        {
            "version": "v1",
            "frozen": False,
            "screen_report": "reports/universe/x",
            "as_of_screen": "2026-09-30",
            "funds": funds,
        }
    )


def test_check_constraints_passes_on_valid_mix() -> None:
    funds = [
        _fund(
            i,
            "医药医疗" if i < 10 else "科技",
            "index" if i % 3 == 0 else "active",
            f"公司{i % 12}",
            ac=i < 8,
            changed=i < 4,
        )
        for i in range(20)
    ]
    assert check_constraints(_universe(funds)) == []


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda fs: [f.update(company="同一家") for f in fs], "基金公司"),
        (lambda fs: [f.update(manager_changed_2y=False) for f in fs], "换过经理"),
        (lambda fs: [f.update(style="active") for f in fs], "主动:指数"),
    ],
)
def test_check_constraints_reports_violations(mutate, expect: str) -> None:
    funds = [
        _fund(
            i,
            "医药医疗" if i < 10 else "科技",
            "index" if i % 3 == 0 else "active",
            f"公司{i % 12}",
            ac=i < 8,
            changed=i < 4,
        )
        for i in range(20)
    ]
    mutate(funds)
    assert any(expect in p for p in check_constraints(_universe(funds)))
