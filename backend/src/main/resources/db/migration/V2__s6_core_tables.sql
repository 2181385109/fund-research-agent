-- S6：用户、知识库、文档、会话、消息。迁移脚本只增不改（CLAUDE.md §4）。

CREATE TABLE users (
    id            BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(64)  NOT NULL,
    password_hash VARCHAR(100) NOT NULL COMMENT 'BCrypt',
    created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_users_username (username)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- kb_type = PUBLIC：系统预置的公共库（owner_id 为空，所有用户只读）；PRIVATE：用户自己的库
CREATE TABLE knowledge_bases (
    id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    owner_id   BIGINT       NULL,
    name       VARCHAR(100) NOT NULL,
    kb_type    VARCHAR(16)  NOT NULL,
    created_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_kb_owner (owner_id),
    CONSTRAINT fk_kb_owner FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

INSERT INTO knowledge_bases (id, owner_id, name, kb_type) VALUES (1, NULL, '基金披露文件', 'PUBLIC');
ALTER TABLE knowledge_bases AUTO_INCREMENT = 100;

-- ai_doc_id：写进 Milvus / ES 的 doc_id（u{用户}-k{库}-{sha256 前 16 位}），也是 ai-service 回调时的定位键
CREATE TABLE documents (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    kb_id       BIGINT       NOT NULL,
    owner_id    BIGINT       NOT NULL,
    filename    VARCHAR(255) NOT NULL,
    ext         VARCHAR(8)   NOT NULL,
    size_bytes  BIGINT       NOT NULL,
    sha256      CHAR(64)     NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    ai_doc_id   VARCHAR(120) NOT NULL,
    status      VARCHAR(16)  NOT NULL COMMENT 'PENDING / PROCESSING / READY / FAILED',
    error       VARCHAR(500) NULL,
    pages       INT          NULL,
    chunks      INT          NULL,
    created_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_doc_kb_sha (kb_id, sha256),
    UNIQUE KEY uk_doc_ai_doc_id (ai_doc_id),
    KEY idx_doc_status (status, updated_at),
    CONSTRAINT fk_doc_kb FOREIGN KEY (kb_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_owner FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

CREATE TABLE conversations (
    id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    title      VARCHAR(200) NOT NULL,
    created_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    KEY idx_conv_user (user_id, updated_at),
    CONSTRAINT fk_conv_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- role = USER / ASSISTANT；助手消息的 status = OK / ERROR / CANCELLED（客户端中途断开时保存已生成的部分）
-- citations：ai-service 的 citations.items 原样（JSON 数组）；disclaimer：服务端固定文案
-- kb_ids：本条提问实际使用的检索范围（JSON 数组，审计用）
CREATE TABLE messages (
    id              BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT      NOT NULL,
    role            VARCHAR(16) NOT NULL,
    content         MEDIUMTEXT  NOT NULL,
    citations       JSON        NULL,
    disclaimer      VARCHAR(500) NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'OK',
    request_id      VARCHAR(64) NULL,
    kb_ids          JSON        NULL,
    created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_msg_conv (conversation_id, id),
    CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;
