package com.fundagent.backend.document;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fundagent.backend.aiclient.AiServiceClient.AiServiceException;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.UploadProperties;
import com.fundagent.backend.document.dto.IngestCallback;
import com.fundagent.backend.document.mapper.DocumentMapper;
import com.fundagent.backend.document.service.DocumentService;
import com.fundagent.backend.kb.KnowledgeBase;
import com.fundagent.backend.kb.service.KbService;
import com.fundagent.backend.testsupport.FakeAiServiceClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.RejectedExecutionException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.util.unit.DataSize;

class DocumentServiceTest {

    private static final long ALICE = 7;
    private static final long KB = 11;

    private final DocumentMapper documents = mock(DocumentMapper.class);
    private final KbService kbs = mock(KbService.class);
    private final FakeAiServiceClient ai = new FakeAiServiceClient();
    private UploadStorage storage;
    private DocumentService service;
    private UploadProperties props;

    @TempDir
    Path tmp;

    private static final byte[] PDF = "%PDF-1.7\nfake body\n".getBytes(StandardCharsets.US_ASCII);

    @BeforeEach
    void setUp() {
        props = new UploadProperties(tmp.toString(), DataSize.ofMegabytes(20), Duration.ofMinutes(15));
        storage = new UploadStorage(props);
        service = new DocumentService(documents, kbs, new UploadValidator(props), storage, ai, Runnable::run, props);
        KnowledgeBase kb = new KnowledgeBase();
        kb.setId(KB);
        kb.setOwnerId(ALICE);
        kb.setKbType(KnowledgeBase.TYPE_PRIVATE);
        when(kbs.requireOwnedPrivate(ALICE, KB)).thenReturn(kb);
        when(kbs.requireAccessible(ALICE, KB)).thenReturn(kb);
        // 条件更新默认命中 1 行
        when(documents.transition(anyLong(), any(), any(), any(), any(), any())).thenReturn(1);
        doAnswer(inv -> {
                    ((Document) inv.getArgument(0)).setId(100L);
                    return 1;
                })
                .when(documents)
                .insert(any(Document.class));
    }

    private static Document doc(long id, String status) {
        Document d = new Document();
        d.setId(id);
        d.setKbId(KB);
        d.setOwnerId(ALICE);
        d.setStatus(status);
        d.setAiDocId("u7-k11-abcdef0123456789");
        d.setStoragePath("nowhere");
        return d;
    }

    @Test
    void uploadStoresFileInsertsPendingAndSubmitsIngest() {
        Document d = service.upload(ALICE, KB, "我的研报.pdf", PDF);
        assertThat(d.getStatus()).isEqualTo("PENDING");
        assertThat(d.getFilename()).isEqualTo("我的研报.pdf");
        assertThat(d.getSha256()).hasSize(64);
        assertThat(d.getAiDocId()).isEqualTo("u7-k11-" + d.getSha256().substring(0, 16));
        assertThat(Path.of(d.getStoragePath())).exists().startsWith(tmp).hasBinaryContent(PDF);
        // PENDING → PROCESSING（条件更新），再向 ai-service 提交 202 风格的异步入库
        verify(documents).transition(100L, "PENDING", "PROCESSING", null, null, null);
        assertThat(ai.ingests).hasSize(1);
        var cmd = ai.ingests.get(0);
        assertThat(cmd.docId()).isEqualTo(d.getAiDocId());
        assertThat(cmd.kbId()).isEqualTo("11");
        assertThat(cmd.ownerId()).isEqualTo("7");
        assertThat(cmd.docTitle()).isEqualTo("我的研报.pdf");
        assertThat(cmd.callback()).isTrue();
        assertThat(Path.of(cmd.filePath())).isAbsolute().exists();
    }

    @Test
    void uploadRejectsDuplicateContentInTheSameKb() {
        when(documents.selectOne(any())).thenReturn(doc(55, "READY"));
        BizException e = catchBiz(() -> service.upload(ALICE, KB, "again.pdf", PDF));
        assertThat(e.errorCode()).isEqualTo(ErrorCode.CONFLICT);
        assertThat(e.getMessage()).contains("55");
        verify(documents, never()).insert(any(Document.class));
        assertThat(ai.ingests).isEmpty();
    }

    @Test
    void concurrentDuplicateInsertIsAlsoConflict() {
        doThrow(new DuplicateKeyException("uk_doc_kb_sha")).when(documents).insert(any(Document.class));
        assertThat(catchBiz(() -> service.upload(ALICE, KB, "a.pdf", PDF)).errorCode()).isEqualTo(ErrorCode.CONFLICT);
        assertThat(ai.ingests).isEmpty();
    }

    @Test
    void afterAFailedIngestTheSameFileCanBeUploadedAgain() {
        when(documents.selectOne(any())).thenReturn(doc(55, "FAILED"));
        Document d = service.upload(ALICE, KB, "retry.pdf", PDF);
        verify(documents).deleteById(55L);
        assertThat(d.getStatus()).isEqualTo("PENDING");
        assertThat(ai.ingests).hasSize(1);
    }

    @Test
    void invalidContentNeverReachesStorageOrDatabase() {
        assertThat(catchBiz(() -> service.upload(ALICE, KB, "x.pdf", "not a pdf".getBytes(StandardCharsets.UTF_8))).errorCode())
                .isEqualTo(ErrorCode.UNSUPPORTED_MEDIA_TYPE);
        verify(documents, never()).insert(any(Document.class));
        assertThat(tmp.toFile().list()).isEmpty();
    }

    @Test
    void uploadingToAnotherUsersOrThePublicKbIsForbidden() {
        when(kbs.requireOwnedPrivate(ALICE, 22L)).thenThrow(new BizException(ErrorCode.FORBIDDEN, "无权访问该知识库"));
        assertThat(catchBiz(() -> service.upload(ALICE, 22L, "a.pdf", PDF)).errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
        verify(documents, never()).insert(any(Document.class));
    }

    @Test
    void submitFailureMarksTheDocumentFailed() {
        ai.ingestFailure = new AiServiceException("ai-service 不可达");
        service.upload(ALICE, KB, "a.pdf", PDF);
        verify(documents).transition(eq(100L), eq("PROCESSING"), eq("FAILED"), any(String.class), isNull(), isNull());
    }

    @Test
    void fullQueueMarksTheDocumentFailedImmediately() {
        DocumentService rejecting = new DocumentService(documents, kbs, new UploadValidator(props), storage, ai, r -> {
            throw new RejectedExecutionException("full");
        }, props);
        rejecting.upload(ALICE, KB, "a.pdf", PDF);
        verify(documents).transition(eq(100L), eq("PENDING"), eq("FAILED"), any(String.class), isNull(), isNull());
        assertThat(ai.ingests).isEmpty();
    }

    @Test
    void submitIsSkippedWhenTheDocumentAlreadyLeftPending() {
        when(documents.transition(anyLong(), eq("PENDING"), eq("PROCESSING"), any(), any(), any())).thenReturn(0);
        service.upload(ALICE, KB, "a.pdf", PDF);
        assertThat(ai.ingests).isEmpty();
    }

    // ------------------------------------------------------------------ 回调

    @Test
    void readyCallbackMovesProcessingToReadyWithCounts() {
        when(documents.selectOne(any())).thenReturn(doc(5, "PROCESSING"));
        service.onIngestResult(new IngestCallback("u7-k11-abcdef0123456789", "READY", 13, 57, null));
        verify(documents).transition(5L, "PROCESSING", "READY", null, 13, 57);
    }

    @Test
    void failedCallbackStoresTheErrorTruncated() {
        when(documents.selectOne(any())).thenReturn(doc(5, "PROCESSING"));
        service.onIngestResult(new IngestCallback("u7-k11-abcdef0123456789", "FAILED", 0, 0, "x".repeat(2000)));
        org.mockito.ArgumentCaptor<String> err = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(documents).transition(eq(5L), eq("PROCESSING"), eq("FAILED"), err.capture(), any(), any());
        assertThat(err.getValue()).hasSize(DocumentService.ERROR_MAX);
    }

    @Test
    void duplicateOrLateOrUnknownCallbacksAreIgnoredWithoutError() {
        // 未知文档
        when(documents.selectOne(any())).thenReturn(null);
        service.onIngestResult(new IngestCallback("nope", "READY", 1, 1, null));
        verify(documents, never()).transition(anyLong(), any(), any(), any(), any(), any());
        // 已经是终态：条件更新命中 0 行，静默忽略
        when(documents.selectOne(any())).thenReturn(doc(5, "READY"));
        when(documents.transition(anyLong(), any(), any(), any(), any(), any())).thenReturn(0);
        service.onIngestResult(new IngestCallback("u7-k11-abcdef0123456789", "READY", 1, 1, null));
        service.onIngestResult(new IngestCallback("u7-k11-abcdef0123456789", "FAILED", 0, 0, "late"));
        verify(documents, never()).updateById(any(Document.class));
    }

    // ------------------------------------------------------------------ 超时清理

    @Test
    void staleDocumentsAreFailedWithAReadableReason() {
        when(documents.findStale(anyInt())).thenReturn(List.of(doc(1, "PROCESSING"), doc(2, "PENDING")));
        assertThat(service.failStale()).isEqualTo(2);
        verify(documents).findStale(15 * 60);
        verify(documents).transition(eq(1L), eq("PROCESSING"), eq("FAILED"), any(String.class), isNull(), isNull());
        verify(documents).transition(eq(2L), eq("PENDING"), eq("FAILED"), any(String.class), isNull(), isNull());
    }

    @Test
    void staleSweepCountsOnlyRowsActuallyChanged() {
        when(documents.findStale(anyInt())).thenReturn(List.of(doc(1, "PROCESSING")));
        when(documents.transition(anyLong(), any(), any(), any(), any(), any())).thenReturn(0); // 回调刚好赶上了
        assertThat(service.failStale()).isZero();
    }

    // ------------------------------------------------------------------ 查询与删除

    @Test
    void listReturnsEmptyForPublicKbAndOwnDocumentsOtherwise() {
        KnowledgeBase pub = new KnowledgeBase();
        pub.setId(1L);
        pub.setKbType(KnowledgeBase.TYPE_PUBLIC);
        when(kbs.requireAccessible(ALICE, 1L)).thenReturn(pub);
        assertThat(service.list(ALICE, 1L)).isEmpty();
        verify(documents, never()).selectList(any());
        when(documents.selectList(any())).thenReturn(List.of(doc(9, "READY")));
        assertThat(service.list(ALICE, KB)).hasSize(1);
    }

    @Test
    void getHidesOthersDocuments() {
        Document bobs = doc(9, "READY");
        bobs.setOwnerId(8L);
        when(documents.selectById(9L)).thenReturn(bobs);
        assertThat(catchBiz(() -> service.get(ALICE, 9L)).errorCode()).isEqualTo(ErrorCode.NOT_FOUND);
        assertThat(catchBiz(() -> service.get(ALICE, 404L)).errorCode()).isEqualTo(ErrorCode.NOT_FOUND);
    }

    @Test
    void deleteRemovesIndexRecordAndFile() throws Exception {
        Path f = Files.write(tmp.resolve("doc.pdf"), PDF);
        Document d = doc(9, "READY");
        d.setStoragePath(f.toString());
        when(documents.selectById(9L)).thenReturn(d);
        service.delete(ALICE, 9L);
        assertThat(ai.deletes.get(0)).containsExactly("u7-k11-abcdef0123456789", "11");
        verify(documents).deleteById(9L);
        assertThat(f).doesNotExist();
    }

    @Test
    void deleteIsRefusedWhileIngestingOrWhenIndexDeletionFails() {
        when(documents.selectById(9L)).thenReturn(doc(9, "PROCESSING"));
        assertThat(catchBiz(() -> service.delete(ALICE, 9L)).errorCode()).isEqualTo(ErrorCode.CONFLICT);
        when(documents.selectById(9L)).thenReturn(doc(9, "READY"));
        ai.deleteFailure = new AiServiceException("down");
        assertThat(catchBiz(() -> service.delete(ALICE, 9L)).errorCode()).isEqualTo(ErrorCode.BAD_GATEWAY);
        verify(documents, never()).deleteById(anyLong());
    }

    @Test
    void storageOnlyDeletesInsideItsRoot(@TempDir Path elsewhere) throws Exception {
        Path outside = Files.writeString(elsewhere.resolve("keep.txt"), "keep");
        storage.delete(outside.toString());
        assertThat(outside).exists();
        storage.delete("\0invalid"); // 不抛异常
    }

    private static BizException catchBiz(Runnable r) {
        try {
            r.run();
        } catch (BizException e) {
            return e;
        }
        throw new AssertionError("expected BizException");
    }
}
