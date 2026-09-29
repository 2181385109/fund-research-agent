package com.fundagent.backend.document.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fundagent.backend.aiclient.AiServiceClient;
import com.fundagent.backend.aiclient.AiServiceClient.AiServiceException;
import com.fundagent.backend.aiclient.AiServiceClient.IngestCommand;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.UploadProperties;
import com.fundagent.backend.document.Document;
import com.fundagent.backend.document.DocumentStatus;
import com.fundagent.backend.document.UploadStorage;
import com.fundagent.backend.document.UploadValidator;
import com.fundagent.backend.document.UploadValidator.Checked;
import com.fundagent.backend.document.dto.IngestCallback;
import com.fundagent.backend.document.mapper.DocumentMapper;
import com.fundagent.backend.kb.KnowledgeBase;
import com.fundagent.backend.kb.service.KbService;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * 文档：上传（校验、sha256 去重、落盘）→ 异步提交 ai-service 入库 → 由 ai-service 带共享密钥回调更新状态。
 * 状态机 PENDING → PROCESSING → READY / FAILED；每次迁移都是条件更新（{@code WHERE status = 当前状态}），
 * 所以重复回调、迟到回调、与超时清理并发都不会把终态改回去。
 */
@Service
public class DocumentService {

    private static final Logger log = LoggerFactory.getLogger(DocumentService.class);
    public static final int ERROR_MAX = 480;

    private final DocumentMapper documents;
    private final KbService kbs;
    private final UploadValidator validator;
    private final UploadStorage storage;
    private final AiServiceClient ai;
    private final Executor ingestExecutor;
    private final UploadProperties props;

    public DocumentService(
            DocumentMapper documents,
            KbService kbs,
            UploadValidator validator,
            UploadStorage storage,
            AiServiceClient ai,
            @Qualifier("ingestExecutor") Executor ingestExecutor,
            UploadProperties props) {
        this.documents = documents;
        this.kbs = kbs;
        this.validator = validator;
        this.storage = storage;
        this.ai = ai;
        this.ingestExecutor = ingestExecutor;
        this.props = props;
    }

    // ------------------------------------------------------------------ 上传

    public Document upload(long userId, long kbId, String originalFilename, byte[] content) {
        KnowledgeBase kb = kbs.requireOwnedPrivate(userId, kbId);
        Checked checked = validator.check(originalFilename, content);
        String sha = sha256(content);

        Document existing = documents.selectOne(
                new LambdaQueryWrapper<Document>().eq(Document::getKbId, kb.getId()).eq(Document::getSha256, sha));
        if (existing != null) {
            if (existing.statusEnum() != DocumentStatus.FAILED) {
                throw new BizException(ErrorCode.CONFLICT, "该文件已上传过（文档 id " + existing.getId() + "）");
            }
            documents.deleteById(existing.getId()); // 上次入库失败：允许重新上传同一个文件
        }

        Path stored = storage.store(userId, kb.getId(), sha, checked.ext(), content);
        Document d = new Document();
        d.setKbId(kb.getId());
        d.setOwnerId(userId);
        d.setFilename(checked.filename());
        d.setExt(checked.ext());
        d.setSizeBytes((long) content.length);
        d.setSha256(sha);
        d.setStoragePath(stored.toString());
        d.setAiDocId("u%d-k%d-%s".formatted(userId, kb.getId(), sha.substring(0, 16)));
        d.setStatus(DocumentStatus.PENDING.name());
        try {
            documents.insert(d);
        } catch (DuplicateKeyException e) { // 并发上传同一个文件
            throw new BizException(ErrorCode.CONFLICT, "该文件已上传过");
        }
        dispatch(d);
        return d;
    }

    /** PENDING → PROCESSING，再提交 ai-service（202 立即返回）。提交失败 → FAILED。 */
    private void dispatch(Document d) {
        try {
            ingestExecutor.execute(() -> submit(d));
        } catch (RejectedExecutionException e) {
            move(d.getId(), DocumentStatus.PENDING, DocumentStatus.FAILED, "入库队列已满，请稍后重新上传", null, null);
        }
    }

    void submit(Document d) {
        if (!move(d.getId(), DocumentStatus.PENDING, DocumentStatus.PROCESSING, null, null, null)) {
            return; // 已被清理任务或删除动作改走
        }
        try {
            ai.submitIngest(new IngestCommand(
                    d.getAiDocId(),
                    Path.of(d.getStoragePath()).toAbsolutePath().normalize().toString(),
                    Long.toString(d.getKbId()),
                    Long.toString(d.getOwnerId()),
                    d.getFilename(),
                    true));
        } catch (RuntimeException e) {
            log.warn("submit ingest failed doc={} : {}", d.getId(), e.toString());
            move(d.getId(), DocumentStatus.PROCESSING, DocumentStatus.FAILED, "提交入库失败：" + e.getMessage(), null, null);
        }
    }

    // ------------------------------------------------------------------ ai-service 回调

    /** 回调幂等：未知文档、状态不在 PROCESSING（重复 / 迟到 / 已被超时清理）都只记日志，不报错，免得 ai-service 重试。 */
    public void onIngestResult(IngestCallback cb) {
        Document d = documents.selectOne(new LambdaQueryWrapper<Document>().eq(Document::getAiDocId, cb.docId()));
        if (d == null) {
            log.warn("ingest callback for unknown doc {}", cb.docId());
            return;
        }
        DocumentStatus target = DocumentStatus.valueOf(cb.status());
        boolean ok = move(
                d.getId(),
                DocumentStatus.PROCESSING,
                target,
                target == DocumentStatus.FAILED ? truncate(cb.error() == null ? "入库失败" : cb.error()) : null,
                cb.pages(),
                cb.chunks());
        if (!ok) {
            log.info("ingest callback ignored doc={} status={} (current={})", d.getId(), cb.status(), d.getStatus());
        }
    }

    /** 长时间停留在 PENDING / PROCESSING 的文档（回调丢了、ai-service 重启了）→ FAILED。返回处理条数。 */
    public int failStale() {
        List<Document> stale = documents.findStale(Math.toIntExact(props.staleAfter().toSeconds()));
        int n = 0;
        for (Document d : stale) {
            String msg = "超过 " + props.staleAfter().toMinutes() + " 分钟没有收到入库结果，请重新上传";
            if (move(d.getId(), d.statusEnum(), DocumentStatus.FAILED, msg, null, null)) {
                n++;
            }
        }
        if (n > 0) {
            log.warn("marked {} stale documents as FAILED", n);
        }
        return n;
    }

    // ------------------------------------------------------------------ 查询 / 删除

    public List<Document> list(long userId, long kbId) {
        KnowledgeBase kb = kbs.requireAccessible(userId, kbId);
        if (kb.isPublic()) {
            return List.of(); // 公共库的文档由 data-pipeline 直接入库，不在业务库里
        }
        return documents.selectList(
                new LambdaQueryWrapper<Document>().eq(Document::getKbId, kb.getId()).orderByDesc(Document::getId));
    }

    public Document get(long userId, long docId) {
        Document d = documents.selectById(docId);
        if (d == null || d.getOwnerId() != userId) {
            throw new BizException(ErrorCode.NOT_FOUND, "文档不存在");
        }
        return d;
    }

    public void delete(long userId, long docId) {
        Document d = get(userId, docId);
        DocumentStatus st = d.statusEnum();
        if (st == DocumentStatus.PENDING || st == DocumentStatus.PROCESSING) {
            throw new BizException(ErrorCode.CONFLICT, "文档正在入库，暂不能删除");
        }
        try {
            ai.deleteDocument(d.getAiDocId(), Long.toString(d.getKbId()));
        } catch (AiServiceException e) {
            throw new BizException(ErrorCode.BAD_GATEWAY, "删除文档索引失败，文档未删除：" + e.getMessage());
        }
        documents.deleteById(d.getId());
        storage.delete(d.getStoragePath());
    }

    // ------------------------------------------------------------------ 内部

    private boolean move(
            long id, DocumentStatus from, DocumentStatus to, String error, Integer pages, Integer chunks) {
        if (!from.canMoveTo(to)) {
            throw new IllegalStateException("非法的状态迁移 " + from + " → " + to);
        }
        return documents.transition(id, from.name(), to.name(), error == null ? null : truncate(error), pages, chunks) == 1;
    }

    private static String truncate(String s) {
        return s.length() > ERROR_MAX ? s.substring(0, ERROR_MAX) : s;
    }

    static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
