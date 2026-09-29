"""评测集条目 schema（PLAN §4.3），fund_qa 与 agent_tasks 共用。

gold_value 约定（详见 eval/datasets/SCHEMA.md）：
- numeric：字符串「数值+单位」，如 ``"1.20%"``、``"247.70亿元"``、``"0.1200元"``；
  ``tolerance`` 是同一展示单位下的绝对容差（``"1.20%"`` + ``0.01`` = ±0.01 个百分点）。
  ``parse_numeric`` 把它换算成基本单位（% → 小数、亿元/万元 → 元），用来和 gold_sql 的结果比对。
- entity：字符串（人名、股票名、日期 YYYY-MM-DD 等）。
- list：字符串列表，判分按集合比对，顺序无关。
- text：``null``，按 answer_points 判分。
- refusal：``null``。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QA_TOPICS = (
    "fee",
    "manager",
    "holdings",
    "performance",
    "contract_clause",
    "commentary",
    "cross_doc",
    "unanswerable",
)
AGENT_TOPICS = (
    "tool_sql",
    "calc_return",
    "latest_nav",
    "doc_db",
    "advice_request",
    "doc_only",
    "no_tool",
)
TOOLS = (
    "search_fund_documents",
    "get_fund_db_schema",
    "run_fund_sql",
    "calc_fund_return",
    "get_latest_nav",
)
PROVENANCE = ("template_reference_script", "llm_draft", "llm_draft_human_verified")

# PLAN §5 S3 各类最少题数
QA_MIN = {
    "fee": 15,
    "manager": 10,
    "holdings": 15,
    "performance": 10,
    "contract_clause": 15,
    "commentary": 15,
    "cross_doc": 10,
    "unanswerable": 10,
}
QA_MIN_TOTAL = 100
QA_MIN_PARAPHRASE = 30
AGENT_MIN = {
    "tool_sql": 20,
    "calc_return": 8,
    "latest_nav": 5,
    "doc_db": 12,
    "advice_request": 5,
    "doc_only": 7,
    "no_tool": 3,
}
AGENT_MIN_TOTAL = 60


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    quote: str = Field(min_length=4)
    page: int = Field(ge=1)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^(qa|agent)-\d{4}$")
    version: Literal["v1"] = "v1"
    split: Literal["dev", "test"]
    question: str = Field(min_length=6)
    fund_codes: list[str]
    topic: str
    style: Literal["keyword", "paraphrase"]
    answerable: bool
    answer_type: Literal["numeric", "entity", "list", "text", "refusal"]
    gold_value: str | list[str] | None = None
    tolerance: float | None = None
    reference_answer: str
    answer_points: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    gold_sql: str | None = None
    # calc_return / latest_nav 题：参考计算的入参（份额代码、起止日等），便于复核
    gold_params: dict | None = None
    # 标准答案出自哪个参考函数 / 表（可追溯）
    gold_source: str = ""
    volatile: bool = False
    provenance: str
    verified_by: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Item:
        topics = QA_TOPICS if self.id.startswith("qa-") else AGENT_TOPICS
        if self.topic not in topics:
            raise ValueError(f"{self.id}: topic {self.topic!r} 不在 {topics}")
        if self.provenance not in PROVENANCE:
            raise ValueError(f"{self.id}: provenance {self.provenance!r}")
        for t in self.expected_tools:
            if t not in TOOLS:
                raise ValueError(f"{self.id}: 未知工具 {t!r}")
        at = self.answer_type
        if not self.answerable and at != "refusal":
            raise ValueError(f"{self.id}: answerable=false 时 answer_type 必须是 refusal")
        if at == "numeric" and self.volatile:
            # 最新净值类：评测时实时抓取比对（PLAN §4.3），这里不存数值
            if self.gold_value is not None or not self.gold_params:
                raise ValueError(
                    f"{self.id}: volatile 题 gold_value 须为 null，且必须有 gold_params"
                )
        elif at == "numeric":
            if not isinstance(self.gold_value, str):
                raise ValueError(f"{self.id}: numeric 题 gold_value 须为「数值+单位」字符串")
            parse_numeric(self.gold_value)
            if self.tolerance is None:
                raise ValueError(f"{self.id}: numeric 题必须给 tolerance")
        elif at == "entity":
            if not isinstance(self.gold_value, str) or not self.gold_value:
                raise ValueError(f"{self.id}: entity 题 gold_value 须为非空字符串")
        elif at == "list":
            if not isinstance(self.gold_value, list) or not self.gold_value:
                raise ValueError(f"{self.id}: list 题 gold_value 须为非空列表")
        elif at in ("text", "refusal") and self.gold_value is not None:
            raise ValueError(f"{self.id}: {at} 题 gold_value 须为 null")
        if at == "text" and not self.answer_points:
            raise ValueError(f"{self.id}: text 题必须有 answer_points")
        if self.volatile and self.topic != "latest_nav":
            raise ValueError(f"{self.id}: 只有 latest_nav 题可以 volatile")
        return self


_NUM = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(%|亿元|万元|元|亿份|万份|份|股|天|年|人|次|只|家)?$")
_SCALE = {
    "%": Decimal("0.01"),
    "亿元": Decimal("100000000"),
    "万元": Decimal("10000"),
    "亿份": Decimal("100000000"),
    "万份": Decimal("10000"),
}


def parse_numeric(value: str) -> tuple[Decimal, str]:
    """``"1.20%"`` → (Decimal('0.0120'), '%')；换算到基本单位（小数 / 元 / 份）。"""
    m = _NUM.match(value.strip())
    if not m:
        raise ValueError(f"无法解析的 numeric gold_value: {value!r}")
    unit = m.group(2) or ""
    return Decimal(m.group(1)) * _SCALE.get(unit, Decimal(1)), unit


def tolerance_base(value: str, tolerance: float | None) -> Decimal:
    """把展示单位下的容差换算到基本单位。"""
    _, unit = parse_numeric(value)
    return Decimal(str(tolerance or 0)) * _SCALE.get(unit, Decimal(1))
