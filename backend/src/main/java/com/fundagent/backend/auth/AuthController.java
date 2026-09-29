package com.fundagent.backend.auth;

import com.fundagent.backend.auth.dto.AuthDtos.AuthResult;
import com.fundagent.backend.auth.dto.AuthDtos.Credentials;
import com.fundagent.backend.auth.dto.AuthDtos.UserView;
import com.fundagent.backend.auth.service.AuthService;
import com.fundagent.backend.common.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private final AuthService auth;

    public AuthController(AuthService auth) {
        this.auth = auth;
    }

    @Operation(summary = "注册", description = "用户名 3–32 位字母数字下划线，密码 8–72 位；成功后直接返回 JWT")
    @PostMapping("/register")
    public ApiResponse<AuthResult> register(@Valid @RequestBody Credentials body) {
        return ApiResponse.ok(auth.register(body.username(), body.password()));
    }

    @Operation(summary = "登录", description = "返回 JWT，之后放在 Authorization: Bearer <token>")
    @PostMapping("/login")
    public ApiResponse<AuthResult> login(@Valid @RequestBody Credentials body) {
        return ApiResponse.ok(auth.login(body.username(), body.password()));
    }

    @Operation(summary = "当前用户")
    @GetMapping("/me")
    public ApiResponse<UserView> me(@RequestAttribute(AuthInterceptor.USER_ID_ATTR) long userId) {
        return ApiResponse.ok(auth.me(userId));
    }
}
