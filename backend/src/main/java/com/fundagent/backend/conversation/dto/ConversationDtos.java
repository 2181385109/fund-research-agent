package com.fundagent.backend.conversation.dto;

import com.fasterxml.jackson.databind.JsonNode;
import com.fundagent.backend.conversation.Conversation;
import jakarta.validation.constraints.Size;
import java.time.LocalDateTime;

public final class ConversationDtos {

    private ConversationDtos() {}

    public record CreateConversation(@Size(max = 100) String title) {}

    public record ConversationView(long id, String title, LocalDateTime createdAt, LocalDateTime updatedAt) {
        public static ConversationView of(Conversation c) {
            return new ConversationView(c.getId(), c.getTitle(), c.getCreatedAt(), c.getUpdatedAt());
        }
    }

    /** citations / kbIds 是 JSON（原样落库、原样返回）。 */
    public record MessageView(
            long id,
            String role,
            String content,
            JsonNode citations,
            String disclaimer,
            String status,
            String requestId,
            JsonNode kbIds,
            LocalDateTime createdAt) {}
}
