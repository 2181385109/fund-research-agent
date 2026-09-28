你是 fund-research-agent 项目的执行者，项目目录是 D:\xiangmu\fund-research-agent。本对话负责批次 B<N>（具体包含哪些阶段见 docs/PLAN.md §7）。

开工前先读：CLAUDE.md 全文、docs/HANDOFF.md、docs/PLAN.md 中本批次各阶段的内容，以及 §0–§4 中与本批次相关的部分。只在需要时再查 PROGRESS 和 DECISIONS，不要通读。

先从 HANDOFF 指明的地方继续：
- 如果上一个对话没做完本批次，就从中断处接着做；
- 如果 HANDOFF 里有还在等用户处理的事，先把它们列给用户。

按分批连续模式推进（CLAUDE.md §1 第 7 条）：
- 本批次各阶段全部做完、push 且 CI 全绿后，重写 docs/HANDOFF.md，然后给用户一段不超过 10 行的汇报，并停下；
- 上下文太长时，推进到一个干净的提交点，写好 HANDOFF 再停下。
