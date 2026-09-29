package com.fundagent.backend.kb;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.common.ApiResponse;
import com.fundagent.backend.kb.dto.KbDtos.CreateKb;
import com.fundagent.backend.kb.dto.KbDtos.KbView;
import com.fundagent.backend.kb.service.KbService;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/kbs")
public class KbController {

    private final KbService kbs;

    public KbController(KbService kbs) {
        this.kbs = kbs;
    }

    @Operation(summary = "我可见的知识库", description = "公共库「基金披露文件」（只读）+ 自己的私有库")
    @GetMapping
    public ApiResponse<List<KbView>> list(@RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId) {
        return ApiResponse.ok(kbs.listAccessible(userId).stream().map(KbView::of).toList());
    }

    @Operation(summary = "新建私有知识库")
    @PostMapping
    public ApiResponse<KbView> create(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @Valid @RequestBody CreateKb body) {
        return ApiResponse.ok(KbView.of(kbs.create(userId, body.name())));
    }

    @Operation(summary = "删除私有知识库", description = "同时删除库内全部文档及其向量 / 关键词索引；有文档正在入库时返回 409")
    @DeleteMapping("/{kbId}")
    public ApiResponse<Void> delete(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @PathVariable long kbId) {
        kbs.delete(userId, kbId);
        return ApiResponse.ok(null);
    }
}
