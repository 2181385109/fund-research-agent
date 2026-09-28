package com.fundagent.backend.common;

import org.springframework.http.HttpStatus;

/** 错误码集中定义：code 前三位与 HTTP 状态一致，便于排查。 */
public enum ErrorCode {
    OK(0, HttpStatus.OK, "ok"),
    BAD_REQUEST(40000, HttpStatus.BAD_REQUEST, "请求参数不合法"),
    NOT_FOUND(40400, HttpStatus.NOT_FOUND, "资源不存在"),
    INTERNAL_ERROR(50000, HttpStatus.INTERNAL_SERVER_ERROR, "服务内部错误"),
    DEPENDENCY_DOWN(50300, HttpStatus.SERVICE_UNAVAILABLE, "依赖服务不可用");

    private final int code;
    private final HttpStatus httpStatus;
    private final String defaultMessage;

    ErrorCode(int code, HttpStatus httpStatus, String defaultMessage) {
        this.code = code;
        this.httpStatus = httpStatus;
        this.defaultMessage = defaultMessage;
    }

    public int code() {
        return code;
    }

    public HttpStatus httpStatus() {
        return httpStatus;
    }

    public String defaultMessage() {
        return defaultMessage;
    }
}
