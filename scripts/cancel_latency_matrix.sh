#!/usr/bin/env bash
# S9：对 4 种 (传输, 心跳) 组合各跑一遍 scripts/cancel_latency.py（在 WSL / Linux 里运行，和 compose 在同一个时钟上）。
# 每个组合：用环境变量重启 backend 容器 → 等健康 → 每个断开点 N 次。最后恢复默认配置（grpc、心跳 1s）。
#
#   TS=$(date -u +%Y%m%dT%H%M%SZ) N=10 bash scripts/cancel_latency_matrix.sh
#
# 前置：compose 全栈在跑（`docker compose --profile app up -d`），ai-service 有 LLM_API_KEY（会花几十次请求的 token）。
# 结果写到 reports/s9/cancel_latency/<TS>/<配置>.jsonl，汇总用 scripts/summarize_cancel_latency.py。
set -u
cd "$(dirname "$0")/.."
TS="${TS:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="reports/s9/cancel_latency/$TS"
mkdir -p "$OUT"
export NO_PROXY=127.0.0.1,localhost
N="${N:-10}"

run_cell() {
  local transport="$1" hb="$2"
  local label="${transport}_hb${hb}"
  echo "=== $label: 重启 backend"
  AI_TRANSPORT="$transport" CHAT_HEARTBEAT="$hb" docker compose --profile app up -d backend 2>&1 | tail -2
  local st=""
  for _ in $(seq 1 60); do
    st="$(docker inspect -f '{{.State.Health.Status}}' fra-backend 2>/dev/null)"
    [ "$st" = "healthy" ] && break
    sleep 3
  done
  echo "backend health=$st"
  sleep 3
  python3 scripts/cancel_latency.py --label "$label" --note "AI_TRANSPORT=$transport CHAT_HEARTBEAT=$hb" --n "$N" --out "$OUT/$label.jsonl"
}

run_cell grpc 5s
run_cell http 5s
run_cell grpc 1s
run_cell http 1s
AI_TRANSPORT=grpc CHAT_HEARTBEAT=1s docker compose --profile app up -d backend 2>&1 | tail -1
echo ALL_DONE "$OUT"
