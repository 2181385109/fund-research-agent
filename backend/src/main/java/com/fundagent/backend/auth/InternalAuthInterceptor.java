package com.fundagent.backend.auth;

import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.SecurityProperties;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.web.servlet.HandlerInterceptor;

/** /internal/**（ai-service 回调）：校验共享密钥头 X-Internal-Secret，常量时间比较；未配置密钥时一律拒绝。 */
public class InternalAuthInterceptor implements HandlerInterceptor {

    public static final String HEADER = "X-Internal-Secret";

    private final byte[] secret;

    public InternalAuthInterceptor(SecurityProperties props) {
        String s = props.internalSecret();
        this.secret = s == null || s.isBlank() ? null : s.getBytes(StandardCharsets.UTF_8);
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String given = request.getHeader(HEADER);
        if (secret == null
                || given == null
                || !MessageDigest.isEqual(secret, given.getBytes(StandardCharsets.UTF_8))) {
            throw new BizException(ErrorCode.UNAUTHORIZED, "内部接口凭据无效");
        }
        return true;
    }
}
