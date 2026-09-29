package com.fundagent.backend.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.util.unit.DataSize;

/**
 * 用户上传文件的配置。
 *
 * @param dir        存放目录（必须在 ai-service 的 DATA_DIR 之下，ai-service 按路径读取）
 * @param maxSize    单个文件上限（PLAN S6：20MB）
 * @param staleAfter PENDING / PROCESSING 超过这个时间还没等到入库回调，就置为 FAILED
 */
@ConfigurationProperties(prefix = "fra.upload")
public record UploadProperties(
        String dir, @DefaultValue("20MB") DataSize maxSize, @DefaultValue("15m") Duration staleAfter) {}
