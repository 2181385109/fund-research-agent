#!/usr/bin/env bash
# S12：每 ~1.5 s 采一次 docker stats（每行：epoch 秒 + docker 的 JSON）。存在停止文件时退出。
#   dockerstats.sh <输出文件> <停止文件>
out="$1"; stop="$2"
rm -f "$stop"
while [ ! -e "$stop" ]; do
  ts=$(date +%s.%N)
  docker stats --no-stream --format '{{json .}}' | sed "s/^/$ts /" >> "$out"
done
