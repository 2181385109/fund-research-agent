package com.fundagent.backend.auth.service;

import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.SecurityProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.util.Date;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.stereotype.Service;

/** JWT（HS256）：主体是用户 id。密钥取配置串的 SHA-256，所以 JWT_SECRET 任意长度都可以（要求 ≥ 16 字符）。 */
@Service
public class JwtService {

    static final int MIN_SECRET_CHARS = 16;

    private final SecretKey key;
    private final SecurityProperties props;
    private final Clock clock;

    public JwtService(SecurityProperties props, Clock clock) {
        String secret = props.jwtSecret();
        if (secret == null || secret.length() < MIN_SECRET_CHARS) {
            throw new IllegalStateException("JWT_SECRET 未配置或太短（至少 " + MIN_SECRET_CHARS + " 个字符）");
        }
        this.props = props;
        this.clock = clock;
        this.key = new SecretKeySpec(sha256(secret), "HmacSHA256");
    }

    public String issue(long userId, String username) {
        Date now = Date.from(clock.instant());
        return Jwts.builder()
                .subject(Long.toString(userId))
                .claim("name", username)
                .issuedAt(now)
                .expiration(new Date(now.getTime() + props.jwtTtl().toMillis()))
                .signWith(key)
                .compact();
    }

    /** 校验签名与有效期，返回用户 id；失败抛 401。 */
    public long parse(String token) {
        try {
            Claims c = Jwts.parser()
                    .verifyWith(key)
                    .clock(() -> Date.from(clock.instant()))
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
            return Long.parseLong(c.getSubject());
        } catch (JwtException | IllegalArgumentException e) {
            throw new BizException(ErrorCode.UNAUTHORIZED);
        }
    }

    private static byte[] sha256(String s) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
