# 语义缓存阈值校准集（cache_pairs_v1）

PLAN S10 要求的「阈值校准集」：一部分是同义改写对，另一部分是**金融场景的难负例对**（只差一个关键要素），
在不同阈值下报 precision / recall，据此选阈值。它**不是** PLAN §4.3 里的 fund_qa / agent_tasks 评测集，所以没有放进那份已冻结的
`MANIFEST.json`（改冻结的清单要经统筹和用户批准）；它的版本、来源和哈希记在这里。

| 项 | 值 |
|---|---|
| 文件 | `cache_pairs_v1.jsonl`，472 对 |
| sha256 | `ebca9f77ac47b8e4717d25155b06f6e6d9162e081f1bea3cc5af4b9c7fab5a8f` |
| 生成 | `python scripts/build_cache_pairs.py`（确定性：固定随机种子 + `data/universe.yaml` + 脚本里的模板与手写句，重跑 sha256 不变） |
| 划分 | 按**意图**划分：同一个意图的所有对只在 dev 或只在 test。dev 208 对（同义 72 / 难负例 104 / 无关 32），test 264 对（同义 92 / 难负例 132 / 无关 40） |
| 状态 | **test 已经用掉**（`reports/cache_calibration/TEST_RUN.json`）。再改数据集要升版本（v2）并重新校准，不能覆盖 v1 的结果 |
| 校准 / 结果 | `ai-service/src/fund_ai/eval/cache_calibration.py`、`reports/cache_calibration/`、`docs/perf/semantic_cache.md` |

## 字段

```json
{"id": "cp-0001", "version": "v1", "split": "dev | test",
 "label": "same | different",
 "category": "paraphrase | hard_negative | unrelated",
 "subtype": "wording | fund_alias | swap_order | hand_written | fund | slot:<槽位> | hand:<类型> | cross_intent",
 "intent": "fee_rate | …（意图名；unrelated 为「a|b」；手写句为 free）",
 "q1": "…", "q2": "…",
 "provenance": "template_generated | hand_written_claude"}
```

- `same`（`paraphrase`）：同一个问题的不同说法，**可以**复用缓存答案。子类型：`wording`（换措辞）、`fund_alias`（基金写全名 / 简称 / 代码）、
  `swap_order`（对比类问题交换两只基金的顺序）、`hand_written`（手写的自由说法）。
- `different` / `hard_negative`：只差**一个**关键要素，**不能**复用。子类型 `fund`（换成同公司 / 同主题 / 名字相近的另一只基金）、
  `slot:<槽位>`（改一个槽位：费用种类、份额类别、季度、年份、持有天数、第几大持仓、上下限、现任 / 前任、指标名、条款名……）、
  `hand:<类型>`（手写）。每个难负例里措辞相同和措辞不同各占一半。
- `different` / `unrelated`：同一个 split 内、不同意图的随机配对（参考基线，不是主要指标）。

## 来源与局限（诚实声明）

- **模板生成（`template_generated`）**：每个意图有 3–5 种措辞模板 × 真实基金池（`data/universe.yaml` 的 20 只基金，基金写全名 / 简称 / 代码）× 槽位取值；
  **模板措辞是执行者（Claude）写的，不是真实用户的提问分布**——真实用户的说法更杂、更口语、更含糊，同义改写对的难度很可能被低估。
- **手写（`hand_written_claude`）**：执行者手写的 20 对同义 + 20 对难负例，更口语、结构不拘一格（dev 8 + 8，test 12 + 12）。
- **标签由构造方式保证**（同槽位取值 = same，改一个槽位 = different），**没有人工审核**；个别「同义」对的措辞在语义上可能略有差别（例如「现任基金经理是谁」与「目前的基金经理」）。
- **已知缺陷（test 跑完之后才发现，没有重跑）**：基金简称以数字结尾的「天弘中证医药100」遇到以年份开头的槽位时，构建脚本的「代码后补空格」规则把简称末尾的 `100` 和年份前三位 `202` 误当成 6 位基金代码，生成了
  「天弘中证医药100202 6年」这样的畸形问题，共 8 对（dev 1：`cp-0071`；test 7：`cp-0188 cp-0199 cp-0222 cp-0303 cp-0315 cp-0330 cp-0332`）。数据集 v1 保持原样；官方数字含这 8 对，另在同一次运行的逐对相似度上排除它们重新计数（`addendum_excluding_malformed.*`，事后诊断）。v2 需要修掉。
- 基金池只有 20 只基金，问题只覆盖基金池内的事实类问法；**没有**覆盖多轮追问（带历史的提问本来就不进缓存）、池外基金、长问题、口误和错别字。
- 校准只量了「一对问题」的决策（相似度 + 守卫）；生产里缓存里可能有很多条，取 top-3 逐个过守卫，候选多了误命中的机会也多——**没有量**。
