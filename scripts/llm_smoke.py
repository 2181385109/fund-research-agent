"""LLM 冒烟：列模型 + 一次流式对话 + 一次 tool call，结果写 reports/smoke/<UTC时间戳>/summary.json。

- 配置读仓库根目录 .env（或同名环境变量）：LLM_BASE_URL、LLM_API_KEY、LLM_MODEL、
  LLM_THINKING、LLM_TIMEOUT_SECONDS。
- 记录请求模型名、响应 model 字段、usage、耗时（首 token 延迟、总耗时）。
- API key 只用于请求头：不打印、不写入结果文件。
- 任一步失败立即停止并以非 0 退出，不重试（避免烧额度）。

用法（需要 httpx，可用 ai-service 的 venv）：python scripts/llm_smoke.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent

STREAM_PROMPT = "用一句话介绍什么是公募基金的管理费。"
TOOL_PROMPT = "110022 最新净值是多少"
NAV_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_latest_nav",
        "description": "查询一只公募基金份额的最新单位净值和净值日期",
        "parameters": {
            "type": "object",
            "properties": {
                "share_code": {"type": "string", "description": "6 位基金份额代码，例如 110022"}
            },
            "required": ["share_code"],
        },
    },
}


class SmokeError(RuntimeError):
    pass


def load_config(root: Path = ROOT) -> dict[str, str]:
    """.env 中的值，被同名环境变量覆盖。"""
    cfg: dict[str, str] = {}
    env_file = root / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_THINKING", "LLM_TIMEOUT_SECONDS"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def iter_sse_data(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """解析 OpenAI 兼容流式响应的 `data:` 行；遇到 [DONE] 结束。"""
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            return
        if payload:
            yield json.loads(payload)


def check_tool_call(message: dict[str, Any]) -> dict[str, Any]:
    """断言响应里有 get_latest_nav 的 tool_call 且参数含 share_code；返回解析后的参数。"""
    calls = message.get("tool_calls") or []
    if not calls:
        raise SmokeError(f"响应中没有 tool_calls，content={message.get('content')!r}")
    fn = calls[0].get("function", {})
    if fn.get("name") != "get_latest_nav":
        raise SmokeError(f"调用了意外的工具 {fn.get('name')!r}")
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError as e:
        raise SmokeError(f"tool 参数不是合法 JSON: {fn.get('arguments')!r}") from e
    if "share_code" not in args:
        raise SmokeError(f"tool 参数缺少 share_code: {args}")
    return args


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def list_models(client: httpx.Client, base_url: str) -> list[str]:
    resp = client.get(f"{base_url}/models")
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", [])]


def run_stream(client: httpx.Client, base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    first_chunk = first_token = None
    response_models: set[str] = set()
    usage = None
    parts: list[str] = []
    with client.stream("POST", f"{base_url}/chat/completions", json=body) as resp:
        if resp.status_code != 200:
            resp.read()
            raise SmokeError(f"流式请求 HTTP {resp.status_code}: {resp.text[:500]}")
        for chunk in iter_sse_data(resp.iter_lines()):
            now = time.perf_counter()
            if first_chunk is None:
                first_chunk = now
            if chunk.get("model"):
                response_models.add(chunk["model"])
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                text = (choice.get("delta") or {}).get("content")
                if text:
                    if first_token is None:
                        first_token = now
                    parts.append(text)
    end = time.perf_counter()
    if first_token is None:
        raise SmokeError("流式响应中没有任何内容 token")
    return {
        "response_models": sorted(response_models),
        "usage": usage,
        "first_chunk_ms": _ms(first_chunk - start) if first_chunk else None,
        "first_token_ms": _ms(first_token - start),
        "total_ms": _ms(end - start),
        "content": "".join(parts),
    }


def run_tool_call(client: httpx.Client, base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    resp = client.post(f"{base_url}/chat/completions", json=body)
    total = time.perf_counter() - start
    if resp.status_code != 200:
        raise SmokeError(f"tool call 请求 HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    choice = data["choices"][0]
    args = check_tool_call(choice["message"])
    return {
        "response_model": data.get("model"),
        "usage": data.get("usage"),
        "total_ms": _ms(total),
        "finish_reason": choice.get("finish_reason"),
        "tool_name": choice["message"]["tool_calls"][0]["function"]["name"],
        "tool_arguments": args,
    }


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def provenance(cfg: dict[str, str]) -> dict[str, Any]:
    """PLAN §4.4 要求的溯源字段。"""
    status = _git("status", "--porcelain")
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "dataset_sha256": None,  # 冒烟不使用评测集
        "manifest_sha256": _sha256(ROOT / "data" / "MANIFEST.json"),
        "data_as_of": cfg.get("DATA_AS_OF") or None,
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "httpx": httpx.__version__,
        },
        "command": " ".join([Path(sys.executable).name, *sys.argv]),
    }


def main() -> int:
    cfg = load_config()
    base_url = cfg.get("LLM_BASE_URL", "").rstrip("/")
    api_key = cfg.get("LLM_API_KEY", "")
    model = cfg.get("LLM_MODEL", "")
    thinking = cfg.get("LLM_THINKING", "disabled")
    timeout = float(cfg.get("LLM_TIMEOUT_SECONDS", "60"))
    if not (base_url and api_key and model):
        print(
            "缺少 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，先运行 scripts/gen_env.py",
            file=sys.stderr,
        )
        return 2

    params = {
        "base_url": base_url,
        "requested_model": model,
        "thinking": thinking,
        "timeout_s": timeout,
        "temperature": 0,
        "stream_prompt": STREAM_PROMPT,
        "tool_prompt": TOOL_PROMPT,
        "tool": NAV_TOOL["function"]["name"],
        "retries": 0,
    }
    common = {"model": model, "temperature": 0, "thinking": {"type": thinking}}
    stream_body = {
        **common,
        "messages": [{"role": "user", "content": STREAM_PROMPT}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    tool_body = {
        **common,
        "messages": [{"role": "user", "content": TOOL_PROMPT}],
        "tools": [NAV_TOOL],
        "tool_choice": "auto",
    }

    ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "reports" / "smoke" / ts
    summary: dict[str, Any] = {"kind": "llm_smoke", "started_at_utc": ts, "params": params}
    summary.update(provenance(cfg))

    headers = {"Authorization": f"Bearer {api_key}"}
    ok = True
    with httpx.Client(headers=headers, timeout=timeout) as client:
        try:
            summary["available_models"] = list_models(client, base_url)
            print(f"[models] {summary['available_models']}")
            if model not in summary["available_models"]:
                raise SmokeError(f"LLM_MODEL={model} 不在 /models 列表中")

            s = run_stream(client, base_url, stream_body)
            summary["stream"] = s
            print(
                f"[stream] requested={model} response={s['response_models']} "
                f"first_token={s['first_token_ms']}ms total={s['total_ms']}ms usage={s['usage']}"
            )
            print(f"[stream] content: {s['content']}")

            t = run_tool_call(client, base_url, tool_body)
            summary["tool_call"] = t
            print(
                f"[tool_call] requested={model} response={t['response_model']} "
                f"tool={t['tool_name']} args={t['tool_arguments']} total={t['total_ms']}ms "
                f"usage={t['usage']}"
            )
        except (SmokeError, httpx.HTTPError, KeyError, ValueError) as e:
            ok = False
            summary["error"] = f"{type(e).__name__}: {e}"
            print(f"[FAIL] {summary['error']}", file=sys.stderr)

    summary["status"] = "PASS" if ok else "FAIL"
    summary["model_ids"] = {
        "requested": model,
        "response_stream": summary.get("stream", {}).get("response_models"),
        "response_tool_call": summary.get("tool_call", {}).get("response_model"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "summary.json"
    with out_file.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[{summary['status']}] summary → {out_file.relative_to(ROOT).as_posix()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    raise SystemExit(main())
