package com.fundagent.backend.auth.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fundagent.backend.auth.User;
import com.fundagent.backend.auth.dto.AuthDtos.AuthResult;
import com.fundagent.backend.auth.dto.AuthDtos.UserView;
import com.fundagent.backend.auth.mapper.UserMapper;
import com.fundagent.backend.common.BizException;
import com.fundagent.backend.common.ErrorCode;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

/** 注册 / 登录：BCrypt 存密码，JWT 无角色。 */
@Service
public class AuthService {

    private static final Pattern USERNAME = Pattern.compile("[A-Za-z0-9_]{3,32}");
    /** BCrypt 只取前 72 字节，超出会被静默截断，所以直接拒绝。 */
    static final int MAX_PASSWORD_BYTES = 72;
    /** 用户不存在时也做一次哈希比较，避免通过耗时区分「用户名不存在」和「密码错」。 */
    private final String dummyHash;

    private final UserMapper users;
    private final PasswordEncoder encoder;
    private final JwtService jwt;

    public AuthService(UserMapper users, PasswordEncoder encoder, JwtService jwt) {
        this.users = users;
        this.encoder = encoder;
        this.jwt = jwt;
        this.dummyHash = encoder.encode("not-a-real-password");
    }

    public AuthResult register(String username, String password) {
        if (!USERNAME.matcher(username).matches()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "用户名只能包含字母、数字和下划线，长度 3–32");
        }
        if (password.getBytes(StandardCharsets.UTF_8).length > MAX_PASSWORD_BYTES) {
            throw new BizException(ErrorCode.BAD_REQUEST, "密码过长（UTF-8 不超过 72 字节）");
        }
        if (findByUsername(username) != null) {
            throw new BizException(ErrorCode.CONFLICT, "用户名已被注册");
        }
        User u = new User();
        u.setUsername(username);
        u.setPasswordHash(encoder.encode(password));
        try {
            users.insert(u);
        } catch (DuplicateKeyException e) { // 并发注册同名用户：以唯一索引为准
            throw new BizException(ErrorCode.CONFLICT, "用户名已被注册");
        }
        return new AuthResult(jwt.issue(u.getId(), username), u.getId(), username);
    }

    public AuthResult login(String username, String password) {
        User u = findByUsername(username);
        boolean ok = encoder.matches(password, u != null ? u.getPasswordHash() : dummyHash);
        if (u == null || !ok) {
            throw new BizException(ErrorCode.UNAUTHORIZED, "用户名或密码错误");
        }
        return new AuthResult(jwt.issue(u.getId(), u.getUsername()), u.getId(), u.getUsername());
    }

    public UserView me(long userId) {
        User u = users.selectById(userId);
        if (u == null) {
            throw new BizException(ErrorCode.UNAUTHORIZED);
        }
        return new UserView(u.getId(), u.getUsername());
    }

    private User findByUsername(String username) {
        return users.selectOne(new LambdaQueryWrapper<User>().eq(User::getUsername, username));
    }
}
