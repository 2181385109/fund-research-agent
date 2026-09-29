package com.fundagent.backend.document.dto;

import com.fundagent.backend.document.Document;
import java.time.LocalDateTime;

public record DocumentView(
        long id,
        long kbId,
        String filename,
        long sizeBytes,
        String status,
        String error,
        Integer pages,
        Integer chunks,
        LocalDateTime createdAt) {

    public static DocumentView of(Document d) {
        return new DocumentView(
                d.getId(),
                d.getKbId(),
                d.getFilename(),
                d.getSizeBytes(),
                d.getStatus(),
                d.getError(),
                d.getPages(),
                d.getChunks(),
                d.getCreatedAt());
    }
}
