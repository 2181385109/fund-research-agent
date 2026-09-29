"""知识库检索范围（ADR-043）：只能由服务端注入，绝不来自 LLM。

- ``KbScope``：本次请求允许检索的库。``include_public`` = 公共库 ``fund_chunks``；
  ``private_kb_ids`` + ``owner_id`` = 私有库 ``user_chunks`` 中该用户名下的这几个库
  （过滤条件是 ``kb_id in (...) AND owner_id == owner``，两个条件缺一不可）。
- 没有范围（``None`` / 外部 MCP 客户端 / ``/v1/retrieve``）= 只查公共库，默认拒绝私有。
- ``ScopeCodec``：范围随每次 ``search_fund_documents`` 调用放在 HTTP 头 ``X-Fund-Kb-Scope`` 里传给文档 MCP，
  ``base64url(json).hmac_sha256``，带过期时间。校验失败抛 ``ScopeError``——工具报错，**不回退成公共库**。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field

SCOPE_HEADER = "X-Fund-Kb-Scope"
# kb_id / owner_id 会拼进 Milvus 过滤表达式，所以只允许这些字符
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
TOKEN_TTL_SECONDS = 60


class ScopeError(ValueError):
    """检索范围令牌缺失、被篡改或已过期。"""


@dataclass(frozen=True)
class KbScope:
    include_public: bool = True
    owner_id: str = ""
    private_kb_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.private_kb_ids and not self.owner_id:
            raise ValueError("有私有库时必须给出 owner_id")
        for v in (*self.private_kb_ids, *([self.owner_id] if self.owner_id else [])):
            if not _SAFE_ID.match(v):
                raise ValueError(f"非法的 kb_id / owner_id：{v!r}")

    @property
    def has_private(self) -> bool:
        return bool(self.private_kb_ids)

    @classmethod
    def public_only(cls) -> KbScope:
        return cls(include_public=True)

    def to_dict(self) -> dict:
        return {
            "include_public": self.include_public,
            "owner_id": self.owner_id,
            "private_kb_ids": list(self.private_kb_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> KbScope:
        return cls(
            include_public=bool(d.get("include_public", False)),
            owner_id=str(d.get("owner_id", "")),
            private_kb_ids=tuple(str(x) for x in d.get("private_kb_ids", [])),
        )


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class ScopeCodec:
    def __init__(self, secret: str | bytes, ttl_seconds: int = TOKEN_TTL_SECONDS) -> None:
        self._key = secret.encode() if isinstance(secret, str) else secret
        if not self._key:
            raise ValueError("scope 签名密钥不能为空")
        self.ttl = ttl_seconds

    def _sig(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def encode(self, scope: KbScope, now: float | None = None) -> str:
        body = {**scope.to_dict(), "exp": int((now if now is not None else time.time()) + self.ttl)}
        payload = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
        return f"{payload}.{self._sig(payload)}"

    def decode(self, token: str, now: float | None = None) -> KbScope:
        try:
            payload, sig = token.split(".", 1)
            if not hmac.compare_digest(sig, self._sig(payload)):
                raise ScopeError("检索范围签名无效")
            body = json.loads(_unb64(payload))
            if not isinstance(body, dict):
                raise ScopeError("检索范围格式无效")
            if int(body.get("exp", 0)) < (now if now is not None else time.time()):
                raise ScopeError("检索范围已过期")
            return KbScope.from_dict(body)
        except ScopeError:
            raise
        except (ValueError, binascii.Error, TypeError, KeyError) as e:
            raise ScopeError("检索范围格式无效") from e
