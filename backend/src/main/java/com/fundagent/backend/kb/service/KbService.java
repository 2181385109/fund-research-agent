package com.fundagent.backend.kb.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fundagent.backend.aiclient.AiServiceClient;
import com.fundagent.backend.aiclient.AiServiceClient.AiServiceException;
import com.fundagent.backend.aiclient.AiServiceClient.KbScope;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.document.Document;
import com.fundagent.backend.document.DocumentStatus;
import com.fundagent.backend.document.UploadStorage;
import com.fundagent.backend.document.mapper.DocumentMapper;
import com.fundagent.backend.kb.KnowledgeBase;
import com.fundagent.backend.kb.dto.KbDtos.ResolvedScope;
import com.fundagent.backend.kb.mapper.KnowledgeBaseMapper;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * 知识库：公共库（id=1，所有人只读）+ 用户私有库；并负责「这个用户这次提问允许检索哪些库」的判定（ADR-043）——
 * 检索范围只在这里算出，客户端只能在允许集合内缩小，请求里带了不允许的 kb_id 整个请求被拒。
 */
@Service
public class KbService {

    private static final Logger log = LoggerFactory.getLogger(KbService.class);
    static final int MAX_KBS_PER_USER = 20;

    private final KnowledgeBaseMapper kbs;
    private final DocumentMapper documents;
    private final AiServiceClient ai;
    private final UploadStorage storage;

    public KbService(KnowledgeBaseMapper kbs, DocumentMapper documents, AiServiceClient ai, UploadStorage storage) {
        this.kbs = kbs;
        this.documents = documents;
        this.ai = ai;
        this.storage = storage;
    }

    /** 该用户可见的库：公共库 + 自己的私有库。 */
    public List<KnowledgeBase> listAccessible(long userId) {
        return kbs.selectList(new LambdaQueryWrapper<KnowledgeBase>()
                .eq(KnowledgeBase::getKbType, KnowledgeBase.TYPE_PUBLIC)
                .or()
                .eq(KnowledgeBase::getOwnerId, userId)
                .orderByAsc(KnowledgeBase::getId));
    }

    public KnowledgeBase create(long userId, String name) {
        long owned = kbs.selectCount(new LambdaQueryWrapper<KnowledgeBase>().eq(KnowledgeBase::getOwnerId, userId));
        if (owned >= MAX_KBS_PER_USER) {
            throw new BizException(ErrorCode.BAD_REQUEST, "私有知识库数量已达上限（" + MAX_KBS_PER_USER + "）");
        }
        KnowledgeBase kb = new KnowledgeBase();
        kb.setOwnerId(userId);
        kb.setName(name.strip());
        kb.setKbType(KnowledgeBase.TYPE_PRIVATE);
        kbs.insert(kb);
        return kb;
    }

    /** 可读取的库（公共库或自己的）；不存在与「是别人的」统一返回 403，避免探测他人知识库 id。 */
    public KnowledgeBase requireAccessible(long userId, long kbId) {
        KnowledgeBase kb = kbs.selectById(kbId);
        if (kb == null || !(kb.isPublic() || (kb.getOwnerId() != null && kb.getOwnerId() == userId))) {
            throw new BizException(ErrorCode.FORBIDDEN, "无权访问该知识库");
        }
        return kb;
    }

    /** 可写的库：只有自己的私有库；公共库只读。 */
    public KnowledgeBase requireOwnedPrivate(long userId, long kbId) {
        KnowledgeBase kb = requireAccessible(userId, kbId);
        if (kb.isPublic()) {
            throw new BizException(ErrorCode.FORBIDDEN, "公共知识库只读");
        }
        return kb;
    }

    /** 删除私有库：先删 ai-service 里的全部文档切块，再删文件和记录。有文档正在入库时拒绝。 */
    public void delete(long userId, long kbId) {
        KnowledgeBase kb = requireOwnedPrivate(userId, kbId);
        List<Document> docs = documents.selectList(new LambdaQueryWrapper<Document>().eq(Document::getKbId, kb.getId()));
        for (Document d : docs) {
            DocumentStatus st = d.statusEnum();
            if (st == DocumentStatus.PENDING || st == DocumentStatus.PROCESSING) {
                throw new BizException(ErrorCode.CONFLICT, "知识库中有文档正在入库，请稍后再删除");
            }
        }
        for (Document d : docs) {
            try {
                ai.deleteDocument(d.getAiDocId(), Long.toString(kb.getId()));
            } catch (AiServiceException e) {
                throw new BizException(ErrorCode.BAD_GATEWAY, "删除文档索引失败，知识库未删除：" + e.getMessage());
            }
        }
        docs.forEach(d -> storage.delete(d.getStoragePath()));
        kbs.deleteById(kb.getId()); // documents 由外键级联删除
    }

    /**
     * 算出本次提问的检索范围。
     *
     * @param requested 客户端选择的库；为空 = 该用户全部可访问的库（公共库 + 自己的私有库）
     * @throws BizException 403：{@code requested} 里有任何一个不在允许集合内（含别人的库、不存在的库）
     */
    public ResolvedScope resolveScope(long userId, List<Long> requested) {
        Set<Long> allowed = new LinkedHashSet<>();
        listAccessible(userId).forEach(kb -> allowed.add(kb.getId()));
        Set<Long> effective;
        if (requested == null || requested.isEmpty()) {
            effective = allowed;
        } else {
            effective = new LinkedHashSet<>(requested);
            for (Long id : effective) {
                if (!allowed.contains(id)) {
                    log.warn("scope denied user={} requestedKb={}", userId, id);
                    throw new BizException(ErrorCode.FORBIDDEN, "无权访问所选的知识库");
                }
            }
        }
        boolean includePublic = effective.contains(KnowledgeBase.PUBLIC_KB_ID);
        List<String> privateIds = new ArrayList<>();
        for (Long id : effective) {
            if (id != KnowledgeBase.PUBLIC_KB_ID) {
                privateIds.add(Long.toString(id));
            }
        }
        return new ResolvedScope(List.copyOf(effective), new KbScope(includePublic, Long.toString(userId), privateIds));
    }
}
