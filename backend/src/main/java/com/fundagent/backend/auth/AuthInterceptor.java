package com.fundagent.backend.auth;

import com.fundagent.backend.auth.service.JwtService;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.servlet.HandlerInterceptor;

/** /api/** 的登录校验：解析 Authorization: Bearer，把用户 id 放进请求属性 {@link #USER_ID_ATTR}。 */
public class AuthInterceptor implements HandlerInterceptor {

    public static final String USER_ID_ATTR = "fra.userId";
    private static final String PREFIX = "Bearer ";

    private final JwtService jwt;

    public AuthInterceptor(JwtService jwt) {
        this.jwt = jwt;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if ("OPTIONS".equalsIgnoreCase(request.getMethod())) {
            return true;
        }
        String header = request.getHeader("Authorization");
        if (header == null || !header.startsWith(PREFIX)) {
            throw new BizException(ErrorCode.UNAUTHORIZED);
        }
        request.setAttribute(USER_ID_ATTR, jwt.parse(header.substring(PREFIX.length()).trim()));
        return true;
    }
}
