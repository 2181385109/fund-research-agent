package com.fundagent.backend.chat;

import java.io.IOException;

/** 对话事件的下游（HTTP 响应侧）。{@link #send} / {@link #heartbeat} 写失败抛 IOException = 客户端已断开。 */
public interface ChatSink {

    /** 转发一个 SSE 事件（data 是 ai-service 给的一行 JSON 原文）。 */
    void send(String event, String data) throws IOException;

    /** SSE 注释行心跳：保活，并让「客户端已断开」尽快暴露为写失败。 */
    void heartbeat() throws IOException;

    /** 正常结束响应。 */
    void complete();
}
