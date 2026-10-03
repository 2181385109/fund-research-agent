package com.fundagent.backend.ingestbatch;

import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.common.GlobalExceptionHandler;
import com.fundagent.backend.common.RequestIdFilter;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Created;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.Progress;
import com.fundagent.backend.ingestbatch.IngestBatchDtos.TaskView;
import java.time.LocalDateTime;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

/** 批次接口：参数校验、202 / 200（幂等）、进度响应体、404。 */
@WebMvcTest(IngestBatchController.class)
@Import({GlobalExceptionHandler.class, RequestIdFilter.class})
class IngestBatchControllerTest {

    @Autowired
    MockMvc mvc;

    @MockitoBean
    IngestBatchService service;

    private static final long USER = 7;

    private static RequestPostProcessor asUser() {
        return req -> {
            req.setAttribute(AuthInterceptor.USER_ID_ATTR, USER);
            return req;
        };
    }

    private static Progress progress(long id, String status, int succeeded, int processing) {
        return new Progress(
                id,
                "2026Q3",
                "quarterly_report",
                status,
                20,
                succeeded,
                0,
                20 - succeeded - processing,
                processing,
                0,
                processing,
                LocalDateTime.of(2026, 10, 25, 9, 0),
                null,
                List.of(new TaskView(5, "110022_quarterly_report_2026Q3", "FAILED", null, 3, "boom", "c1", null)),
                null);
    }

    @Test
    void createNewBatchReturns202WithProgress() throws Exception {
        when(service.create(USER, "2026Q3")).thenReturn(new Created(true, progress(9, "RUNNING", 0, 20)));
        mvc.perform(post("/api/ingest-batches")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportPeriod\":\"2026Q3\"}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.created").value(true))
                .andExpect(jsonPath("$.data.batch.batchId").value(9))
                .andExpect(jsonPath("$.data.batch.total").value(20))
                .andExpect(jsonPath("$.data.batch.processing").value(20))
                .andExpect(jsonPath("$.data.batch.status").value("RUNNING"));
    }

    @Test
    void planStyleSnakeCaseBodyIsAccepted() throws Exception {
        when(service.create(USER, "2026Q3")).thenReturn(new Created(true, progress(9, "RUNNING", 0, 20)));
        mvc.perform(post("/api/ingest-batches")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"report_period\":\"2026Q3\"}"))
                .andExpect(status().isAccepted());
    }

    @Test
    void resubmittingAnActivePeriodReturns200AndTheExistingBatch() throws Exception {
        when(service.create(USER, "2026Q3")).thenReturn(new Created(false, progress(9, "RUNNING", 5, 15)));
        mvc.perform(post("/api/ingest-batches")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportPeriod\":\"2026Q3\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.created").value(false))
                .andExpect(jsonPath("$.data.batch.batchId").value(9))
                .andExpect(jsonPath("$.data.batch.succeeded").value(5));
    }

    @Test
    void invalidPeriodIsRejectedBeforeReachingTheService() throws Exception {
        for (String body : List.of(
                "{\"reportPeriod\":\"2026\"}",
                "{\"reportPeriod\":\"2026Q5\"}",
                "{\"reportPeriod\":\"\"}",
                "{}",
                "{broken")) {
            mvc.perform(post("/api/ingest-batches").with(asUser()).contentType(MediaType.APPLICATION_JSON).content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value(40000));
        }
        verify(service, never()).create(anyLong(), eq("2026Q5"));
    }

    @Test
    void periodWithoutDocumentsIs404() throws Exception {
        when(service.create(USER, "2030Q1")).thenThrow(new BizException(ErrorCode.NOT_FOUND, "清单里没有该报告期"));
        mvc.perform(post("/api/ingest-batches")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reportPeriod\":\"2030Q1\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value(40400));
    }

    @Test
    void progressEndpointShowsCountsFailuresAndOptionalTasks() throws Exception {
        when(service.progress(9, false)).thenReturn(progress(9, "RUNNING", 12, 7));
        mvc.perform(get("/api/ingest-batches/9").with(asUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.total").value(20))
                .andExpect(jsonPath("$.data.succeeded").value(12))
                .andExpect(jsonPath("$.data.failed").value(1))
                .andExpect(jsonPath("$.data.processing").value(7))
                .andExpect(jsonPath("$.data.failures[0].docId").value("110022_quarterly_report_2026Q3"))
                .andExpect(jsonPath("$.data.tasks").doesNotExist());
        verify(service).progress(9, false);

        when(service.progress(9, true)).thenReturn(progress(9, "RUNNING", 12, 7));
        mvc.perform(get("/api/ingest-batches/9?tasks=true").with(asUser())).andExpect(status().isOk());
        verify(service).progress(9, true);
    }

    @Test
    void unknownBatchIs404AndListPassesTheLimit() throws Exception {
        when(service.progress(404, false)).thenThrow(new BizException(ErrorCode.NOT_FOUND, "批次不存在"));
        mvc.perform(get("/api/ingest-batches/404").with(asUser())).andExpect(status().isNotFound());

        when(service.recent(3)).thenReturn(List.of(progress(9, "COMPLETED", 20, 0)));
        mvc.perform(get("/api/ingest-batches?limit=3").with(asUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data[0].batchId").value(9));
    }
}
