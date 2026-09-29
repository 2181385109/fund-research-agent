# data

入库：
- `universe.yaml`：基金池 v1（20 只，2026-09-29 用户确认后冻结）
- `MANIFEST.json`：每份披露 PDF 的 doc_id、标题、发布日、URL、sha256、页数、可提取性；缺口清单；经理变更公告；结构化快照每张表的行数和 sha256
- `CHANGELOG.md`：冻结产物（基金池、DATA_AS_OF、快照）的变更记录

**不入库**（`.gitignore`）：`raw/`（原始披露 PDF、抓取缓存）、`snapshots/<DATA_AS_OF>/`（结构化数据快照）。原因见 PLAN §2.1 版权与合规。重建方法见 `data-pipeline/README.md`。
