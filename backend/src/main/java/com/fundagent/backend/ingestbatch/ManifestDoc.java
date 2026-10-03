package com.fundagent.backend.ingestbatch;

/** {@code data/MANIFEST.json} 里一份可入库文档（只取批量入库用得到的字段）。 */
public record ManifestDoc(
        String docId,
        String fundCode,
        String fundName,
        String docType,
        String reportPeriod,
        String title,
        String localPath,
        String sha256) {}
