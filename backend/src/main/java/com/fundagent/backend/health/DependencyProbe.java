package com.fundagent.backend.health;

/** 一个外部依赖的健康探测。实现类只负责「探一次」，成功正常返回，失败抛异常；计时和汇总由 {@link HealthService} 做。 */
public interface DependencyProbe {

    /** 依赖名，出现在 health 响应的 components 里，例如 mysql、redis。 */
    String name();

    void probe() throws Exception;
}
