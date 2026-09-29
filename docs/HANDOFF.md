# HANDOFF — B8 停在人工盲标关卡（2026-09-30）

写给下一个执行者对话（或用户带着盲标结果回来的同一批次）。已写进 CLAUDE.md / PLAN / DECISIONS / API 的内容只给指针。

## 1. 当前进度
- **B1–B7 完成；B8（S8 回答评测 + 第一期收尾）做到了「等用户盲标」**，还差三件事：盲标一致率 / kappa → README 与 PROGRESS 补数字 → 打 tag `v0.1-phase1` 并推送（CLAUDE.md §10：tag 只打 `v0.1-phase1`）。
- 已完成并入库的：统筹补充 1（复现性检查，`docs/tuning_log.md` run 11、12）、补充 2（费用关卡，用户已确认，实测 vs 估算见 `reports/answer_eval/20260929T171529Z/cost_actual_vs_estimate.md`）、全量回答评测（test 124 题 × 2 检索配置，248 次运行**全部成功**，无失败、无重试、无判分失败）、README 指标表（每个数字带链接和 summary.json 字段路径）、`docs/LIMITATIONS.md`（一期汇总 + 回答评测方法 9 条）、ADR-046、PROGRESS「S8 … 进行中」一节。
- 已 push；功能提交 `d024c82`，随后 `5dadd3c` 修了 scripts job 的 ruff（两个费用脚本的长行，第一次 CI run 36608044346 因此在 `scripts + security scan` 失败）。**CI run 36608275582（`5dadd3c`）7 个 job 全绿**。其后只有本文件 / PROGRESS 的文档提交。
- 测试：ai-service 194+（含 `tests/test_eval_scoring.py` 15 个）、backend 108、frontend 30、scripts 29，均未改动其它部分。

## 2. 下一步（用户把盲标表交回来之后）
1. 用户在 `reports/answer_eval/20260929T171529Z/blind_table/blind_table.xlsx` 最后一列填 0/1/2，告诉路径（表头有评分标准；36 行，候选池 72 条 text 类回答，种子 20260930；对照表在上一级 `blind_key.json`）。
2. 算一致率与 kappa（工作目录 `ai-service`）：
   `python -m fund_ai.eval.answer blind-score --dir ../reports/answer_eval/20260929T171529Z --table <用户的.xlsx>` → 写 `reports/answer_eval/20260929T171529Z/blind_agreement.json`（精确一致率、Cohen kappa（无权 / 线性加权）、「满分 vs 非满分」二值一致率与 kappa、混淆矩阵、逐行对照）。**报告时写明 n；裁判 35/36 给 2 分，kappa 在类别极不均衡时不稳定（kappa 悖论），要把一致率和混淆矩阵一起报**；如实写人与裁判的分歧方向（裁判偏宽还是偏严）。
3. 补进：README 「人工盲标」一行（现在写着「待补」，并链接了尚不存在的 `blind_agreement.json`）、PROGRESS S8 验收第 2 条与「实测数字」、LIMITATIONS S8-2 里的「结果见…」。把 PROGRESS S8 标题的「进行中」改为完成日期。
4. 跑 `scripts/security_scan.py` → commit → push → 确认 CI 全绿 → `git tag v0.1-phase1` → `git push origin v0.1-phase1`。README「当前进度」S8 一行改为 ✅。
5. 重写本 HANDOFF（B8 完成，下一批 B9 = S9 gRPC，PLAN §7）。

## 3. 如何拉起环境（回答评测部分；其它见 README、SETUP）
- 全栈仍是 compose 的 `app` profile（8 个容器 healthy，入口 <http://127.0.0.1:8088>）；**ticket-qa 的 5 个容器仍是停止状态**（见 §6-1）。
- 评测用的两个 ai-service 是**本机进程**（不是容器），已停。重跑：分别以 `RETRIEVAL_MODE=hybrid_rerank AI_SERVICE_PORT=8011 MCP_DOCS_URL=http://127.0.0.1:8011/mcp` 与 `RETRIEVAL_MODE=vector AI_SERVICE_PORT=8012 MCP_DOCS_URL=http://127.0.0.1:8012/mcp` 起 `python -m uvicorn fund_ai.api.app:app --port <端口>`（**用 PowerShell `Start-Process` 起、重定向日志，才不会被后台任务的 10 分钟超时杀掉**）；mcp-tools 用容器的 8101。命令与子命令见 `ai-service/src/fund_ai/eval/README.md`。`run` 对已有记录的题一律跳过（防止「重跑到成功」）。
- 重跑评测会重新花钱（本次全量实测费用上界约 $0.53）；**test 已经用掉**，任何改 Agent / 检索 / prompt 的改动之后再跑 test，都要按红线 2 登记（`docs/tuning_log.md` 风格），不要悄悄覆盖。

## 4. 会再踩的坑（B6/B7 的仍有效；下面是 B8 新增的）
- **命令分类器偶发不可用**：Bash / PowerShell 调用可能整体返回「auto mode classifier gave no verdict」，换一个工具（PowerShell）或稍后重试；期间可以做只读 / 编辑类工作。
- **Bash heredoc 里放中文会被截断**（B7 已记）：改文件一律用 Edit / Write；这次又遇到一次（`python - <<EOF` 里的中文替换串失效），已改用 Edit。
- **后台任务有 10 分钟超时**：长任务（评测 run、uvicorn）用 PowerShell `Start-Process -RedirectStandardOutput/-RedirectStandardError`（同一个参数不能写两遍）+ Monitor 等 `run_meta.json`；`run` 的进度打印被管道 `tail` 缓冲，别指望中途看到输出。
- **判分口径已冻结**（ADR-046）：numeric 主口径 = first，any 只作参考；list 主口径 = 标准项全部出现；多调用只算 no_tool；裁判 = deepseek-flash。要改口径必须新增 ADR 并说明是在看到 test 结果之后。
- **`git_dirty`**：`summary.json` 记录的是评测**判分时**工作区状态；改了文档后要先提交再 `score`（`score` 对已有 `scores.jsonl` 的题不会再调裁判，只重写 summary / report，不花钱）。
- 评测里 `summary.json` 的 `env.index.ingest_run` 取 `reports/ingest/` 里最新一个「全部成功」的入库 run（当前 `20260929T141806Z`）；重新入库后要用 `--ingest-run` 指定。
- 检索数字（S4 run 9）与 S8 的回答数字不在同一个索引上（重建后 BM25 路有小波动，LIMITATIONS S8-1）；对外不要逐题对照。

## 5. 已冻结的产物
| 产物 | 版本 / 值 | sha256 |
|---|---|---|
| `data/universe.yaml` | v1，20 只 | `7539d874374167f4954fef4bad46eb8b2232654972a6ab472242325aeec8692f` |
| DATA_AS_OF | 2026-09-28 | — |
| 数据快照 / 披露 PDF（不入库） | 12 张表 / 100 份 | 见 `data/MANIFEST.json`（sha256 `b5df59cb954c170e44071fb38603f57eff401863656912e410a96a721d49e127`） |
| `eval/datasets/fund_qa_v1.jsonl` | v1，112 题（dev 33 / test 79） | `77fb06a9686ea195ef8813d192bc90ffceb05ffe0b1122864777dc026997e48d` |
| `eval/datasets/agent_tasks_v1.jsonl` | v1，66 题（dev 21 / test 45） | `7d4c4fc3f4d6b63643fe608ca17c1ca07177257086253a1c878cf13bb33c3b77` |

**test 集已用掉**：S4 检索评测（run 9、10、复现 run 11）和 S8 回答评测（`reports/answer_eval/20260929T171529Z`）。dev 用于调参和费用小样（`cost_sample_dev_20260930`）。

## 6. 等用户 / 统筹处理的事
1. **用户（阻塞 B8 收尾）：人工盲标**，见 §2。标完之前不要看裁判分（`scores.jsonl`、`report.md`、`summary.json` 里有）。
2. **用户：ticket-qa 的 5 个容器仍是停止状态**（B7 起）。恢复：`wsl.exe -d Ubuntu-24.04 -u root -- docker start ticketqa-rabbitmq ticketqa-redis ticketqa-wiremock ticketqa-mysql ticketqa-prometheus`。
3. **统筹**：`docs/PLAN.md` §7 表把 B7、B8 标状态；CLAUDE.md §5 的 `JUDGE_MODEL` 那一行按用户决定改成了 deepseek-flash（执行者通常不改 CLAUDE.md）；前端「公共库文件清单页」仍没做（LIMITATIONS S6-7 / S7-4）。
4. 数据质量登记（未改动）：001551 销售服务费快照与招募说明书不一致（`reports/data_quality/20260929T045331Z/`）。
5. 已登记但未查明的真实错误：agent-0020（两个配置都答 21，gold 23）；vector 配置有 4 题因检索不到答错（qa-0008、0023、0047、0053）；hybrid_rerank 的 qa-0055 把 A/C 份额数字说反。逐条见 `reports/answer_eval/20260929T171529Z/numeric_any_vs_first.md`。
