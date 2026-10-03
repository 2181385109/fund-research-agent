-- S11：季报批量入库。迁移脚本只增不改（CLAUDE.md §4）。
--
-- ingest_batch  一次「把某个报告期的全部季报入库」的批次。
-- ingest_task   批次里每份文档一个任务；status：PENDING（在 outbox 里还没发出）→ SENT（已发到 Kafka，等结果）
--               → SUCCEEDED | SKIPPED（sha256 没变且已 READY，没有重复入库）| FAILED（重试用尽，已进 DLQ）。
--               终态不可回退（消费结果时只更新 PENDING / SENT 的行，所以重复 / 迟到的结果是无害的）。
-- outbox_event  事务性发件箱：批次、任务与待发消息在**同一个事务**里写入，由 OutboxRelay 轮询发到 Kafka，
--               发送成功后标记 SENT（至少一次：发送成功但标记失败会重发，消费端按 doc_id + sha256 幂等）。

CREATE TABLE ingest_batch (
    id            BIGINT       NOT NULL AUTO_INCREMENT,
    report_period VARCHAR(16)  NOT NULL,
    doc_type      VARCHAR(32)  NOT NULL,
    status        VARCHAR(32)  NOT NULL DEFAULT 'RUNNING', -- RUNNING | COMPLETED | COMPLETED_WITH_FAILURES | EXPIRED
    total         INT          NOT NULL,
    -- 同一报告期同一时刻只允许一个进行中的批次：进行中 = 'type:period'，结束后置 NULL（NULL 不参与唯一约束）
    active_key    VARCHAR(64)  NULL,
    created_by    BIGINT       NULL,
    created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    completed_at  DATETIME(3)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ingest_batch_active (active_key),
    KEY idx_ingest_batch_period (report_period, id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

CREATE TABLE ingest_task (
    id          BIGINT       NOT NULL AUTO_INCREMENT,
    batch_id    BIGINT       NOT NULL,
    doc_id      VARCHAR(150) NOT NULL,
    file_path   VARCHAR(255) NOT NULL,
    sha256      CHAR(64)     NOT NULL,
    status      VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
    chunks      INT          NULL,
    attempts    INT          NULL,
    error       VARCHAR(600) NULL,
    consumer_id VARCHAR(128) NULL,
    created_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at DATETIME(3)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ingest_task_batch_doc (batch_id, doc_id),
    KEY idx_ingest_task_batch_status (batch_id, status),
    CONSTRAINT fk_ingest_task_batch FOREIGN KEY (batch_id) REFERENCES ingest_batch (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

CREATE TABLE outbox_event (
    id             BIGINT       NOT NULL AUTO_INCREMENT,
    topic          VARCHAR(128) NOT NULL,
    msg_key        VARCHAR(190) NOT NULL,
    payload        MEDIUMTEXT   NOT NULL,
    aggregate_type VARCHAR(40)  NOT NULL,
    aggregate_id   BIGINT       NOT NULL,
    status         VARCHAR(16)  NOT NULL DEFAULT 'PENDING', -- PENDING | SENT
    attempts       INT          NOT NULL DEFAULT 0,
    last_error     VARCHAR(500) NULL,
    created_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    sent_at        DATETIME(3)  NULL,
    PRIMARY KEY (id),
    KEY idx_outbox_status_id (status, id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;
