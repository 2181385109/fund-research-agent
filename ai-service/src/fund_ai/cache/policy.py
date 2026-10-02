"""语义缓存的策略（PLAN S10）：什么问题 / 什么回答能进缓存，以及缓存的隔离命名空间。纯函数，没有 IO。

**不缓存**（任何一条命中就不写入；读取侧只检查「提问本身」能判断的几条）：

- 带对话历史的提问（``history`` 非空）：追问依赖上文（「它的管理费呢」），同一句话在不同对话里意思不同。
- 用过 ``get_latest_nav`` 的回答：最新净值有时效性（PLAN S10 明确要求）；出处里有 ``api`` 类也算（双保险）。
- 荐基 / 买卖建议类提问：合规上每次都应该由 Agent 现场处理，不让缓存把一次回答复用给别人。
- 出错的回答（``error`` 事件、``done.status != ok``、工具服务不可用）、没有回答正文的、达到工具轮数上限的。
- 输出守卫命中违规表述的回答（``compliance_flags`` 非空）。
- 提问里带「今天 / 现在 / 实时…」这类相对时间词：答案随时间变，缓存键里没有日期以外的时间维度。

**隔离**：同一问题在不同的 ``kb_id + kb_version + DATA_AS_OF``（以及换了模型 / 提示词 / 嵌入模型）下答案可能不同，
所以每条缓存都挂在一个命名空间下，查询只在同一命名空间内做近邻检索。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from fund_ai.agent.compliance import looks_like_advice_request
from fund_ai.retrieval.scope import KbScope

# 提问里出现这些词 = 答案依赖「此刻」，不缓存。不含「当前」「最新」：「当前基金经理」「最新一期季报」是静态问题
_TIME_SENSITIVE = re.compile(
    r"今天|今日|当天|昨天|昨日|此刻|现在|实时|刚刚|最新净值|最新估值|今早|今晚"
)
_WS = re.compile(r"\s+")


def normalize_question(q: str) -> str:
    """NFKC（全角 / 半角统一）+ 折叠空白 + 去首尾空白。缓存的嵌入与存储都用规范化后的文本。"""
    return _WS.sub(" ", unicodedata.normalize("NFKC", q)).strip()


def request_uncacheable_reason(question: str, history: Sequence[Any]) -> str | None:
    """只看请求本身就能判断的不缓存原因；None = 可以查缓存（也可能写缓存，写之前还要看回答）。"""
    if history:
        return "history"
    if looks_like_advice_request(question):
        return "advice_request"
    if _TIME_SENSITIVE.search(question):
        return "time_sensitive"
    return None


def answer_uncacheable_reason(
    events: Sequence[Mapping[str, Any]], max_answer_chars: int
) -> str | None:
    """看完整的事件序列，判断这次回答能不能写进缓存；None = 可以。"""
    answer_chars = 0
    done: Mapping[str, Any] | None = None
    for ev in events:
        name, data = ev["event"], ev["data"]
        if name == "error":
            return "error"
        if name == "tool_start" and data.get("name") == "get_latest_nav":
            return "latest_nav"
        if (
            name == "tool_end"
            and data.get("status") == "error"
            and data.get("error_kind") == "unavailable"
        ):
            return "tool_unavailable"
        if name == "token":
            answer_chars += len(data.get("text", ""))
        if name == "citations" and any(i.get("kind") == "api" for i in data.get("items", [])):
            return "latest_nav"
        if name == "done":
            done = data
    if done is None:
        return "incomplete"
    if done.get("status") != "ok":
        return "error"
    if done.get("compliance_flags"):
        return "compliance_flag"
    if done.get("max_steps_reached"):
        return "max_steps"
    if answer_chars == 0:
        return "empty"
    if answer_chars > max_answer_chars:
        return "too_long"
    return None


def cache_namespace(
    scope: KbScope | None,
    private_versions: Mapping[str, str],
    *,
    data_as_of: str,
    public_version: str,
    agent_fingerprint: str,
    embedder_id: str,
) -> str:
    """命名空间 = 检索范围（公共库版本 + 各私有库 id 与版本）+ DATA_AS_OF + 模型 / 提示词 + 嵌入模型 的哈希。

    任何一项变化（文档增删、数据快照换了、提示词或模型改了）都会得到另一个命名空间，旧缓存自然不会被命中。
    返回 24 位十六进制（可直接作 RediSearch TAG 值，不需要转义）。
    """
    include_public = True if scope is None else scope.include_public
    private_ids = [] if scope is None else sorted(scope.private_kb_ids)
    parts = {
        "public": public_version if include_public else None,
        "private": [[k, private_versions.get(k, "")] for k in private_ids],
        "owner": "" if scope is None or not private_ids else scope.owner_id,
        "as_of": data_as_of,
        "agent": agent_fingerprint,
        "embedder": embedder_id,
    }
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
