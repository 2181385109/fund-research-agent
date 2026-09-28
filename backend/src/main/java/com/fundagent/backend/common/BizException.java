package com.fundagent.backend.common;

/** 业务异常：由 {@link GlobalExceptionHandler} 转成对应 HTTP 状态和 {@link ApiResponse}。 */
public class BizException extends RuntimeException {

    private final ErrorCode errorCode;

    public BizException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public BizException(ErrorCode errorCode) {
        this(errorCode, errorCode.defaultMessage());
    }

    public ErrorCode errorCode() {
        return errorCode;
    }
}
