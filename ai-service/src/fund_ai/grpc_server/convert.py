"""dict（Agent 事件 / HTTP 响应体）→ protobuf 消息。

字段名与 HTTP 的 JSON 完全一致（见 proto/fundagent/v1/ai_service.proto），所以直接用
``json_format.ParseDict``：这样事件协议只有一个事实来源（Agent 产出的 dict），proto 与它不一致时
测试会立刻失败（严格模式不接受未知字段）。生产路径上遇到未知字段不能让流中断（disclaimer 必须在
done 之前发出），所以退回宽松解析并记一条 error 日志。
"""

from __future__ import annotations

import logging
from typing import Any

from google.protobuf import json_format
from google.protobuf.message import Message

from fundagent.v1 import ai_service_pb2 as pb

log = logging.getLogger("fund_ai.grpc_server.convert")

# SSE 事件名 → 消息类型（与 ChatEvent.oneof 的字段名一致）
EVENT_TYPES: dict[str, type[Message]] = {
    "meta": pb.Meta,
    "tool_start": pb.ToolStart,
    "tool_end": pb.ToolEnd,
    "token": pb.Token,
    "disclaimer": pb.Disclaimer,
    "done": pb.Done,
    "error": pb.ChatError,
}
CITATION_KINDS = ("document", "database", "computation", "api")


def fill(data: dict[str, Any], msg: Message, *, strict: bool = False) -> Message:
    """把 dict 填进 msg；strict=False 时未知字段记 error 日志后忽略。"""
    try:
        json_format.ParseDict(data, msg, ignore_unknown_fields=False)
    except json_format.ParseError as e:
        if strict:
            raise
        log.error("proto 缺少字段或类型不符，已忽略出错字段后重试：%s", str(e)[:300])
        msg.Clear()
        json_format.ParseDict(data, msg, ignore_unknown_fields=True)
    return msg


def citation_to_proto(item: dict[str, Any], *, strict: bool = False) -> pb.Citation:
    kind = item["kind"]
    msg = pb.Citation(id=item["id"], kind=kind)
    if kind in CITATION_KINDS:
        detail = {k: v for k, v in item.items() if k not in ("id", "kind")}
        fill(detail, getattr(msg, kind), strict=strict)
    else:  # 未知的出处类型：保留 id / kind，不丢整条事件
        log.error("未知的出处类型 %r（proto 没有对应的 detail）", kind)
    return msg


def event_to_proto(ev: dict[str, Any], *, strict: bool = False) -> pb.ChatEvent:
    """Agent 事件 ``{"event": 名字, "data": dict}`` → ChatEvent。"""
    name, data = ev["event"], ev["data"]
    out = pb.ChatEvent()
    if name == "citations":
        out.citations.items.extend(citation_to_proto(i, strict=strict) for i in data["items"])
        return out
    cls = EVENT_TYPES.get(name)
    if cls is None:
        raise ValueError(f"未知事件 {name!r}")
    getattr(out, name).CopyFrom(fill(data, cls(), strict=strict))
    return out


def retrieve_response(result: dict[str, Any], *, strict: bool = False) -> pb.RetrieveResponse:
    return fill(result, pb.RetrieveResponse(), strict=strict)


def to_dict(msg: Message) -> dict[str, Any]:
    """测试 / 对照用：消息 → 与 HTTP 一致字段名的 dict（保留 proto 字段名）。"""
    return json_format.MessageToDict(
        msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True
    )
