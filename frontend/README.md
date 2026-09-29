# frontend

Vue 3 + Vite + TypeScript。登录、知识库与文档管理、对话页。接口与 SSE 事件协议以 [docs/API.md](../docs/API.md) 为准。

## 开发

```bash
cd frontend
npm ci
npm run dev        # http://127.0.0.1:5173，/api 代理到 http://127.0.0.1:8081（VITE_BACKEND_URL 可改）
npm test           # vitest：SSE 解析、事件状态、Markdown/角标渲染、出处摘要、风险提示与范围选择组件
npm run build      # vue-tsc 类型检查 + vite build → dist/
```

## 结构

| 路径 | 作用 |
|---|---|
| `src/api/sse.ts` | SSE 解析器（fetch + ReadableStream，不用 EventSource：要带 `Authorization` 头且是 POST）。忽略 `: ping` 心跳，块边界可在任意位置 |
| `src/api/chat.ts` | 对话流：**先看 HTTP 状态码**（流开始前的 401/403/404/503 是普通 JSON），再读流 |
| `src/lib/chatState.ts` | `applyEvent`：SSE 事件 → 回答状态（文本、工具状态、出处、风险提示） |
| `src/lib/render.ts` | Markdown → 清洗后的 HTML，`[n]` → 按出处类型区分的角标 |
| `src/lib/citations.ts` | 四类出处（document / database / computation / api）的摘要与详情行 |
| `src/components/DisclaimerBar.vue` | 风险提示：常驻，无关闭 / 折叠入口 |
| `src/components/ScopePicker.vue` | 检索范围：公共库 / 私有库勾选 |
| `src/views/` | `LoginView`、`KbView`（知识库与文档，轮询入库状态）、`ChatView` |

## 约定

- 风险提示有两层，都不能关闭：每条回答下显示服务端下发的 `disclaimer` 原文（历史消息取 `disclaimer` 字段；没有时回退到固定文案），页面底部另有常驻提示。
- 检索范围由用户勾选后以 `kbIds` 发给 backend；**只是申请，不是授权**：backend 逐个校验，任何一个不是自己可访问的库就整个请求 403（ADR-043）。
- 部署：`deploy/frontend/Dockerfile` 构建后由 nginx 提供静态文件并反向代理 `/api`，对话流接口关闭代理缓冲（`deploy/frontend/nginx.conf`）。
