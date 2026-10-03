#!/usr/bin/env bash
# S12：对运行中的 ai-service 容器做 py-spy 采样（旁路容器共享它的 PID 命名空间，不改动被测容器和镜像）。
#   profile_pyspy.sh <输出目录(WSL 路径)> <秒数> [采样率Hz=100] [名字前缀=ai]
# 产出：<前缀>_raw.txt（折叠栈，供 collapse_stacks.py 汇总）和 <前缀>_flame.svg（火焰图）：两次连续采样各 <秒数>，所以负载要持续 2 倍时间
# 需要先在负载下运行（run_perf.py 的另一个终端里调用）。py-spy 从 PyPI 现装在一次性容器里（国内镜像）。
set -euo pipefail
OUT="$1"; DUR="$2"; RATE="${3:-100}"; NAME="${4:-ai}"
mkdir -p "$OUT"
docker run --rm --pid=container:fra-ai-service --cap-add SYS_PTRACE --user root \
  -v "$OUT":/out fra/ai-service:dev bash -c "
    pip install -q -i https://mirrors.aliyun.com/pypi/simple/ py-spy >/dev/null 2>&1
    PID=\$(for p in /proc/[0-9]*; do if tr '\0' ' ' < \$p/cmdline 2>/dev/null | grep -q 'uvicorn fund_ai.api.app'; then basename \$p; break; fi; done)
    echo \"target pid=\$PID\"; py-spy --version
    py-spy record -p \$PID -d $DUR -r $RATE --threads -f raw -o /out/${NAME}_raw.txt >/out/${NAME}_pyspy.log 2>&1
    py-spy record -p \$PID -d $DUR -r $RATE --threads -f flamegraph -o /out/${NAME}_flame.svg >>/out/${NAME}_pyspy.log 2>&1
    py-spy dump -p \$PID > /out/${NAME}_dump_after.txt 2>&1 || true
  "
