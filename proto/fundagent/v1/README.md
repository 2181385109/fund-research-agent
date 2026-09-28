# v1（占位）

属于 **S9 gRPC 改造**，尚未实现。

`Chat`（server streaming）、`Retrieve`、`IngestDocument`、`DeleteDocument` 的 proto 定义。改动须向后兼容：字段号不重用，删除的字段用 `reserved`。
