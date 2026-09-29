# calc_fund_return 与参考脚本逐位比对

- 份额 40 个；用例共 4192 条（不含费 1522、含费 2670）；两边都判为无法计算（起点早于首个净值日）250 条；参与逐位比较 3942 条
- 全部字段逐位相同的用例：3942 / 3942
- 不一致：0 处（见 mismatches.json）

| 字段 | 逐位相同 | 参与比较 |
|---|---|---|
| start_used | 3942 | 3942 |
| end_used | 3942 | 3942 |
| ret | 3942 | 3942 |
| annualized | 3942 | 3942 |
| max_drawdown | 3942 | 3942 |
| dividends_in_range | 3942 | 3942 |
| net_ret_with_fees | 2465 | 2465 |

- 数据集 calc_return 题（gold_params，8 位小数）：匹配 9 / 9

复现：`cd eval && python ../scripts/verify_returns_vs_reference.py`（seed=20260929）
