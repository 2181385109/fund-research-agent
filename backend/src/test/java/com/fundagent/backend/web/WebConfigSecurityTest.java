package com.fundagent.backend.web;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fundagent.backend.auth.AuthController;
import com.fundagent.backend.auth.dto.AuthDtos.AuthResult;
import com.fundagent.backend.auth.service.AuthService;
import com.fundagent.backend.auth.service.JwtService;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.common.GlobalExceptionHandler;
import com.fundagent.backend.config.SecurityProperties;
import com.fundagent.backend.config.WebConfig;
import com.fundagent.backend.document.InternalDocumentController;
import com.fundagent.backend.document.service.DocumentService;
import com.fundagent.backend.kb.KbController;
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
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** 路由鉴权：/api/** 要 JWT，注册 / 登录 / health 除外；/internal/** 要共享密钥头。 */
@WebMvcTest({AuthController.class, KbController.class, InternalDocumentController.class})
@Import({GlobalExceptionHandler.class, WebConfig.class, WebConfigSecurityTest.Props.class})
class WebConfigSecurityTest {

    @TestConfiguration
    static class Props {
        @Bean
        SecurityProperties securityProperties() {
            return new SecurityProperties("a-jwt-secret-long-enough", Duration.ofHours(1), "shared-internal-secret");
        }
    }

    @Autowired
    MockMvc mvc;

    @MockitoBean
    JwtService jwt;

    @MockitoBean
    AuthService auth;

    @MockitoBean
    KbService kbs;

    @MockitoBean
    DocumentService documents;

    @Test
    void apiRequiresABearerToken() throws Exception {
        mvc.perform(get("/api/kbs")).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value(40100));
        mvc.perform(get("/api/kbs").header("Authorization", "Basic abc")).andExpect(status().isUnauthorized());
        when(jwt.parse("bad")).thenThrow(new BizException(ErrorCode.UNAUTHORIZED));
        mvc.perform(get("/api/kbs").header("Authorization", "Bearer bad")).andExpect(status().isUnauthorized());
    }

    @Test
    void validTokenPassesAndTheUserIdReachesTheController() throws Exception {
        when(jwt.parse("good")).thenReturn(7L);
        when(kbs.listAccessible(7L)).thenReturn(List.of());
        mvc.perform(get("/api/kbs").header("Authorization", "Bearer good"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0));
    }

    @Test
    void registerAndLoginAreOpen() throws Exception {
        when(auth.login("alice_01", "password123")).thenReturn(new AuthResult("t", 1, "alice_01"));
        mvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"alice_01\",\"password\":\"password123\"}"))
                .andExpect(status().isOk());
    }

    @Test
    void internalCallbackNeedsTheSharedSecret() throws Exception {
        String body = "{\"doc_id\":\"u7-k11-x\",\"status\":\"READY\",\"pages\":1,\"chunks\":2}";
        mvc.perform(post("/internal/documents/callback").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/internal/documents/callback")
                        .header("X-Internal-Secret", "wrong")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isUnauthorized());
        // 用户的 JWT 不能当内部凭据用
        when(jwt.parse("good")).thenReturn(7L);
        mvc.perform(post("/internal/documents/callback")
                        .header("Authorization", "Bearer good")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/internal/documents/callback")
                        .header("X-Internal-Secret", "shared-internal-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isOk());
    }

    @Test
    void internalCallbackValidatesTheStatusValue() throws Exception {
        mvc.perform(post("/internal/documents/callback")
                        .header("X-Internal-Secret", "shared-internal-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"doc_id\":\"x\",\"status\":\"PROCESSING\"}"))
                .andExpect(status().isBadRequest());
    }
}
