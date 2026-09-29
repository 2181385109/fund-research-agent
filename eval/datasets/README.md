# datasets（S3 评测集 v1）

| 文件 | 内容 |
|---|---|
| `fund_qa_v1.jsonl` | 文档检索为主的问答题（S4 检索评测、S8 回答评测） |
| `agent_tasks_v1.jsonl` | Agent 任务：Text2SQL、收益计算、最新净值、综合题、荐基请求等（S8） |
| `SCHEMA.md` | 字段、gold 约定、证据命中规则、第三期转训练对的方法、test 不进训练的规则 |
| `MANIFEST.json` | 版本、是否冻结、各文件 sha256 与行数、数据快照指纹、报告路径 |
| `CHANGELOG.md` | 变更记录（冻结后修改须升版本并经用户批准） |
| `review/spotcheck_v1.md` | 用户抽检表与结论 |

生成、校验、报告的命令见 `eval/reference/README.md`。冻结前不许用评测集跑任何检索或 Agent（PLAN §5 S3）。
