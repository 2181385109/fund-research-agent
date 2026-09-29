package com.fundagent.backend.testsupport;

import com.fundagent.backend.aiclient.AiServiceClient;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.RejectedExecutionException;

/** 测试用 AiServiceClient：记录调用；对话流由测试通过 {@link #listener} 手动驱动。 */
public class FakeAiServiceClient implements AiServiceClient {

    public final List<IngestCommand> ingests = new ArrayList<>();
    public final List<String[]> deletes = new ArrayList<>();
    public final List<ChatCommand> chats = new ArrayList<>();
    public volatile ChatListener listener;
    public volatile boolean cancelled;
    public RuntimeException ingestFailure;
    public RuntimeException deleteFailure;
    public boolean rejectChat;

    @Override
    public void submitIngest(IngestCommand cmd) {
        if (ingestFailure != null) {
            throw ingestFailure;
        }
        ingests.add(cmd);
    }

    @Override
    public void deleteDocument(String aiDocId, String kbId) {
        if (deleteFailure != null) {
            throw deleteFailure;
        }
        deletes.add(new String[] {aiDocId, kbId});
    }

    @Override
    public ChatStream chat(ChatCommand cmd, ChatListener listener) {
        if (rejectChat) {
            throw new RejectedExecutionException("full");
        }
        chats.add(cmd);
        this.listener = listener;
        this.cancelled = false;
        return () -> cancelled = true;
    }
}
