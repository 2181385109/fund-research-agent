package com.fundagent.backend.document;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.EnumSet;
import org.junit.jupiter.api.Test;

class DocumentStatusTest {

    @Test
    void onlyForwardTransitionsAreAllowed() {
        assertThat(DocumentStatus.PENDING.canMoveTo(DocumentStatus.PROCESSING)).isTrue();
        assertThat(DocumentStatus.PENDING.canMoveTo(DocumentStatus.FAILED)).isTrue();
        assertThat(DocumentStatus.PENDING.canMoveTo(DocumentStatus.READY)).isFalse();
        assertThat(DocumentStatus.PROCESSING.canMoveTo(DocumentStatus.READY)).isTrue();
        assertThat(DocumentStatus.PROCESSING.canMoveTo(DocumentStatus.FAILED)).isTrue();
        assertThat(DocumentStatus.PROCESSING.canMoveTo(DocumentStatus.PENDING)).isFalse();
    }

    @Test
    void readyAndFailedAreTerminal() {
        for (DocumentStatus s : EnumSet.of(DocumentStatus.READY, DocumentStatus.FAILED)) {
            assertThat(s.isTerminal()).isTrue();
            for (DocumentStatus t : DocumentStatus.values()) {
                assertThat(s.canMoveTo(t)).as("%s -> %s", s, t).isFalse();
            }
        }
        assertThat(DocumentStatus.PENDING.isTerminal()).isFalse();
        assertThat(DocumentStatus.PROCESSING.isTerminal()).isFalse();
    }
}
