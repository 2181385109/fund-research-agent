# data（占位）

属于 **S1 基金池与数据采集**，尚未实现。

入库：`universe.yaml`（基金池，用户确认后冻结）、`MANIFEST.json`（PDF 与快照的 URL、sha256、页数、可提取性）。

**不入库**（`.gitignore`）：`raw/`（原始披露 PDF）、`snapshots/<DATA_AS_OF>/`（结构化数据快照）。原因见 PLAN §2.1 版权与合规。
