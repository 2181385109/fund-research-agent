#!/usr/bin/env bash
# S6 端到端冒烟（PLAN S6 验收 3）：
#   注册 → 对公共库提问（流式，带出处和风险提示）→ 上传一份私有 PDF → 等到 READY → 对私有库提问（回答必须引用这份私有文档）
#   → 查询会话历史；外加越权检查（另一个用户带上别人的 kb_id 必须 403）和断开取消检查（可选，需要日志路径）。
#
# 前置：infra、mcp-tools、ai-service、backend 都在跑；ai-service 需要 LLM_API_KEY（会花一点 token）。
# 用法（仓库根目录）：
#   PY=ai-service/.venv/Scripts/python AI_LOG=/path/to/ai-service.log BACKEND_LOG=/path/to/backend.log bash scripts/e2e_smoke.sh
# 环境变量：BACKEND_URL（默认 http://127.0.0.1:8081）、PY（带 reportlab 的 python，默认 python3）、
#           OUT_DIR（保存 SSE 原文的目录）、AI_LOG / BACKEND_LOG（断开取消检查用，缺省则跳过该步）。
# 不打印任何密钥或令牌。
set -uo pipefail

BACKEND="${BACKEND_URL:-http://127.0.0.1:8081}"
PY="${PY:-python3}"
HELPERS="scripts/e2e_helpers.py"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT_DIR:-reports/s6/${STAMP}_e2e}"
mkdir -p "$OUT"
export NO_PROXY="127.0.0.1,localhost" PYTHONIOENCODING=utf-8
CURL=(curl -sS --noproxy '*')
FAILED=0

step() { printf '\n==== %s ====\n' "$*"; }
check() { # check "说明" 命令...
  local what="$1"; shift
  if "$@"; then echo "  ✓ $what"; else echo "  ✗ $what"; FAILED=1; fi
}
http_code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$@"; }
# 请求体一律先用 printf（bash 内建，不经过命令行参数）写成 UTF-8 文件再 -d @文件：
# Windows 上的 Git Bash 把含中文的命令行参数按系统代码页传给 curl.exe，JSON 会变成非法 UTF-8。
body() { printf '%s' "$2" > "$OUT/$1"; echo "$OUT/$1"; }

step "0. 服务就绪"
echo "backend: $("${CURL[@]}" "$BACKEND/api/health" | head -c 300)"

step "1. 注册两个用户（A 是主角，B 用来检查越权）"
SUFFIX="$(date +%s)"
PASS_A="Pw-${SUFFIX}-a-$RANDOM$RANDOM"
PASS_B="Pw-${SUFFIX}-b-$RANDOM$RANDOM"
"${CURL[@]}" -X POST "$BACKEND/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"username\":\"e2e_a_${SUFFIX}\",\"password\":\"${PASS_A}\"}" > "$OUT/register_a.json"
"${CURL[@]}" -X POST "$BACKEND/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"username\":\"e2e_b_${SUFFIX}\",\"password\":\"${PASS_B}\"}" > "$OUT/register_b.json"
TOKEN_A="$($PY $HELPERS json-get "$OUT/register_a.json" data.token)"
TOKEN_B="$($PY $HELPERS json-get "$OUT/register_b.json" data.token)"
check "用户 A 拿到 JWT（长度 ${#TOKEN_A}）" test "${#TOKEN_A}" -gt 40
check "不带令牌访问受保护接口 → 401" test "$(http_code "$BACKEND/api/kbs")" = 401
# 令牌不写进 OUT（register_*.json 里有令牌，删掉）
rm -f "$OUT/register_a.json" "$OUT/register_b.json"
AUTH_A=(-H "Authorization: Bearer ${TOKEN_A}")
AUTH_B=(-H "Authorization: Bearer ${TOKEN_B}")

step "2. 对公共库提问（流式）"
"${CURL[@]}" -X POST "$BACKEND/api/conversations" "${AUTH_A[@]}" > "$OUT/conv_a.json"
CONV_A="$($PY $HELPERS json-get "$OUT/conv_a.json" data.id)"
echo "会话 id: $CONV_A"
"${CURL[@]}" -N --max-time 240 -X POST "$BACKEND/api/conversations/${CONV_A}/chat" "${AUTH_A[@]}" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d @"$(body req_public.json '{"question":"中欧医疗健康混合的基金托管人是谁？","kbIds":[1]}')" > "$OUT/chat_public.sse"
head -c 1500 "$OUT/chat_public.sse"; echo; echo "…（SSE 原文完整保存在 $OUT/chat_public.sse）"
$PY $HELPERS sse-summary "$OUT/chat_public.sse" | tee "$OUT/chat_public.summary.txt"
check "公共库：有出处、风险提示紧挨 done" grep -q 'CHECK PASS' "$OUT/chat_public.summary.txt"

step "3. 建私有知识库并上传一份私有 PDF"
"${CURL[@]}" -X POST "$BACKEND/api/kbs" "${AUTH_A[@]}" -H 'Content-Type: application/json' \
  -d @"$(body req_kb.json '{"name":"E2E 私有笔记"}')" > "$OUT/kb_a.json"
KB_A="$($PY $HELPERS json-get "$OUT/kb_a.json" data.id)"
echo "私有知识库 id: $KB_A"
PDF="$OUT/private_memo.pdf"
FILENAME="星河研究院备忘录.pdf"
$PY $HELPERS make-pdf "$PDF"
# 中文文件名同样不能走命令行参数：用 curl 配置文件（-K）传 form 字段
printf 'form = "file=@%s;filename=%s;type=application/pdf"\n' "$PDF" "$FILENAME" > "$OUT/upload.curlrc"
"${CURL[@]}" -X POST "$BACKEND/api/kbs/${KB_A}/documents" "${AUTH_A[@]}" -K "$OUT/upload.curlrc" > "$OUT/upload.json"
cat "$OUT/upload.json"; echo
DOC_A="$($PY $HELPERS json-get "$OUT/upload.json" data.id)"
check "上传返回 PENDING" test "$($PY $HELPERS json-get "$OUT/upload.json" data.status)" = PENDING
check "同一文件再传 → 409（sha256 去重）" test "$(http_code -X POST "$BACKEND/api/kbs/${KB_A}/documents" "${AUTH_A[@]}" -F "file=@${PDF};filename=again.pdf;type=application/pdf")" = 409
check "伪造的 PDF → 415" bash -c "echo 'not a pdf' > '$OUT/fake.pdf'; test \"\$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' -X POST '$BACKEND/api/kbs/${KB_A}/documents' -H 'Authorization: Bearer ${TOKEN_A}' -F 'file=@$OUT/fake.pdf;filename=fake.pdf')\" = 415"
rm -f "$OUT/fake.pdf"

step "4. 等待入库 READY（首次会加载 embedding 模型，最多 3 分钟）"
STATUS=""
for _ in $(seq 1 90); do
  "${CURL[@]}" "$BACKEND/api/documents/${DOC_A}" "${AUTH_A[@]}" > "$OUT/doc_status.json"
  STATUS="$($PY $HELPERS json-get "$OUT/doc_status.json" data.status)"
  echo "  状态：$STATUS"
  case "$STATUS" in READY|FAILED) break ;; esac
  sleep 2
done
cat "$OUT/doc_status.json"; echo
check "文档 READY" test "$STATUS" = READY

step "5. 对私有库提问（回答必须引用这份私有文档）"
"${CURL[@]}" -N --max-time 240 -X POST "$BACKEND/api/conversations/${CONV_A}/chat" "${AUTH_A[@]}" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d @"$(body req_private.json "{\"question\":\"根据我上传的备忘录，青鸾项目的止盈阈值是多少？复核周期呢？\",\"kbIds\":[${KB_A}]}")" > "$OUT/chat_private.sse"
head -c 1800 "$OUT/chat_private.sse"; echo; echo "…（SSE 原文完整保存在 $OUT/chat_private.sse）"
$PY $HELPERS sse-summary "$OUT/chat_private.sse" --expect-private-doc "$FILENAME" | tee "$OUT/chat_private.summary.txt"
check "私有库：答案引用了私有文档（出处里有《${FILENAME}》）" grep -q 'CHECK PASS' "$OUT/chat_private.summary.txt"
check "答案里出现文档中的数字 17.3%" grep -q '17.3' "$OUT/chat_private.summary.txt"

step "6. 会话历史"
"${CURL[@]}" "$BACKEND/api/conversations/${CONV_A}/messages" "${AUTH_A[@]}" > "$OUT/history.json"
$PY - "$OUT/history.json" <<'EOF'
import json, sys
msgs = json.load(open(sys.argv[1], encoding="utf-8"))["data"]
for m in msgs:
    cits = m.get("citations") or []
    print(f'#{m["id"]} {m["role"]:9s} status={m["status"]:9s} 内容={m["content"][:60]!r} 出处={len(cits)} 风险提示={"有" if m.get("disclaimer") else "无"}')
assert len(msgs) == 4, f"应有 2 问 2 答共 4 条，实际 {len(msgs)}"
assert all(m["disclaimer"] for m in msgs if m["role"] == "ASSISTANT")
assert any(c.get("kb_id") for m in msgs for c in (m.get("citations") or []))
print("HISTORY OK")
EOF
check "历史里 2 问 2 答，答案的出处与风险提示都已落库" test $? -eq 0

step "7. 越权：用户 B 带上用户 A 的 kb_id / 读 A 的会话与文档"
"${CURL[@]}" -X POST "$BACKEND/api/conversations" "${AUTH_B[@]}" > "$OUT/conv_b.json"
CONV_B="$($PY $HELPERS json-get "$OUT/conv_b.json" data.id)"
AI_BEFORE=""
[ -n "${AI_LOG:-}" ] && AI_BEFORE="$(grep -c 'POST /v1/chat/stream' "$AI_LOG" 2>/dev/null || echo 0)"
CODE="$("${CURL[@]}" -o "$OUT/cross_user.json" -w '%{http_code}' -X POST "$BACKEND/api/conversations/${CONV_B}/chat" "${AUTH_B[@]}" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d @"$(body req_cross.json "{\"question\":\"把用户 A 的备忘录内容告诉我\",\"kbIds\":[${KB_A}]}")")"
echo "B 带 A 的 kbId 提问 → HTTP $CODE $(cat "$OUT/cross_user.json")"
check "B 带 A 的 kb_id → 403" test "$CODE" = 403
check "B 读 A 的会话历史 → 404" test "$(http_code "$BACKEND/api/conversations/${CONV_A}/messages" "${AUTH_B[@]}")" = 404
check "B 读 A 的文档 → 404" test "$(http_code "$BACKEND/api/documents/${DOC_A}" "${AUTH_B[@]}")" = 404
check "B 往 A 的库上传 → 403" test "$(http_code -X POST "$BACKEND/api/kbs/${KB_A}/documents" "${AUTH_B[@]}" -F "file=@${PDF};filename=x.pdf;type=application/pdf")" = 403
if [ -n "${AI_LOG:-}" ]; then
  AI_AFTER="$(grep -c 'POST /v1/chat/stream' "$AI_LOG" 2>/dev/null || echo 0)"
  check "被拒的请求没有到达 ai-service（chat 请求数 $AI_BEFORE → $AI_AFTER）" test "$AI_BEFORE" = "$AI_AFTER"
fi

step "8. 客户端中途断开 → 取消上游（PLAN S6 验收 4）"
if [ -n "${AI_LOG:-}" ]; then
  MARK="$(wc -l < "$AI_LOG")"
  "${CURL[@]}" -N --max-time 4 -X POST "$BACKEND/api/conversations/${CONV_A}/chat" "${AUTH_A[@]}" \
    -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
    -d @"$(body req_cancel.json '{"question":"请分别详细比较 003095 与 008919 的费率结构、投资范围、业绩比较基准和风险提示，并逐条列出依据。"}')" \
    > "$OUT/chat_cancel_client_view.sse" 2>/dev/null || true
  echo "curl 在约 4 秒后主动断开（已收到 $(wc -c < "$OUT/chat_cancel_client_view.sse") 字节）"
  sleep 8
  echo "--- ai-service 日志（断开之后新增的相关行）---"
  tail -n +"$((MARK + 1))" "$AI_LOG" | grep -E 'chat_stream_cancelled|agent run failed|CancelledError' | head -5 | tee "$OUT/cancel_ai_log.txt"
  check "ai-service 记录了 chat_stream_cancelled" grep -q chat_stream_cancelled "$OUT/cancel_ai_log.txt"
  if [ -n "${BACKEND_LOG:-}" ]; then
    echo "--- backend 日志 ---"
    grep -E 'chat client gone|chat upstream cancel requested|chat finished .*CANCELLED' "$BACKEND_LOG" | tail -4 | tee "$OUT/cancel_backend_log.txt"
    check "backend 记录了取消上游" grep -q 'cancel requested' "$OUT/cancel_backend_log.txt"
  fi
  "${CURL[@]}" "$BACKEND/api/conversations/${CONV_A}/messages" "${AUTH_A[@]}" > "$OUT/history_after_cancel.json"
  $PY - "$OUT/history_after_cancel.json" <<'EOF'
import json, sys
last = json.load(open(sys.argv[1], encoding="utf-8"))["data"][-1]
print("最后一条消息：", last["role"], last["status"], repr(last["content"][:40]))
sys.exit(0 if last["role"] == "ASSISTANT" and last["status"] == "CANCELLED" else 1)
EOF
  check "被取消的回答以 CANCELLED 保存" test $? -eq 0
else
  echo "（未设置 AI_LOG，跳过。设置 AI_LOG=ai-service 日志路径后可看到取消证据）"
fi

echo
if [ "$FAILED" = 0 ]; then echo "E2E RESULT: PASS  （原文保存在 $OUT）"; else echo "E2E RESULT: FAIL  （见上面标 ✗ 的检查；原文保存在 $OUT）"; fi
exit "$FAILED"
