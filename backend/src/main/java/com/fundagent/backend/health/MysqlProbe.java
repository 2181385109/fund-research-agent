package com.fundagent.backend.health;

import com.fundagent.backend.config.HealthProperties;
import java.sql.Connection;
import java.sql.SQLException;
import javax.sql.DataSource;
import org.springframework.stereotype.Component;

/** 从连接池取一个连接并做 isValid 校验；取连接的等待上限由 Hikari connection-timeout 控制。 */
@Component
public class MysqlProbe implements DependencyProbe {

    private final DataSource dataSource;
    private final HealthProperties properties;

    public MysqlProbe(DataSource dataSource, HealthProperties properties) {
        this.dataSource = dataSource;
        this.properties = properties;
    }

    @Override
    public String name() {
        return "mysql";
    }

    @Override
    public void probe() throws SQLException {
        try (Connection connection = dataSource.getConnection()) {
            int timeoutSeconds = (int) Math.max(1, properties.timeout().toSeconds());
            if (!connection.isValid(timeoutSeconds)) {
                throw new SQLException("connection is not valid");
            }
        }
    }
}
