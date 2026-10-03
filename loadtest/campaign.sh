#!/usr/bin/env bash
# S12 正式战役：A / B / C（缓存关）→ 重启栈（缓存开）→ 预热缓存 → D。在 Git Bash 里从仓库根运行；约 2.5–3 小时。
# 日志：reports/perf/campaign_<ts>.log。每个场景的结果目录由 run_perf.py 打印。
set -uo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 MSYS2_ARG_CONV_EXCL='*'
PY=.venv/Scripts/python
STACK="mock-llm; ratelimit off; quota off; kafka consumer off"
WSLDIR="/mnt/$(pwd | sed -E 's|^/([a-z])/|\1/|')"   # Git Bash 路径 → WSL 路径
wsl_stack() { wsl.exe -d Ubuntu-24.04 -u root -- bash "$WSLDIR/stack.sh" "$@" 2>&1 | tr -d '\0' | tail -3; }
wait_ai() { until curl -s --noproxy '*' http://127.0.0.1:8001/health 2>/dev/null | grep -q '"status":"UP"' && curl -s --noproxy '*' http://127.0.0.1:8081/api/health 2>/dev/null | grep -q 200; do sleep 5; done; }
wait_ai
$PY run_perf.py --scenario A --users 1,2,4,8 --reps 3 --warmup 20 --duration 120 --stop-timeout 120 --stack "$STACK; cache off"
$PY run_perf.py --scenario B --users 1,2,4,8,16 --reps 3 --warmup 20 --duration 120 --stop-timeout 150 --stack "$STACK; cache off"
$PY run_perf.py --scenario C --users 1,2,4,8,16,32,64 --reps 3 --warmup 15 --duration 60 --stop-timeout 120 --stack "$STACK; cache off"
wsl_stack up cache; sleep 20; wait_ai
$PY make_users.py -n 100
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p ../reports/perf/d_prime; POOL=../reports/perf/d_prime/${TS}_confirmed.json
$PY perf_prime_cache.py --out "$POOL"
$PY run_perf.py --scenario D --users 1,4,16,64 --reps 3 --warmup 15 --duration 60 --stop-timeout 60 --d-pool "$POOL" --stack "$STACK; cache ON (threshold 0.80, guard on)"
echo CAMPAIGN_DONE
