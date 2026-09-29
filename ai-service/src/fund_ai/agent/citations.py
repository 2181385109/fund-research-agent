"""出处（PLAN §5 S5）：工具结果 → 编号出处，回答里的 [n] 校验与流式过滤。

一次请求内所有工具结果共用一套编号（从 1 开始，全局唯一）：
- ``search_fund_documents`` 每个片段一个编号（同一切块重复命中沿用旧编号）→ ``kind=document``；
- ``run_fund_sql`` 每次成功的查询一个编号 → ``database``（表名、source、as_of）；
- ``calc_fund_return`` 每次一个编号 → ``computation``（入参、实际使用的起止日）；
- ``get_latest_nav`` 每次一个编号 → ``api``（source、nav_date、fetched_at，stale 时标注）；
- ``get_fund_db_schema`` 不是出处，不编号。
给 LLM 看的文本里把编号写成 ``[n]``；LLM 在回答里用 [n] 引用。回答里出现了不存在的 [n] 时：
流式输出中直接丢弃（``CitationStreamFilter``，不会到达用户），并记日志、计入 ``done.dropped_citations``。
``citations`` 事件只列回答里实际引用了的出处。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("fund_ai.agent.citations")

SNIPPET_MAX_CHARS = 400  # citations 事件里带的片段长度上限（给 LLM 的正文不截断）


@dataclass
class Source:
    id: int
    kind: str  # document | database | computation | api
    data: dict[str, Any]

    def to_event(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, **self.data}


@dataclass
class CitationRegistry:
    sources: dict[int, Source] = field(default_factory=dict)
    _chunk_ids: dict[str, int] = field(default_factory=dict)
    cited: list[int] = field(default_factory=list)  # 回答里出现过的有效编号（按首次出现顺序）
    dropped: list[int] = field(default_factory=list)  # 回答里出现过的无效编号

    def add(self, kind: str, data: dict[str, Any], dedup_key: str | None = None) -> int:
        if dedup_key is not None and dedup_key in self._chunk_ids:
            return self._chunk_ids[dedup_key]
        sid = len(self.sources) + 1
        self.sources[sid] = Source(sid, kind, data)
        if dedup_key is not None:
            self._chunk_ids[dedup_key] = sid
        return sid

    def is_valid(self, n: int) -> bool:
        return n in self.sources

    def mark_cited(self, n: int) -> None:
        if n not in self.cited:
            self.cited.append(n)

    def mark_dropped(self, n: int) -> None:
        if n not in self.dropped:
            self.dropped.append(n)

    def cited_events(self) -> list[dict[str, Any]]:
        return [self.sources[n].to_event() for n in sorted(self.cited)]


# ---------------------------------------------------------------- 工具结果 → 出处 + 给 LLM 的文本


def _compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def register_tool_result(
    reg: CitationRegistry, tool: str, args: dict[str, Any], data: dict[str, Any] | None, text: str
) -> tuple[str, list[int]]:
    """成功的工具结果：登记出处，返回 (给 LLM 的文本, 本次新登记或复用的编号列表)。
    data 是工具返回的 JSON（解析失败时为 None，此时原样返回文本，不登记出处）。"""
    if data is None:
        return text, []
    if tool == "search_fund_documents":
        return _register_documents(reg, data)
    if tool == "run_fund_sql":
        return _register_sql(reg, data)
    if tool == "calc_fund_return":
        return _register_computation(reg, args, data)
    if tool == "get_latest_nav":
        return _register_nav(reg, data)
    return text, []


def _register_documents(reg: CitationRegistry, data: dict[str, Any]) -> tuple[str, list[int]]:
    results = data.get("results") or []
    if not results:
        return "没有检索到相关片段（文档库中找不到依据）。", []
    ids: list[int] = []
    blocks: list[str] = [f"检索到 {len(results)} 个片段，引用时用方括号里的编号："]
    for r in results:
        pages = (
            f"第{r['page_start']}页"
            if r["page_start"] == r["page_end"]
            else f"第{r['page_start']}–{r['page_end']}页"
        )
        private = bool(r.get("kb_id"))
        extra = {"kb_id": r["kb_id"]} if private else {}
        sid = reg.add(
            "document",
            {
                **extra,
                "fund_code": r["fund_code"],
                "fund_name": r["fund_name"],
                "doc_id": r["doc_id"],
                "doc_type": r["doc_type"],
                "doc_title": r["doc_title"],
                "report_period": r["report_period"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
                "section": r["section"],
                "snippet": r["text"][:SNIPPET_MAX_CHARS],
            },
            dedup_key=r["chunk_id"],
        )
        if sid not in ids:
            ids.append(sid)
        if private:  # 用户上传的文档：没有基金，文档名是文件名
            head = f"[{sid}] 用户文档《{r['doc_title']}》｜{pages}"
        else:
            head = f"[{sid}] {r['fund_name']}（{r['fund_code']}）｜{r['doc_title']}｜{pages}"
        if r["section"]:
            head += f"｜{r['section']}"
        blocks.append(f"{head}\n{r['text']}")
    return "\n\n".join(blocks), ids


def _register_sql(reg: CitationRegistry, data: dict[str, Any]) -> tuple[str, list[int]]:
    sid = reg.add(
        "database",
        {
            "tables": data.get("tables", []),
            "source": data.get("source", ""),
            "as_of": data.get("as_of", ""),
            "sql": data.get("executed_sql", ""),
            "row_count": data.get("row_count", 0),
        },
    )
    body = {
        "columns": data.get("columns"),
        "rows": data.get("rows"),
        "row_count": data.get("row_count"),
        "truncated": data.get("truncated"),
    }
    text = (
        f"[{sid}] 数据库查询结果（来源：{data.get('source', '')}；数据截至 {data.get('as_of', '')}）\n"
        f"{_compact(body)}"
    )
    if data.get("truncated"):
        text += f"\n注意：结果被截断为 {data.get('max_rows')} 行，不是全部数据。"
    return text, [sid]


def _register_computation(
    reg: CitationRegistry, args: dict[str, Any], data: dict[str, Any]
) -> tuple[str, list[int]]:
    sid = reg.add(
        "computation",
        {
            "tool": "calc_fund_return",
            "args": args,
            "share_code": data.get("share_code", args.get("share_code")),
            "start_used": data.get("start_used"),
            "end_used": data.get("end_used"),
            "source": data.get("source", ""),
            "as_of": data.get("as_of", ""),
        },
    )
    return f"[{sid}] 收益计算结果（确定性计算，直接引用 display 里的百分数）\n{_compact(data)}", [
        sid
    ]


def _register_nav(reg: CitationRegistry, data: dict[str, Any]) -> tuple[str, list[int]]:
    stale = bool(data.get("stale"))
    sid = reg.add(
        "api",
        {
            "share_code": data.get("share_code"),
            "source": data.get("source", ""),
            "nav_date": data.get("nav_date"),
            "fetched_at": data.get("fetched_at"),
            "stale": stale,
        },
    )
    text = f"[{sid}] 最新净值查询结果\n{_compact(data)}"
    if stale:
        text += "\n注意：接口不可用，这是快照数据而不是最新净值，回答时必须说明「非最新」并给出快照日期。"
    return text, [sid]


# ---------------------------------------------------------------- 流式过滤


_REF_BODY = re.compile(r"\d{1,3}(?:\s*[,，、]\s*\d{1,3})*")
_REF_CHARS = set(",，、 ")
_MAX_REF_LEN = 16  # 超过就不可能是编号（「[1, 2, 3]」这种列表也够了）


class CitationStreamFilter:
    """流式文本里的 ``[n]`` 校验：有效编号放行并记为已引用；无效编号丢弃并记日志。

    token 边界可能切在 ``[12`` 与 ``]`` 之间，所以遇到 ``[`` 后先缓冲，等到能判断为止。
    ``feed`` 返回可以立即发给用户的文本；一轮结束（或整个回答结束）时调用 ``flush``。
    """

    def __init__(self, registry: CitationRegistry, request_id: str = "") -> None:
        self.reg = registry
        self.request_id = request_id
        self._buf = ""  # 以 "[" 开头、尚未确定的片段

    def feed(self, text: str) -> str:
        out: list[str] = []
        for ch in text:
            if self._buf:
                self._buf += ch
                if ch == "]":
                    out.append(self._resolve())
                elif not (ch.isdigit() or ch in _REF_CHARS) or len(self._buf) > _MAX_REF_LEN:
                    # 不是 [数字]：原样放行；这个字符本身可能又是一个新的 "["
                    if ch == "[":
                        out.append(self._buf[:-1])
                        self._buf = "["
                    else:
                        out.append(self._buf)
                        self._buf = ""
            elif ch == "[":
                self._buf = "["
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest

    def _resolve(self) -> str:
        buf, self._buf = self._buf, ""
        body = buf[1:-1]
        if not _REF_BODY.fullmatch(body):
            return buf
        keep: list[int] = []
        for n in (int(x) for x in re.findall(r"\d+", body)):
            if self.reg.is_valid(n):
                self.reg.mark_cited(n)
                keep.append(n)
            else:
                self.reg.mark_dropped(n)
                log.warning("dropped invalid citation [%d] request=%s", n, self.request_id)
        # 只有单个有效编号时原样放行；「[1, 2]」这类列表规整成「[1][2]」，无效的丢掉
        if len(keep) == 1 and body.strip().isdigit():
            return buf
        return "".join(f"[{n}]" for n in keep)
