package com.fundagent.backend.health;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/** 依次探测所有依赖并计时；任一依赖 DOWN，总体即 DOWN。 */
@Service
public class HealthService {

    private static final Logger log = LoggerFactory.getLogger(HealthService.class);

    private final List<DependencyProbe> probes;

    public HealthService(List<DependencyProbe> probes) {
        this.probes = probes;
    }

    public HealthReport check() {
        Map<String, HealthReport.Component> components = new LinkedHashMap<>();
        boolean allUp = true;
        for (DependencyProbe probe : probes) {
            long start = System.nanoTime();
            HealthReport.Component component;
            try {
                probe.probe();
                component = new HealthReport.Component(HealthReport.Status.UP, elapsedMs(start), null);
            } catch (Exception e) {
                allUp = false;
                String error = e.getClass().getSimpleName() + ": " + e.getMessage();
                log.warn("dependency {} DOWN: {}", probe.name(), error);
                component = new HealthReport.Component(HealthReport.Status.DOWN, elapsedMs(start), error);
            }
            components.put(probe.name(), component);
        }
        return new HealthReport(allUp ? HealthReport.Status.UP : HealthReport.Status.DOWN, components);
    }

    private static long elapsedMs(long startNanos) {
        return (System.nanoTime() - startNanos) / 1_000_000;
    }
}
