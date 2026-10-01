# ruff: noqa: E501
"""S9：量「客户端断开 → ai-service 侧取消」的延迟（HTTP 与 gRPC 对比，PLAN S9 + 统筹补充 2）。

方法：对 backend 的 `POST /api/conversations/{id}/chat`（SSE）发一个会触发工具调用的真实问题，读到指定事件后
**直接关闭 TCP 连接**（模拟浏览器关页面），记下关闭时刻 t_disc；然后从两个容器的日志里找这个 request_id 的：
  - backend：`chat client gone request=<id> reason=<原因>`（backend 发现客户端已断开，并开始取消上游）
  - ai-service：`chat_stream_cancelled request=<id> transport=<grpc|http>`（Agent 生成器被取消）
延迟 = 日志时间戳 − t_disc。因为要和容器日志的时间戳比较，**必须和容器在同一个时钟上运行**：在 WSL（容器所在的虚拟机）里跑本脚本
（Windows 与 WSL 的时钟有 ~100 ms 量级的偏差）。只用标准库。不打印令牌或密钥。

    python3 scripts/cancel_latency.py --label grpc_hb5s --n 10 --out reports/s9/cancel_latency/<ts>/grpc_hb5s.jsonl

backend 用哪种传输 / 心跳间隔由启动 backend 容器时的环境变量决定（AI_TRANSPORT、CHAT_HEARTBEAT），本脚本不改它们，
只把 `--label` 和 `--note` 原样写进结果文件。断开点（`--points`）：meta = 收到 meta 就断（此时 Agent 在等第一次 LLM 响应，没有事件流动），
tool_start = 收到第一个 tool_start 就断（工具在执行、没有事件流动），token = 收到第 3 个 token 就断（事件正在持续流动）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import socket
import statistics
import string
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

QUESTION = "中银创新医疗混合C 的销售服务费率是多少？"
TZ = ZoneInfo("Asia/Shanghai")  # 两个容器都设了 TZ=Asia/Shanghai
AI_TS = re.compile(
    r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) INFO fund_ai\.api\.chat chat_stream_cancelled request=(\S+) transport=(\S+) elapsed_ms=(\d+)"
)
BE_TS = re.compile(
    r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3})[+-]\d\d:\d\d .*chat client gone request=(\S+) reason=(.*?), cancelling"
)


def api(base: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as r:
        return json.loads(r.read())["data"]


def new_user(base: str) -> str:
    name = "cl" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    pw = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    return api(base, "/api/auth/register", {"username": name, "password": pw})["token"]


def open_chat(host: str, port: int, conv: int, token: str) -> socket.socket:
    body = json.dumps({"question": QUESTION}, ensure_ascii=False).encode()
    s = socket.create_connection((host, port), timeout=60)
    head = (
        f"POST /api/conversations/{conv}/chat HTTP/1.1\r\nHost: {host}:{port}\r\nAccept: text/event-stream\r\n"
        f"Content-Type: application/json; charset=utf-8\r\nAuthorization: Bearer {token}\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode()
    s.sendall(head + body)
    return s


def read_until(s: socket.socket, point: str) -> tuple[str | None, float]:
    """读到断开点对应的事件；返回 (request_id, 断开时刻)。读到流结束都没等到断开点则 request_id 仍然返回，时刻为 nan。"""
    buf = b""
    rid = None
    tokens = 0
    while True:
        chunk = s.recv(65536)
        if not chunk:
            return rid, float("nan")
        buf += chunk
        while b"\n\n" in buf.replace(b"\r\n", b"\n"):
            norm = buf.replace(b"\r\n", b"\n")
            block, _, rest = norm.partition(b"\n\n")
            buf = rest
            name = None
            data = None
            for line in block.decode("utf-8", "replace").split("\n"):
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
            if name == "meta" and data:
                rid = json.loads(data).get("request_id")
            if name == "token":
                tokens += 1
            if (
                (point == "meta" and name == "meta")
                or (point == "tool_start" and name == "tool_start")
                or (point == "token" and tokens >= 3)
            ):
                return rid, time.time()
            if name == "done":
                return rid, float("nan")


def docker_logs(container: str, since_epoch: float) -> str:
    since = datetime.fromtimestamp(since_epoch - 2, tz=ZoneInfo("UTC")).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    out = subprocess.run(
        ["docker", "logs", "--since", since, container],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return out.stdout + out.stderr


def epoch_ai(date_s: str, ms: str) -> float:
    return (
        datetime.strptime(date_s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp()
        + int(ms) / 1000
    )


def epoch_be(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=TZ).timestamp()


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8081")
    ap.add_argument("--n", type=int, default=10, help="每个断开点的重复次数")
    ap.add_argument("--points", default="meta,tool_start,token")
    ap.add_argument("--label", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--ai-container", default="fra-ai-service")
    ap.add_argument("--be-container", default="fra-backend")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    host, port = re.match(r"https?://([^:/]+):?(\d+)?", a.backend).groups()
    port = int(port or 80)
    token = new_user(a.backend)
    rows = []
    for point in a.points.split(","):
        for i in range(a.n):
            conv = api(a.backend, "/api/conversations", {"title": "cancel-latency"}, token)["id"]
            t0 = time.time()
            s = open_chat(host, port, conv, token)
            rid, t_disc = read_until(s, point)
            s.close()
            row = {
                "label": a.label,
                "point": point,
                "i": i,
                "request_id": rid,
                "t_disc": t_disc,
                "note": a.note,
            }
            if t_disc != t_disc:  # nan：流在断开点之前就结束了
                row["status"] = "finished_before_disconnect"
                rows.append(row)
                continue
            row["ttd_s"] = round(t_disc - t0, 3)
            ai = be = None
            for _ in range(40):  # 最多等 ~12 s 让日志出现
                time.sleep(0.3)
                ai_log = docker_logs(a.ai_container, t_disc)
                m = next((m for m in AI_TS.finditer(ai_log) if m.group(3) == rid), None)
                if m:
                    ai = (epoch_ai(m.group(1), m.group(2)), m.group(4))
                    be_m = next(
                        (
                            m
                            for m in BE_TS.finditer(docker_logs(a.be_container, t_disc))
                            if m.group(2) == rid
                        ),
                        None,
                    )
                    be = (epoch_be(be_m.group(1)), be_m.group(3)) if be_m else None
                    break
            if ai:
                row["status"] = "cancelled"
                row["ai_cancel_delay_ms"] = round((ai[0] - t_disc) * 1000)
                row["transport_logged"] = ai[1]
                if be:
                    row["backend_gone_delay_ms"] = round((be[0] - t_disc) * 1000)
                    row["backend_reason"] = be[1]
            else:
                row["status"] = "no_cancel_log"
            rows.append(row)
            print(
                json.dumps(
                    {k: row[k] for k in row if k not in ("t_disc", "note")}, ensure_ascii=False
                ),
                flush=True,
            )
            time.sleep(1.0)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n== {a.label}  {a.note}")
    for point in a.points.split(","):
        ok = [r for r in rows if r["point"] == point and r["status"] == "cancelled"]
        bad = [r for r in rows if r["point"] == point and r["status"] != "cancelled"]
        if ok:
            d = [r["ai_cancel_delay_ms"] for r in ok]
            print(
                f"  {point:11s} n={len(ok)}/{a.n} (失败/未取消 {len(bad)})  断开→ai-service取消 ms: median={statistics.median(d):.0f} p90={pct(d, 0.9):.0f} min={min(d)} max={max(d)}"
            )
        else:
            print(f"  {point:11s} 没有成功样本，失败 {len(bad)}")


if __name__ == "__main__":
    main()
