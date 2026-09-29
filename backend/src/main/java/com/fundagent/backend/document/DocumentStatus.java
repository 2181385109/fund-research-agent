package com.fundagent.backend.document;

import java.util.EnumSet;
import java.util.Set;

/** 文档状态机：PENDING → PROCESSING → READY / FAILED。READY、FAILED 是终态。 */
public enum DocumentStatus {
    PENDING,
    PROCESSING,
    READY,
    FAILED;

    public Set<DocumentStatus> next() {
        return switch (this) {
            case PENDING -> EnumSet.of(PROCESSING, FAILED);
            case PROCESSING -> EnumSet.of(READY, FAILED);
            case READY, FAILED -> EnumSet.noneOf(DocumentStatus.class);
        };
    }

    public boolean canMoveTo(DocumentStatus target) {
        return next().contains(target);
    }

    public boolean isTerminal() {
        return next().isEmpty();
    }
}
