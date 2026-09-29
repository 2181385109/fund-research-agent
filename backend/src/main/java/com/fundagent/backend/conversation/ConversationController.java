package com.fundagent.backend.conversation;

import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.common.ApiResponse;
import com.fundagent.backend.conversation.dto.ConversationDtos.ConversationView;
import com.fundagent.backend.conversation.dto.ConversationDtos.CreateConversation;
import com.fundagent.backend.conversation.dto.ConversationDtos.MessageView;
import com.fundagent.backend.conversation.service.ConversationService;
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
@RequestMapping("/api/conversations")
public class ConversationController {

    private final ConversationService conversations;

    public ConversationController(ConversationService conversations) {
        this.conversations = conversations;
    }

    @Operation(summary = "新建会话")
    @PostMapping
    public ApiResponse<ConversationView> create(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId,
            @Valid @RequestBody(required = false) CreateConversation body) {
        return ApiResponse.ok(ConversationView.of(conversations.create(userId, body == null ? null : body.title())));
    }

    @Operation(summary = "我的会话列表（最近更新在前）")
    @GetMapping
    public ApiResponse<List<ConversationView>> list(@RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId) {
        return ApiResponse.ok(conversations.list(userId).stream().map(ConversationView::of).toList());
    }

    @Operation(summary = "会话历史", description = "全部消息；助手消息带 citations（出处）与 disclaimer（风险提示）")
    @GetMapping("/{id}/messages")
    public ApiResponse<List<MessageView>> messages(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @PathVariable long id) {
        return ApiResponse.ok(conversations.history(userId, id));
    }

    @Operation(summary = "删除会话")
    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(
            @RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId, @PathVariable long id) {
        conversations.delete(userId, id);
        return ApiResponse.ok(null);
    }
}
