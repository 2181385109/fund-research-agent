"""配对 bootstrap（PLAN §5 S4 主对比）：同一批题上两种配置的逐题指标差的均值及 95% 置信区间。

对题目下标有放回抽样 n_boot 次，每次计算 mean(b − a)；CI 取 2.5% / 97.5% 分位（百分位法）。
固定随机种子，结果可复现。另报「差 ≤ 0 的重抽样比例」作为单侧参考。
"""

from __future__ import annotations

import random


def paired_bootstrap(
    a: list[float], b: list[float], n_boot: int = 10000, seed: int = 20260929
) -> dict:
    if len(a) != len(b):
        raise ValueError("配对样本长度不一致")
    n = len(a)
    if n == 0:
        return {"n": 0}
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[min(n_boot - 1, int(0.975 * n_boot))]
    return {
        "n": n,
        "mean_a": sum(a) / n,
        "mean_b": sum(b) / n,
        "mean_diff": sum(diffs) / n,
        "ci95": [lo, hi],
        "p_diff_le_0": sum(m <= 0 for m in means) / n_boot,
        "n_boot": n_boot,
        "seed": seed,
    }
