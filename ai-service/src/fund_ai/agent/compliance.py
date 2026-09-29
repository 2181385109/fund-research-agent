"""合规（CLAUDE.md §2 红线 8、PLAN §0）：风险提示的固定文案，以及输出守卫。

- 风险提示由服务端确定性追加（``disclaimer`` 事件），文案固定，不由 LLM 生成，也没有关闭开关。
- 输出守卫只做检测：命中违规表述时打标记并记日志，第一期不改写回答（PLAN S5）。
  这是关键词启发式：命中词前面紧挨着否定语（「不建议买入」「无法推荐购买」）时不算违规，
  因为拒答本身会用到这些词。它会漏报（换个说法就检测不到），评测里另有 advice_request 题检验拒答。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("fund_ai.agent.compliance")

DISCLAIMER = "以上内容基于公开披露信息整理，仅供学习研究，不构成投资建议。基金有风险，投资需谨慎。"

# 违规表述：荐基 / 买卖建议 / 保本保收益类承诺
_PATTERNS: tuple[str, ...] = (
    r"建议(?:您|你|投资者)?(?:买入|购买|申购|加仓|增持|定投|持有|卖出|赎回|减仓|清仓)",
    r"推荐(?:您|你|投资者)?(?:购买|买入|申购|配置|关注)",
    r"(?:值得|适合|可以)(?:买入|购买|申购|加仓|重仓)",
    r"(?:应该|应当|不妨|可以考虑|不如)(?:买入|购买|申购|加仓|卖出|赎回|抄底)",
    r"稳赚",
    r"(?:保证|承诺|确保)(?:保本|收益|盈利)",
    r"包赚",
    r"必涨",
    r"一定(?:会)?(?:涨|赚)",
    r"闭眼(?:买|入)",
    r"最佳买点",
)
_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))

# 命中词之前（同一句内）出现这些否定 / 拒绝 / 疑问表达，视为在陈述「不做该建议」或转述用户的问题
# （「判断现在是否适合加仓」不是在建议加仓）
_NEGATIONS = (
    "不",  # 不能、不会、不便、不建议……
    "无法",
    "没有",
    "未",
    "勿",
    "避免",
    "并非",
    "无从",
    "无权",
    "是否",
    "能否",
    "可否",
    "是不是",
    "该不该",
    "要不要",
    "会不会",
)
_LOOKBACK = 8
_SENTENCE_BREAKS = "。！？!?；;\n"


@dataclass(frozen=True)
class Violation:
    phrase: str
    start: int


def scan_output(text: str) -> list[Violation]:
    out: list[Violation] = []
    for m in _RE.finditer(text):
        head = text[max(0, m.start() - _LOOKBACK) : m.start()]
        # 只看同一句内的前文
        for i in range(len(head) - 1, -1, -1):
            if head[i] in _SENTENCE_BREAKS:
                head = head[i + 1 :]
                break
        if any(n in head for n in _NEGATIONS):
            continue
        out.append(Violation(m.group(0), m.start()))
    return out


def guard_answer(text: str, request_id: str = "") -> list[str]:
    """检测违规表述：返回命中的词（去重、保序）；命中时记 warning 日志（不含回答全文）。"""
    phrases: list[str] = []
    for v in scan_output(text):
        if v.phrase not in phrases:
            phrases.append(v.phrase)
    if phrases:
        log.warning("compliance flagged request=%s phrases=%s", request_id, phrases)
    return phrases
