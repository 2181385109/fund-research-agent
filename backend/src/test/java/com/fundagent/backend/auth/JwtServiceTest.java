package com.fundagent.backend.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fundagent.backend.auth.service.JwtService;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import com.fundagent.backend.config.SecurityProperties;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

class JwtServiceTest {

    private static final Instant T0 = Instant.parse("2026-09-29T00:00:00Z");

    private static JwtService service(String secret, Instant now) {
        return new JwtService(
                new SecurityProperties(secret, Duration.ofHours(1), "internal"), Clock.fixed(now, ZoneOffset.UTC));
    }

    @Test
    void issuedTokenRoundTripsToUserId() {
        JwtService jwt = service("a-secret-of-any-length-16+", T0);
        assertThat(jwt.parse(jwt.issue(42, "alice"))).isEqualTo(42L);
    }

    @Test
    void tokenExpiresAfterTtl() {
        String token = service("a-secret-of-any-length-16+", T0).issue(1, "u");
        JwtService later = service("a-secret-of-any-length-16+", T0.plus(Duration.ofHours(2)));
        assertThatThrownBy(() -> later.parse(token))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.UNAUTHORIZED));
    }

    @Test
    void tokenSignedWithAnotherSecretOrTamperedIsRejected() {
        String token = service("secret-number-one-000000", T0).issue(1, "u");
        assertThatThrownBy(() -> service("secret-number-two-000000", T0).parse(token)).isInstanceOf(BizException.class);
        JwtService jwt = service("secret-number-one-000000", T0);
        String tampered = token.substring(0, token.length() - 3) + (token.endsWith("AAA") ? "BBB" : "AAA");
        assertThatThrownBy(() -> jwt.parse(tampered)).isInstanceOf(BizException.class);
        assertThatThrownBy(() -> jwt.parse("not-a-jwt")).isInstanceOf(BizException.class);
        assertThatThrownBy(() -> jwt.parse("")).isInstanceOf(BizException.class);
    }

    @Test
    void missingOrShortSecretFailsFast() {
        assertThatThrownBy(() -> service(null, T0)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> service("short", T0)).isInstanceOf(IllegalStateException.class);
    }
}
