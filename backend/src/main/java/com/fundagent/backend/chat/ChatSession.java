package com.fundagent.backend.chat;

/** 一次进行中的对话流。 */
public interface ChatSession {

    /** 客户端已断开（连接关闭、超时、写失败）：取消上游请求，保存已生成的部分。已结束的会话上调用无效果。 */
    void clientGone(String reason);

    boolean isFinished();
}
