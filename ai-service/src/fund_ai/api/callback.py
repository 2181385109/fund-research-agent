"""入库结果回调 backend（S6）：``POST {BACKEND_BASE_URL}/internal/documents/callback``，带共享密钥头。

回调地址固定来自配置，不接受请求里给的 URL（防 SSRF）。失败按指数退避重试 ``callback_retries`` 次；
仍失败只记日志——backend 侧有「长时间停留在 PROCESSING 就置为 FAILED」的兜底。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from fund_ai.config import Settings

log = logging.getLogger("fund_ai.api.callback")
SECRET_HEADER = "X-Internal-Secret"
CALLBACK_PATH = "/internal/documents/callback"


async def send_callback(settings: Settings, payload: dict[str, Any]) -> bool:
    url = settings.backend_base_url.rstrip("/") + CALLBACK_PATH
    headers = {SECRET_HEADER: settings.internal_callback_secret.get_secret_value()}
    delay = 0.5
    for attempt in range(1, settings.callback_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=settings.callback_timeout_seconds, trust_env=False
            ) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code < 300:
                return True
            log.warning(
                "callback doc=%s attempt=%d status=%d",
                payload.get("doc_id"),
                attempt,
                r.status_code,
            )
        except httpx.HTTPError as e:
            log.warning(
                "callback doc=%s attempt=%d error=%s",
                payload.get("doc_id"),
                attempt,
                type(e).__name__,
            )
        if attempt < settings.callback_retries:
            await asyncio.sleep(delay)
            delay *= 2
    log.error("callback doc=%s gave up", payload.get("doc_id"))
    return False
