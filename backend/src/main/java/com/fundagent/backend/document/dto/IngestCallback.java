package com.fundagent.backend.document.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/** ai-service 入库完成后的回调（POST /internal/documents/callback）。ai-service 发的是 snake_case 的 doc_id。 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record IngestCallback(
        @JsonAlias("doc_id") @NotBlank String docId,
        @NotBlank @Pattern(regexp = "READY|FAILED") String status,
        Integer pages,
        Integer chunks,
        String error) {}
