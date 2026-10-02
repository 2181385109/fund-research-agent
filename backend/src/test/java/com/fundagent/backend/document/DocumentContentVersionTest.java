package com.fundagent.backend.document;

import static org.assertj.core.api.Assertions.assertThat;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.MybatisSqlSessionFactoryBuilder;
import com.fundagent.backend.document.mapper.DocumentMapper;
import com.fundagent.backend.testsupport.TestMysql;
import org.apache.ibatis.mapping.Environment;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

/** 私有库「内容版本」的 SQL（语义缓存按它隔离）：对真实 MySQL 执行，覆盖增删文档、状态变化。 */
class DocumentContentVersionTest {

    private static final long USER = 8_001L;
    private static final long KB = 8_101L;

    private final JdbcTemplate jdbc = new JdbcTemplate(TestMysql.dataSource());
    private final SqlSessionFactory factory = factory();

    private static SqlSessionFactory factory() {
        MybatisConfiguration cfg = new MybatisConfiguration();
        cfg.setEnvironment(new Environment("test", new JdbcTransactionFactory(), TestMysql.dataSource()));
        cfg.addMapper(DocumentMapper.class);
        return new MybatisSqlSessionFactoryBuilder().build(cfg);
    }

    @BeforeEach
    void seed() {
        jdbc.update("DELETE FROM documents WHERE kb_id = ?", KB);
        jdbc.update("DELETE FROM knowledge_bases WHERE id = ?", KB);
        jdbc.update("DELETE FROM users WHERE id = ?", USER);
        jdbc.update("INSERT INTO users (id, username, password_hash) VALUES (?, 'ver-test', 'x')", USER);
        jdbc.update("INSERT INTO knowledge_bases (id, owner_id, name, kb_type) VALUES (?, ?, 'k', 'PRIVATE')", KB, USER);
    }

    private void addDoc(String sha, String status, String updatedAt) {
        jdbc.update(
                "INSERT INTO documents (kb_id, owner_id, filename, ext, size_bytes, sha256, storage_path, ai_doc_id, status, updated_at) "
                        + "VALUES (?, ?, 'f.pdf', 'pdf', 1, ?, 'p', ?, ?, ?)",
                KB, USER, sha.repeat(64).substring(0, 64), "ai-" + sha + KB, status, updatedAt);
    }

    private String version() {
        try (SqlSession s = factory.openSession(true)) {
            return s.getMapper(DocumentMapper.class).contentVersion(KB);
        }
    }

    @Test
    void emptyKbHasAStableVersion() {
        assertThat(version()).isEqualTo("0-0");
    }

    @Test
    void addingOrRemovingAReadyDocumentChangesTheVersion() {
        addDoc("a", "READY", "2026-10-02 10:00:00.000");
        String v1 = version();
        assertThat(v1).startsWith("1-");

        addDoc("b", "READY", "2026-10-02 11:00:00.000");
        String v2 = version();
        assertThat(v2).startsWith("2-").isNotEqualTo(v1);

        jdbc.update("DELETE FROM documents WHERE kb_id = ? AND sha256 LIKE 'bbbb%'", KB);
        assertThat(version()).isEqualTo(v1); // 回到只有 a：版本与之前相同（内容也相同）

        jdbc.update("DELETE FROM documents WHERE kb_id = ?", KB);
        assertThat(version()).isEqualTo("0-0");
    }

    @Test
    void documentsThatAreNotReadyDoNotAffectTheVersion() {
        addDoc("a", "READY", "2026-10-02 10:00:00.000");
        String v = version();
        addDoc("c", "PROCESSING", "2026-10-02 12:00:00.000");
        addDoc("d", "FAILED", "2026-10-02 13:00:00.000");
        assertThat(version()).isEqualTo(v);
    }

    @Test
    void reIngestingChangesTheLatestUpdateTimeAndSoTheVersion() {
        addDoc("a", "READY", "2026-10-02 10:00:00.000");
        String v1 = version();
        jdbc.update("UPDATE documents SET updated_at = '2026-10-02 15:30:00.000' WHERE kb_id = ?", KB);
        assertThat(version()).isNotEqualTo(v1).startsWith("1-");
    }
}
