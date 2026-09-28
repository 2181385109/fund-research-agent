package com.fundagent.backend.common;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.slf4j.MDC;

/**
 * 统一响应体。
 *
 * @param code      0 表示成功，其余见 {@link ErrorCode}
 * @param message   人可读的说明
 * @param data      业务数据；失败时也可携带（例如 health 的各依赖明细）
 * @param requestId 本次请求的 requestId，与日志中的 MDC 一致
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ApiResponse<T>(int code, String message, T data, String requestId) {

    public static <T> ApiResponse<T> ok(T data) {
        return of(ErrorCode.OK, ErrorCode.OK.defaultMessage(), data);
    }

    public static <T> ApiResponse<T> error(ErrorCode errorCode, String message) {
        return of(errorCode, message, null);
    }

    public static <T> ApiResponse<T> of(ErrorCode errorCode, String message, T data) {
        return new ApiResponse<>(errorCode.code(), message, data, MDC.get(RequestIdFilter.MDC_KEY));
    }
}
