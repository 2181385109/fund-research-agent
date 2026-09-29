package com.fundagent.backend.document;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.common.ApiResponse;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.document.dto.DocumentView;
import com.fundagent.backend.document.service.DocumentService;
import io.swagger.v3.oas.annotations.Operation;
import java.io.IOException;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api")
public class DocumentController {

    private final DocumentService documents;

    public DocumentController(DocumentService documents) {
        this.documents = documents;
    }

    @Operation(
            summary = "上传文档到私有知识库",
            description = "PDF / Markdown / TXT，单个不超过 20MB；校验魔数，按 sha256 去重。返回 202：入库在后台进行，"
                    + "轮询文档状态 PENDING → PROCESSING → READY / FAILED")
    @PostMapping(value = "/kbs/{kbId}/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.ACCEPTED)
    public ApiResponse<DocumentView> upload(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId,
            @PathVariable long kbId,
            @RequestPart("file") MultipartFile file) {
        byte[] bytes;
        try {
            bytes = file.getBytes();
        } catch (IOException e) {
            throw new BizException(ErrorCode.BAD_REQUEST, "读取上传文件失败");
        }
        return ApiResponse.ok(DocumentView.of(documents.upload(userId, kbId, file.getOriginalFilename(), bytes)));
    }

    @Operation(summary = "知识库中的文档", description = "公共库返回空列表（其文档由数据管线离线入库）")
    @GetMapping("/kbs/{kbId}/documents")
    public ApiResponse<List<DocumentView>> list(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @PathVariable long kbId) {
        return ApiResponse.ok(documents.list(userId, kbId).stream().map(DocumentView::of).toList());
    }

    @Operation(summary = "文档详情 / 入库状态")
    @GetMapping("/documents/{docId}")
    public ApiResponse<DocumentView> get(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @PathVariable long docId) {
        return ApiResponse.ok(DocumentView.of(documents.get(userId, docId)));
    }

    @Operation(summary = "删除文档", description = "同时删除其向量 / 关键词索引；入库中返回 409")
    @DeleteMapping("/documents/{docId}")
    public ApiResponse<Void> delete(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @PathVariable long docId) {
        documents.delete(userId, docId);
        return ApiResponse.ok(null);
    }
}
