package com.fundagent.backend.web;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fundagent.backend.auth.AuthController;
import com.fundagent.backend.auth.AuthInterceptor;
import com.fundagent.backend.auth.dto.AuthDtos.AuthResult;
import com.fundagent.backend.auth.dto.AuthDtos.UserView;
import com.fundagent.backend.auth.service.AuthService;
import com.fundagent.backend.chat.ChatController;
import com.fundagent.backend.chat.ChatSession;
import com.fundagent.backend.chat.ChatSink;
import com.fundagent.backend.chat.service.ChatService;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.common.GlobalExceptionHandler;
import com.fundagent.backend.common.RequestIdFilter;
import com.fundagent.backend.config.ChatProperties;
import com.fundagent.backend.conversation.Conversation;
import com.fundagent.backend.conversation.ConversationController;
import com.fundagent.backend.conversation.dto.ConversationDtos.MessageView;
import com.fundagent.backend.conversation.service.ConversationService;
import com.fundagent.backend.document.Document;
import com.fundagent.backend.document.DocumentController;
import com.fundagent.backend.document.service.DocumentService;
import com.fundagent.backend.kb.KbController;
import com.fundagent.backend.kb.KnowledgeBase;
import com.fundagent.backend.kb.service.KbService;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/** 各控制器的 @WebMvcTest：参数校验、状态码、响应体；用户 id 由测试直接放进请求属性（鉴权拦截器另测）。 */
@WebMvcTest({
    AuthController.class,
    KbController.class,
    DocumentController.class,
    ConversationController.class,
    ChatController.class
})
@Import({GlobalExceptionHandler.class, RequestIdFilter.class, ControllersWebMvcTest.Props.class})
class ControllersWebMvcTest {

    @TestConfiguration
    static class Props {
        @Bean
        ChatProperties chatProperties() {
            return new ChatProperties(6, Duration.ofSeconds(5), Duration.ofMinutes(1));
        }
    }

    @Autowired
    MockMvc mvc;

    @Autowired
    ObjectMapper json;

    @MockitoBean
    AuthService auth;

    @MockitoBean
    KbService kbs;

    @MockitoBean
    DocumentService documents;

    @MockitoBean
    ConversationService conversations;

    @MockitoBean
    ChatService chat;

    private static final long USER = 7;

    private static org.springframework.test.web.servlet.request.RequestPostProcessor asUser() {
        return req -> {
            req.setAttribute(AuthInterceptor.USER_ID_ATTR, USER);
            return req;
        };
    }

    // ------------------------------------------------------------------ auth

    @Test
    void registerValidatesInputAndReturnsToken() throws Exception {
        when(auth.register("alice_01", "password123")).thenReturn(new AuthResult("tok", 1, "alice_01"));
        mvc.perform(post("/api/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"alice_01\",\"password\":\"password123\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.token").value("tok"))
                .andExpect(jsonPath("$.requestId").exists());
        mvc.perform(post("/api/auth/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"ab\",\"password\":\"short\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(40000));
        mvc.perform(post("/api/auth/register").contentType(MediaType.APPLICATION_JSON).content("{broken"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void loginFailureIs401AndMeUsesTheAuthenticatedUser() throws Exception {
        when(auth.login("alice_01", "wrong-password"))
                .thenThrow(new BizException(ErrorCode.UNAUTHORIZED, "用户名或密码错误"));
        mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"alice_01\",\"password\":\"wrong-password\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value(40100));
        when(auth.me(USER)).thenReturn(new UserView(USER, "alice_01"));
        mvc.perform(get("/api/auth/me").with(asUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.username").value("alice_01"));
    }

    // ------------------------------------------------------------------ kb

    private static KnowledgeBase kb(long id, Long owner, String type, String name) {
        KnowledgeBase k = new KnowledgeBase();
        k.setId(id);
        k.setOwnerId(owner);
        k.setKbType(type);
        k.setName(name);
        return k;
    }

    @Test
    void kbListMarksThePublicKbReadOnly() throws Exception {
        when(kbs.listAccessible(USER))
                .thenReturn(List.of(kb(1, null, "PUBLIC", "基金披露文件"), kb(11, USER, "PRIVATE", "我的笔记")));
        mvc.perform(get("/api/kbs").with(asUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[0].readOnly").value(true))
                .andExpect(jsonPath("$.data[1].readOnly").value(false))
                .andExpect(jsonPath("$.data[1].name").value("我的笔记"));
    }

    @Test
    void kbCreateValidatesNameAndDeleteMapsForbidden() throws Exception {
        when(kbs.create(USER, "研究笔记")).thenReturn(kb(12, USER, "PRIVATE", "研究笔记"));
        mvc.perform(post("/api/kbs").with(asUser()).contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"研究笔记\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(12));
        mvc.perform(post("/api/kbs").with(asUser()).contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"  \"}"))
                .andExpect(status().isBadRequest());
        doThrow(new BizException(ErrorCode.FORBIDDEN, "公共知识库只读")).when(kbs).delete(USER, 1L);
        mvc.perform(delete("/api/kbs/1").with(asUser()))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value(40300));
    }

    // ------------------------------------------------------------------ documents

    private static Document doc(long id, String status) {
        Document d = new Document();
        d.setId(id);
        d.setKbId(11L);
        d.setFilename("研报.pdf");
        d.setSizeBytes(1234L);
        d.setStatus(status);
        return d;
    }

    @Test
    void uploadReturns202WithPendingDocument() throws Exception {
        byte[] bytes = "%PDF-1.7".getBytes();
        when(documents.upload(eq(USER), eq(11L), eq("研报.pdf"), any())).thenReturn(doc(5, "PENDING"));
        mvc.perform(multipart("/api/kbs/11/documents")
                        .file(new MockMultipartFile("file", "研报.pdf", "application/pdf", bytes))
                        .with(asUser()))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.data.status").value("PENDING"))
                .andExpect(jsonPath("$.data.filename").value("研报.pdf"));
        verify(documents).upload(USER, 11L, "研报.pdf", bytes);
    }

    @Test
    void uploadErrorsKeepTheirHttpStatus() throws Exception {
        when(documents.upload(anyLong(), anyLong(), any(), any()))
                .thenThrow(new BizException(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "只支持 PDF、Markdown、TXT 文件"))
                .thenThrow(new BizException(ErrorCode.CONFLICT, "该文件已上传过"))
                .thenThrow(new BizException(ErrorCode.PAYLOAD_TOO_LARGE, "文件超过 20MB 上限"));
        var file = new MockMultipartFile("file", "a.exe", "application/octet-stream", new byte[] {1});
        mvc.perform(multipart("/api/kbs/11/documents").file(file).with(asUser())).andExpect(status().isUnsupportedMediaType());
        mvc.perform(multipart("/api/kbs/11/documents").file(file).with(asUser())).andExpect(status().isConflict());
        mvc.perform(multipart("/api/kbs/11/documents").file(file).with(asUser())).andExpect(status().isPayloadTooLarge());
        // 缺少 file 部分
        mvc.perform(multipart("/api/kbs/11/documents").with(asUser())).andExpect(status().isBadRequest());
    }

    @Test
    void documentListGetAndDelete() throws Exception {
        when(documents.list(USER, 11L)).thenReturn(List.of(doc(5, "READY"), doc(4, "FAILED")));
        mvc.perform(get("/api/kbs/11/documents").with(asUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.length()").value(2))
                .andExpect(jsonPath("$.data[0].status").value("READY"));
        when(documents.get(USER, 5L)).thenReturn(doc(5, "READY"));
        mvc.perform(get("/api/documents/5").with(asUser())).andExpect(jsonPath("$.data.id").value(5));
        doThrow(new BizException(ErrorCode.NOT_FOUND, "文档不存在")).when(documents).delete(USER, 99L);
        mvc.perform(delete("/api/documents/99").with(asUser())).andExpect(status().isNotFound());
        mvc.perform(delete("/api/documents/5").with(asUser())).andExpect(status().isOk());
        verify(documents).delete(USER, 5L);
    }

    // ------------------------------------------------------------------ conversations

    @Test
    void conversationsCreateListAndHistory() throws Exception {
        Conversation c = new Conversation();
        c.setId(3L);
        c.setTitle("新对话");
        when(conversations.create(eq(USER), isNull())).thenReturn(c);
        mvc.perform(post("/api/conversations").with(asUser()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(3))
                .andExpect(jsonPath("$.data.title").value("新对话"));
        when(conversations.list(USER)).thenReturn(List.of(c));
        mvc.perform(get("/api/conversations").with(asUser())).andExpect(jsonPath("$.data.length()").value(1));
        var citations = json.readTree("[{\"id\":1,\"kind\":\"document\"}]");
        when(conversations.history(USER, 3L))
                .thenReturn(List.of(
                        new MessageView(1, "USER", "问", null, null, "OK", "r", null, null),
                        new MessageView(2, "ASSISTANT", "答[1]", citations, "风险提示", "OK", "r", null, null)));
        mvc.perform(get("/api/conversations/3/messages").with(asUser()))
                .andExpect(jsonPath("$.data[1].citations[0].kind").value("document"))
                .andExpect(jsonPath("$.data[1].disclaimer").value("风险提示"));
        doThrow(new BizException(ErrorCode.NOT_FOUND, "会话不存在")).when(conversations).history(USER, 404L);
        mvc.perform(get("/api/conversations/404/messages").with(asUser())).andExpect(status().isNotFound());
    }

    // ------------------------------------------------------------------ chat（SSE）

    @Test
    void chatStreamsSseEventsFromTheService() throws Exception {
        when(chat.start(eq(USER), eq(3L), eq("问题？"), anyList(), any(ChatSink.class))).thenAnswer(inv -> {
            ChatSink sink = inv.getArgument(4);
            sink.send("meta", "{\"request_id\":\"r\"}");
            sink.send("token", "{\"text\":\"你好\"}");
            sink.send("disclaimer", "{\"text\":\"风险提示\"}");
            sink.send("done", "{\"status\":\"ok\"}");
            sink.complete();
            return session();
        });
        MvcResult started = mvc.perform(post("/api/conversations/3/chat")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .content("{\"question\":\"问题？\",\"kbIds\":[1,11]}"))
                .andExpect(request().asyncStarted())
                .andReturn();
        MvcResult done = mvc.perform(asyncDispatch(started))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andReturn();
        // 字节是 UTF-8（MockMvc 默认按 ISO-8859-1 解码，所以显式指定）
        String body = done.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        org.assertj.core.api.Assertions.assertThat(body)
                .contains("event:meta", "data:{\"text\":\"你好\"}", "event:disclaimer", "event:done");
        org.assertj.core.api.Assertions.assertThat(body.indexOf("event:disclaimer"))
                .isLessThan(body.indexOf("event:done"));
    }

    @Test
    void chatRejectionsAreOrdinaryJsonErrorsBeforeAnyStreaming() throws Exception {
        when(chat.start(eq(USER), eq(3L), any(), eq(List.of(22L)), any(ChatSink.class)))
                .thenThrow(new BizException(ErrorCode.FORBIDDEN, "无权访问所选的知识库"));
        // 真实客户端会带 Accept: text/event-stream，错误响应仍必须是 JSON（曾经因此变成 500）
        mvc.perform(post("/api/conversations/3/chat")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .accept(MediaType.TEXT_EVENT_STREAM)
                        .content("{\"question\":\"问\",\"kbIds\":[22]}"))
                .andExpect(status().isForbidden())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value(40300));
        mvc.perform(post("/api/conversations/3/chat")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"\"}"))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/api/conversations/3/chat")
                        .with(asUser())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"" + "长".repeat(2001) + "\"}"))
                .andExpect(status().isBadRequest());
    }

    private static ChatSession session() {
        return new ChatSession() {
            @Override
            public void clientGone(String reason) {}

            @Override
            public boolean isFinished() {
                return true;
            }
        };
    }
}
