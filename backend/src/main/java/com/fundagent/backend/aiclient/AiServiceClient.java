package com.fundagent.backend.aiclient;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.util.List;

/**
 * 主后端调用 AI 服务的接口。第一期实现是 {@link HttpAiServiceClient}（HTTP + SSE），第二期（S9）再加 gRPC 实现，
 * 业务代码只依赖这个接口。
 */
public interface AiServiceClient {

    /** 提交私有文档入库：ai-service 立即返回 202，入库在后台完成后回调 backend（POST /internal/documents/callback）。 */
    void submitIngest(IngestCommand cmd);

    /** 删除一份私有文档在 ai-service 里的全部切块。 */
    void deleteDocument(String aiDocId, String kbId);

    /**
     * 发起一次流式问答。本方法立即返回，事件由后台线程依次回调 {@code listener}；返回的句柄用来取消上游请求
     * （客户端断开时调用，验收 4）。
     */
    ChatStream chat(ChatCommand cmd, ChatListener listener);

    /** 检索范围（ADR-043），字段名与 ai-service 的 KbScopeIn 一致。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record KbScope(boolean includePublic, String ownerId, List<String> privateKbIds) {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record IngestCommand(String docId, String filePath, String kbId, String ownerId, String docTitle, boolean callback) {}

    record HistoryItem(String role, String content) {}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    record ChatCommand(String question, List<HistoryItem> history, String requestId, KbScope kbScope) {}

    /** 一个 SSE 事件：名字与 data（一行 JSON 原文，不解析）。 */
    record SseEvent(String name, String data) {}

    /** 回调都在同一个后台线程里顺序发生；{@link #onEvent} 抛出的异常会被视为「下游已不可用」并取消上游。 */
    interface ChatListener {
        void onEvent(SseEvent event) throws Exception;

        /** 上游正常读完（收到流结束）。 */
        void onComplete();

        /** 上游失败（连接不上、非 200、读取中断、空闲超时）；被主动取消时不会调用。 */
        void onError(Throwable error);
    }

    interface ChatStream {
        /** 取消上游请求：关闭连接，ai-service 侧生成器随之被取消。可重复调用。 */
        void cancel();
    }

    /** ai-service 返回了错误状态或不可达。 */
    class AiServiceException extends RuntimeException {
        public AiServiceException(String message, Throwable cause) {
            super(message, cause);
        }

        public AiServiceException(String message) {
            super(message);
        }
    }
}
