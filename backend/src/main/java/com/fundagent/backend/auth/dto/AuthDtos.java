package com.fundagent.backend.auth.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public final class AuthDtos {

    private AuthDtos() {}

    public record Credentials(
            @NotBlank @Size(min = 3, max = 32) String username,
            @NotBlank @Size(min = 8, max = 72) String password) {}

    public record AuthResult(String token, long userId, String username) {}

    public record UserView(long userId, String username) {}
}
