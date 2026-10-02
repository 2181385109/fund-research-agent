package com.fundagent.backend.testsupport;

import javax.sql.DataSource;
import org.flywaydb.core.Flyway;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.containers.MySQLContainer;

/**
 * 测试用的真实 MySQL（已执行全部 Flyway 迁移）：默认 Testcontainers（CI）；本机可设
 * {@code FRA_TEST_MYSQL=jdbc:mysql://host:port/db?user=…&password=…} 指向一次性的 MySQL（不要指向开发库 fra_app）。
 */
public final class TestMysql {

    private static MySQLContainer<?> container;
    private static DataSource dataSource;

    private TestMysql() {}

    public static synchronized DataSource dataSource() {
        if (dataSource == null) {
            String url;
            String user = null;
            String password = null;
            String external = System.getenv("FRA_TEST_MYSQL");
            if (external != null && !external.isBlank()) {
                url = external;
            } else {
                container = new MySQLContainer<>("mysql:8.4.11");
                container.start();
                url = container.getJdbcUrl() + "?serverTimezone=Asia/Shanghai";
                user = container.getUsername();
                password = container.getPassword();
            }
            DriverManagerDataSource ds = new DriverManagerDataSource(url);
            if (user != null) {
                ds.setUsername(user);
                ds.setPassword(password);
            }
            Flyway.configure().dataSource(ds).locations("classpath:db/migration").load().migrate();
            dataSource = ds;
        }
        return dataSource;
    }
}
