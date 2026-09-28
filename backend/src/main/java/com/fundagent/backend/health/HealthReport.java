package com.fundagent.backend.health;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.Map;

/** health 接口的数据部分：总体状态 + 每个依赖的状态和耗时。 */
public record HealthReport(Status status, Map<String, Component> components) {

    public enum Status { UP, DOWN }

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Component(Status status, long latencyMs, String error) {}

    @JsonIgnore
    public boolean isUp() {
        return status == Status.UP;
    }
}
