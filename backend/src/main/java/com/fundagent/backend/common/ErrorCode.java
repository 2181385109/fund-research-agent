package com.fundagent.backend.common;

import org.springframework.http.HttpStatus;

/** 错误码集中定义：code 前三位与 HTTP 状态一致，便于排查。 */
public enum ErrorCode {
    OK(0, HttpStatus.OK, "ok"),
    BAD_REQUEST(40000, HttpStatus.BAD_REQUEST, "请求参数不合法"),
    UNAUTHORIZED(40100, HttpStatus.UNAUTHORIZED, "未登录或登录已过期"),
    FORBIDDEN(40300, HttpStatus.FORBIDDEN, "无权访问"),
    NOT_FOUND(40400, HttpStatus.NOT_FOUND, "资源不存在"),
    CONFLICT(40900, HttpStatus.CONFLICT, "资源冲突"),
    PAYLOAD_TOO_LARGE(41300, HttpStatus.PAYLOAD_TOO_LARGE, "文件过大"),
    UNSUPPORTED_MEDIA_TYPE(41500, HttpStatus.UNSUPPORTED_MEDIA_TYPE, "不支持的文件类型"),
    INTERNAL_ERROR(50000, HttpStatus.INTERNAL_SERVER_ERROR, "服务内部错误"),
    BAD_GATEWAY(50200, HttpStatus.BAD_GATEWAY, "上游服务不可用"),
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
