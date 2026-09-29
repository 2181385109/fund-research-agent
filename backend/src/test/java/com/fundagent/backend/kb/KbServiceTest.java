package com.fundagent.backend.kb;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fundagent.backend.aiclient.AiServiceClient.AiServiceException;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.UploadProperties;
import com.fundagent.backend.document.Document;
import com.fundagent.backend.document.DocumentStatus;
import com.fundagent.backend.document.UploadStorage;
import com.fundagent.backend.document.mapper.DocumentMapper;
import com.fundagent.backend.kb.dto.KbDtos.ResolvedScope;
import com.fundagent.backend.kb.mapper.KnowledgeBaseMapper;
import com.fundagent.backend.kb.service.KbService;
import com.fundagent.backend.testsupport.FakeAiServiceClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.util.unit.DataSize;

class KbServiceTest {

    private static final long ALICE = 7;
    private static final long BOB = 8;

    private final KnowledgeBaseMapper kbs = mock(KnowledgeBaseMapper.class);
    private final DocumentMapper documents = mock(DocumentMapper.class);
    private final FakeAiServiceClient ai = new FakeAiServiceClient();
    private UploadStorage storage;
    private KbService service;

    @TempDir
    Path tmp;

    @BeforeEach
    void setUp() {
        storage = new UploadStorage(new UploadProperties(tmp.toString(), DataSize.ofMegabytes(20), Duration.ofMinutes(15)));
        service = new KbService(kbs, documents, ai, storage);
    }

    private static KnowledgeBase kb(long id, Long owner, String type) {
        KnowledgeBase k = new KnowledgeBase();
        k.setId(id);
        k.setOwnerId(owner);
        k.setName("kb" + id);
        k.setKbType(type);
        return k;
    }

    private void visibleToAlice() {
        // 公共库 + Alice 自己的两个私有库（11、12）；Bob 的库 22 不在返回里
        when(kbs.selectList(any()))
                .thenReturn(List.of(
                        kb(1, null, KnowledgeBase.TYPE_PUBLIC),
                        kb(11, ALICE, KnowledgeBase.TYPE_PRIVATE),
                        kb(12, ALICE, KnowledgeBase.TYPE_PRIVATE)));
    }

    @Test
    void defaultScopeIsPublicPlusAllOwnPrivateKbs() {
        visibleToAlice();
        ResolvedScope s = service.resolveScope(ALICE, null);
        assertThat(s.kbIds()).containsExactly(1L, 11L, 12L);
        assertThat(s.aiScope().includePublic()).isTrue();
        assertThat(s.aiScope().ownerId()).isEqualTo("7");
        assertThat(s.aiScope().privateKbIds()).containsExactly("11", "12");
        assertThat(service.resolveScope(ALICE, List.of()).kbIds()).containsExactly(1L, 11L, 12L);
    }

    @Test
    void clientCanNarrowTheScopeWithinWhatIsAllowed() {
        visibleToAlice();
        ResolvedScope onlyPrivate = service.resolveScope(ALICE, List.of(11L));
        assertThat(onlyPrivate.aiScope().includePublic()).isFalse();
        assertThat(onlyPrivate.aiScope().privateKbIds()).containsExactly("11");
        ResolvedScope onlyPublic = service.resolveScope(ALICE, List.of(1L, 1L));
        assertThat(onlyPublic.aiScope().includePublic()).isTrue();
        assertThat(onlyPublic.aiScope().privateKbIds()).isEmpty();
        assertThat(onlyPublic.kbIds()).containsExactly(1L);
    }

    @Test
    void anotherUsersOrNonexistentKbIsRejectedForTheWholeRequest() {
        visibleToAlice();
        // Alice 手动带上 Bob 的库 22（不在她的允许集合里）
        for (List<Long> requested : List.of(List.of(22L), List.of(11L, 22L), List.of(1L, 11L, 999L))) {
            assertThatThrownBy(() -> service.resolveScope(ALICE, requested))
                    .isInstanceOfSatisfying(BizException.class, e -> {
                        assertThat(e.errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
                        assertThat(e.getMessage()).doesNotContain("22").doesNotContain("999");
                    });
        }
    }

    @Test
    void requireAccessibleTreatsForeignAndMissingKbsTheSame() {
        when(kbs.selectById(22L)).thenReturn(kb(22, BOB, KnowledgeBase.TYPE_PRIVATE));
        when(kbs.selectById(404L)).thenReturn(null);
        when(kbs.selectById(1L)).thenReturn(kb(1, null, KnowledgeBase.TYPE_PUBLIC));
        when(kbs.selectById(11L)).thenReturn(kb(11, ALICE, KnowledgeBase.TYPE_PRIVATE));
        BizException foreign = catchBiz(() -> service.requireAccessible(ALICE, 22L));
        BizException missing = catchBiz(() -> service.requireAccessible(ALICE, 404L));
        assertThat(foreign.errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
        assertThat(missing.errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
        assertThat(foreign.getMessage()).isEqualTo(missing.getMessage());
        assertThat(service.requireAccessible(ALICE, 1L).isPublic()).isTrue();
        assertThat(service.requireAccessible(ALICE, 11L).getId()).isEqualTo(11L);
    }

    @Test
    void publicKbIsReadOnly() {
        when(kbs.selectById(1L)).thenReturn(kb(1, null, KnowledgeBase.TYPE_PUBLIC));
        BizException e = catchBiz(() -> service.requireOwnedPrivate(ALICE, 1L));
        assertThat(e.errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
        assertThat(e.getMessage()).contains("只读");
        BizException del = catchBiz(() -> service.delete(ALICE, 1L));
        assertThat(del.errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
    }

    @Test
    void createTrimsNameAndEnforcesPerUserLimit() {
        when(kbs.selectCount(any())).thenReturn(0L);
        KnowledgeBase created = service.create(ALICE, "  我的研究笔记  ");
        assertThat(created.getName()).isEqualTo("我的研究笔记");
        assertThat(created.getKbType()).isEqualTo(KnowledgeBase.TYPE_PRIVATE);
        assertThat(created.getOwnerId()).isEqualTo(ALICE);
        verify(kbs).insert(created);
        when(kbs.selectCount(any())).thenReturn(20L);
        assertThat(catchBiz(() -> service.create(ALICE, "第 21 个").getClass()).errorCode()).isEqualTo(ErrorCode.BAD_REQUEST);
    }

    private Document doc(long id, String status, Path file) {
        Document d = new Document();
        d.setId(id);
        d.setKbId(11L);
        d.setOwnerId(ALICE);
        d.setStatus(status);
        d.setAiDocId("u7-k11-" + id);
        d.setStoragePath(file.toString());
        return d;
    }

    @Test
    void deleteRemovesIndexFilesAndRecord() throws Exception {
        when(kbs.selectById(11L)).thenReturn(kb(11, ALICE, KnowledgeBase.TYPE_PRIVATE));
        Path f1 = Files.writeString(tmp.resolve("a.txt"), "a");
        when(documents.selectList(any())).thenReturn(List.of(doc(1, "READY", f1)));
        service.delete(ALICE, 11L);
        assertThat(ai.deletes).hasSize(1);
        assertThat(ai.deletes.get(0)).containsExactly("u7-k11-1", "11");
        assertThat(f1).doesNotExist();
        verify(kbs).deleteById(11L);
    }

    @Test
    void deleteIsRefusedWhileDocumentsAreIngestingOrIndexDeletionFails() throws Exception {
        when(kbs.selectById(11L)).thenReturn(kb(11, ALICE, KnowledgeBase.TYPE_PRIVATE));
        Path f = Files.writeString(tmp.resolve("b.txt"), "b");
        when(documents.selectList(any())).thenReturn(List.of(doc(1, DocumentStatus.PROCESSING.name(), f)));
        assertThat(catchBiz(() -> service.delete(ALICE, 11L)).errorCode()).isEqualTo(ErrorCode.CONFLICT);
        verify(kbs, never()).deleteById(anyLong());

        when(documents.selectList(any())).thenReturn(List.of(doc(2, "READY", f)));
        ai.deleteFailure = new AiServiceException("down");
        assertThat(catchBiz(() -> service.delete(ALICE, 11L)).errorCode()).isEqualTo(ErrorCode.BAD_GATEWAY);
        assertThat(f).exists();
        verify(kbs, never()).deleteById(anyLong());
    }

    @Test
    void cannotDeleteSomeoneElsesKb() {
        when(kbs.selectById(22L)).thenReturn(kb(22, BOB, KnowledgeBase.TYPE_PRIVATE));
        assertThat(catchBiz(() -> service.delete(ALICE, 22L)).errorCode()).isEqualTo(ErrorCode.FORBIDDEN);
        verify(kbs, never()).deleteById(anyLong());
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
