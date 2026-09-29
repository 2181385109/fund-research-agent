package com.fundagent.backend.kb.dto;

import com.fundagent.backend.aiclient.AiServiceClient.KbScope;
import com.fundagent.backend.kb.KnowledgeBase;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.time.LocalDateTime;
import java.util.List;

public final class KbDtos {

    private KbDtos() {}

    public record CreateKb(@NotBlank @Size(max = 100) String name) {}

    public record KbView(long id, String name, String type, boolean readOnly, LocalDateTime createdAt) {
        public static KbView of(KnowledgeBase kb) {
            return new KbView(kb.getId(), kb.getName(), kb.getKbType(), kb.isPublic(), kb.getCreatedAt());
        }
    }

    /** 一次提问实际使用的检索范围：kbIds 落库审计，aiScope 发给 ai-service。 */
    public record ResolvedScope(List<Long> kbIds, KbScope aiScope) {}
}
