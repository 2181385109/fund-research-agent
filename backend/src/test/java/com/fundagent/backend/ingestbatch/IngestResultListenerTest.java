package com.fundagent.backend.ingestbatch;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class IngestResultListenerTest {

    private final IngestBatchService service = mock(IngestBatchService.class);
    private final IngestResultListener listener = new IngestResultListener(service, new ObjectMapper());

    private static ConsumerRecord<String, String> rec(String value) {
        return new ConsumerRecord<>("doc.ingest.result", 0, 0L, "k", value);
    }

    @Test
    void parsesSnakeCaseResultAndForwardsIt() {
        listener.onResult(rec("{\"schema\":1,\"batch_id\":7,\"task_id\":11,\"doc_id\":\"d1\",\"status\":\"SUCCEEDED\","
                + "\"chunks\":31,\"attempts\":1,\"consumer_id\":\"host-1\",\"unknown_future_field\":true}"));
        ArgumentCaptor<IngestMessages.Result> cap = ArgumentCaptor.forClass(IngestMessages.Result.class);
        verify(service).applyResult(cap.capture());
        IngestMessages.Result r = cap.getValue();
        assertThat(r.batchId()).isEqualTo(7L);
        assertThat(r.taskId()).isEqualTo(11L);
        assertThat(r.docId()).isEqualTo("d1");
        assertThat(r.status()).isEqualTo("SUCCEEDED");
        assertThat(r.chunks()).isEqualTo(31);
        assertThat(r.consumerId()).isEqualTo("host-1");
    }

    @Test
    void unparseableMessageIsDroppedWithoutThrowing() {
        listener.onResult(rec("this is not json"));
        listener.onResult(rec("[1,2,3]"));
        verify(service, never()).applyResult(any());
    }

    @Test
    void requestMessageSerialisesToTheSnakeCaseContract() throws Exception {
        IngestMessages.Request req = new IngestMessages.Request(
                1, 7, 11, "d1", "110022", "名", "quarterly_report", "2026Q2", "t", "data/raw/pdf/d1.pdf",
                "a".repeat(64), "2026-10-03T00:00:00Z");
        var json = new ObjectMapper().readTree(new ObjectMapper().writeValueAsString(req));
        assertThat(json.fieldNames()).toIterable().containsExactlyInAnyOrder(
                "schema", "batch_id", "task_id", "doc_id", "fund_code", "fund_name", "doc_type",
                "report_period", "title", "file_path", "sha256", "requested_at");
    }
}
