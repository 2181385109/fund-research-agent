package com.fundagent.backend.chat.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.util.List;

/**
 * 提问。{@code kbIds} 是客户端选择的知识库（缺省 = 我可见的全部：公共库 + 自己的私有库）；
 * 其中任何一个不是自己可访问的，整个请求以 403 拒绝（检索范围由服务端判定，见 ADR-043）。
 */
public record ChatRequest(@NotBlank @Size(max = 2000) String question, @Size(max = 50) List<Long> kbIds) {}
