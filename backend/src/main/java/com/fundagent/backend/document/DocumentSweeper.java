package com.fundagent.backend.document;

import com.fundagent.backend.document.service.DocumentService;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** 定时把「太久没收到入库回调」的文档置为 FAILED（回调丢了、ai-service 重启了）。 */
@Component
public class DocumentSweeper {

    private final DocumentService documents;

    public DocumentSweeper(DocumentService documents) {
        this.documents = documents;
    }

    @Scheduled(fixedDelayString = "${fra.upload.sweep-interval:60s}", initialDelayString = "${fra.upload.sweep-interval:60s}")
    public void sweep() {
        documents.failStale();
    }
}
