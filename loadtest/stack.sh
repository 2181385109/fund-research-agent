#!/usr/bin/env bash
# S12：压测栈的切换脚本（在 WSL 里运行；由 run_perf.py 调用，也可手动调用）
#   stack.sh up [cache]     用 mock LLM + 宽松限流重建 mock-llm / mcp-tools / ai-service / backend；带 cache 时打开语义缓存
#   stack.sh up real        场景 E：LLM 换成 .env 里的真实 DeepSeek（密钥只在本脚本里读取，不打印），净值接口仍然是 mock 的 stub
#   stack.sh restore        恢复正常配置（真实 LLM、默认限流、缓存关）
# 环境变量 PERF_BACKEND_JAVA_OPTS 会传给 backend 的 JAVA_TOOL_OPTIONS（JFR 用）
set -euo pipefail
cd "$(dirname "$0")/.."
F="-f docker-compose.yml -f loadtest/compose.loadtest.yml --profile app --profile loadtest"
case "${1:-}" in
  up)
    export PERF_CACHE=false
    for a in "${@:2}"; do
      case "$a" in
        cache) export PERF_CACHE=true ;;
        real)
          getv() { grep -m1 "^$1=" .env | cut -d= -f2-; }
          export PERF_LLM_BASE_URL="$(getv LLM_BASE_URL)" PERF_LLM_API_KEY="$(getv LLM_API_KEY)" PERF_LLM_MODEL="$(getv LLM_MODEL)"
          ;;
      esac
    done
    docker compose $F up -d --no-deps --force-recreate mock-llm mcp-tools ai-service backend
    ;;
  restore)
    docker compose --profile app up -d --no-deps --force-recreate mcp-tools ai-service backend
    docker compose $F rm -sf mock-llm || true
    ;;
  *) echo "usage: stack.sh up [cache] [real] | restore" >&2; exit 2 ;;
esac
