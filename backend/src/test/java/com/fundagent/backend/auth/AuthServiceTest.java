package com.fundagent.backend.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fundagent.backend.auth.dto.AuthDtos.AuthResult;
import com.fundagent.backend.auth.mapper.UserMapper;
import com.fundagent.backend.auth.service.AuthService;
import com.fundagent.backend.auth.service.JwtService;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

class AuthServiceTest {

    private final UserMapper users = mock(UserMapper.class);
    private final JwtService jwt = mock(JwtService.class);
    private final PasswordEncoder encoder = new BCryptPasswordEncoder(4); // 低成本，测试快
    private AuthService auth;

    @BeforeEach
    void setUp() {
        auth = new AuthService(users, encoder, jwt);
        when(jwt.issue(org.mockito.ArgumentMatchers.anyLong(), any())).thenReturn("jwt-token");
    }

    private User stored(String name, String rawPassword) {
        User u = new User();
        u.setId(7L);
        u.setUsername(name);
        u.setPasswordHash(encoder.encode(rawPassword));
        return u;
    }

    @Test
    void registerHashesPasswordWithBcryptAndReturnsToken() {
        doAnswer(inv -> {
                    ((User) inv.getArgument(0)).setId(11L);
                    return 1;
                })
                .when(users)
                .insert(any(User.class));
        AuthResult r = auth.register("alice_01", "correct horse");
        assertThat(r.token()).isEqualTo("jwt-token");
        assertThat(r.userId()).isEqualTo(11L);
        org.mockito.ArgumentCaptor<User> cap = org.mockito.ArgumentCaptor.forClass(User.class);
        verify(users).insert(cap.capture());
        assertThat(cap.getValue().getPasswordHash()).startsWith("$2").isNotEqualTo("correct horse");
        assertThat(encoder.matches("correct horse", cap.getValue().getPasswordHash())).isTrue();
    }

    @Test
    void registerRejectsDuplicateBadNameAndOverlongPassword() {
        when(users.selectOne(any())).thenReturn(stored("alice_01", "whatever1"));
        assertThatThrownBy(() -> auth.register("alice_01", "password123"))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.CONFLICT));
        assertThatThrownBy(() -> auth.register("bad name!", "password123"))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.BAD_REQUEST));
        assertThatThrownBy(() -> auth.register("bob_01", "密".repeat(25))) // 75 字节 > 72
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.BAD_REQUEST));
    }

    @Test
    void concurrentRegistrationOfSameNameMapsToConflict() {
        doThrow(new DuplicateKeyException("uk_users_username")).when(users).insert(any(User.class));
        assertThatThrownBy(() -> auth.register("carol_01", "password123"))
                .isInstanceOfSatisfying(BizException.class, e -> assertThat(e.errorCode()).isEqualTo(ErrorCode.CONFLICT));
    }

    @Test
    void loginSucceedsWithRightPassword() {
        when(users.selectOne(any())).thenReturn(stored("alice_01", "correct horse"));
        AuthResult r = auth.login("alice_01", "correct horse");
        assertThat(r.token()).isEqualTo("jwt-token");
        assertThat(r.username()).isEqualTo("alice_01");
    }

    @Test
    void wrongPasswordAndUnknownUserGetTheSameError() {
        when(users.selectOne(any())).thenReturn(stored("alice_01", "correct horse"), (User) null);
        BizException wrong = catchBiz(() -> auth.login("alice_01", "wrong-password"));
        BizException unknown = catchBiz(() -> auth.login("nobody_01", "wrong-password"));
        assertThat(wrong.errorCode()).isEqualTo(ErrorCode.UNAUTHORIZED);
        assertThat(unknown.errorCode()).isEqualTo(ErrorCode.UNAUTHORIZED);
        assertThat(unknown.getMessage()).isEqualTo(wrong.getMessage());
    }

    @Test
    void meReturnsProfileOrUnauthorized() {
        when(users.selectById(7L)).thenReturn(stored("alice_01", "x"));
        assertThat(auth.me(7L).username()).isEqualTo("alice_01");
        assertThatThrownBy(() -> auth.me(999L)).isInstanceOf(BizException.class);
    }

    private static BizException catchBiz(Runnable r) {
        try {
            r.run();
        } catch (BizException e) {
            return e;
        }
        throw new AssertionError("expected BizException");
    }
}
