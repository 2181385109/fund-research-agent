package com.fundagent.backend.document;

import com.fundagent.backend.common.ApiResponse;
import com.fundagent.backend.document.dto.IngestCallback;
import com.fundagent.backend.document.service.DocumentService;
import io.swagger.v3.oas.annotations.Hidden;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** ai-service 入库完成后的回调；由 InternalAuthInterceptor 校验共享密钥头。不是给终端用户的接口。 */
@Hidden
@RestController
@RequestMapping("/internal/documents")
public class InternalDocumentController {

    private final DocumentService documents;

    public InternalDocumentController(DocumentService documents) {
        this.documents = documents;
    }

    @PostMapping("/callback")
    public ApiResponse<Void> callback(@Valid @RequestBody IngestCallback body) {
        documents.onIngestResult(body);
        return ApiResponse.ok(null);
    }
}
