你是 fund-research-agent 项目的执行者，项目目录是 D:\xiangmu\fund-research-agent。本对话负责批次 B1：S1 基金池与数据采集，以及 S2 文档入库。

开工前先读：CLAUDE.md（全文，注意 §1 第 7、8 条的分批模式和 HANDOFF 规则，以及 §5）、docs/PLAN.md（§0–§4、§7，以及 §5 中的 S1、S2）、docs/PROGRESS.md 的「当前状态」。S0 已经完成，环境和各种坑记在 PROGRESS 的 S0 一节和 DECISIONS 里。

【先落实 S0 遗留的四个决定，登记 ADR 后再开始 S1】
1. ES 内存：mem_limit 调到 1792m，同时加 -XX:MaxDirectMemorySize=256m。
2. 新增 fund_loader 账号：只有 fund_data 的读写和建表权限，仅供 data-pipeline 导入用；任何代码都不许用 root。
3. LLM_THINKING=disabled、JUDGE_MODEL=deepseek-v4-pro 维持现状（已写入 CLAUDE.md §5）。
4. 镜像加速顺序不改，也不重启 Docker。

【S1 补充要求】
- 基金池约 20 只，医药医疗约 10 只、科技约 10 只，按 PLAN §4.1 的条件，先用脚本筛出候选，再人工取舍。
- 用户关卡：把候选整理成一张表发给用户确认。表的列包括：代码、简称、主题、主动/指数、基金公司、规模、成立日、有无 A/C 份额、近两年是否换过经理、入选理由。用户确认后冻结 universe.yaml，再开始下载。
- AkShare 固定为 1.18.97。已知 fund_fee_em(indicator="申购费率") 返回空表：先换参数试，不行就从招募说明书的表格中解析。
- DATA_AS_OF 取下载时已收盘的最近一个交易日；如果执行时已过 2026-09-30 收盘，就取 2026-09-30。

【批次结束】
S2 验收通过、push 且 CI 全绿后，按 CLAUDE.md §1 第 8 条写 docs/HANDOFF.md，然后给用户一段不超过 10 行的汇报，并停下。
