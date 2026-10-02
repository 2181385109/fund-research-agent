"""语义缓存阈值校准（PLAN S10）：在 ``eval/datasets/cache_pairs_v1.jsonl`` 上量 precision / recall / 误命中率。

数据集的构造方式与局限见 ``scripts/build_cache_pairs.py``：同义改写对（应命中）、金融场景的难负例对（只差一个
关键要素，**不能**命中）、无关对（参考基线）。dev / test 按意图划分。

**选阈值的规则（在看到任何结果之前写死，红线 2；规则 B 是 dev 第一次扫描之后、实现守卫之前补充的，
理由见 ADR-048）**

- 在 dev 上扫描阈值网格（0.50–1.00，步长 0.005）。「复用一次缓存答案就等于答错」的难负例**一个都不能误命中**。
- 规则 A（只用向量相似度）：``t*_A = 网格上最小的、使 dev 难负例误命中数为 0 的阈值``。
  这是零误命中工作点；它对应的 recall 是多少就是多少，**不为了好看的 recall 往下调**。
- 规则 B（相似度 + 关键要素守卫，``fund_ai.cache.guard``）：命中 = 相似度 ≥ t **且**签名一致。
  ``t*_B = 网格上不低于 0.80 的最小阈值，使 dev 上（难负例 + 无关对）误命中数为 0``。下限 0.80 是预先写定的保守下限：
  低于它，句向量相似度不足以作为「同一个意思」的证据，把阈值压得更低只是把全部压力交给守卫的词典。
- 守卫的词典只依据 dev 调整（看 dev 上被误拒的同义改写和漏过的难负例）；**test 上不调整**。
- 诚实声明：词典是执行者在知道整套数据集设计的情况下写的，所以全量词典（配置 B）在 test 上**不是严格的样本外**。
  为此另报配置 **B_dev**：词典只保留「在 dev 问题里实际命中过」的概念（机械规则，``observed_concepts``），阈值同 B
  （两者在 dev 上的行为完全一致，所以阈值相同）。B_dev 在 test 上的误命中 / recall 才是
  对「没见过的区分维度」的样本外估计。
- test 只跑一次：``test`` 子命令读取 dev 的 t*_A 与 t*_B，在 test 上同时报告两种配置，并写下 ``TEST_RUN.json`` 标记；
  标记存在时拒绝再跑（要重跑必须显式 ``--allow-rerun``，且结果会标注为重跑，不得替换第一次的数字）。

命令（在 ``ai-service/`` 下）::

    python -m fund_ai.eval.cache_calibration dev
    python -m fund_ai.eval.cache_calibration test --dev-run ../reports/cache_calibration/<dev 目录>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from fund_ai.cache.guard import observed_concepts, signature
from fund_ai.cache.policy import normalize_question
from fund_ai.cache.store import cosine
from fund_ai.config import REPO_ROOT, get_settings
from fund_ai.embedding.factory import build_embedder
from fund_ai.retrieval.entity import FundEntityRecognizer, FundEntry

DATASET = REPO_ROOT / "eval" / "datasets" / "cache_pairs_v1.jsonl"
OUT_ROOT = REPO_ROOT / "reports" / "cache_calibration"
MARKER = OUT_ROOT / "TEST_RUN.json"
GRID = [round(0.50 + 0.005 * i, 3) for i in range(101)]  # 0.500 … 1.000


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """二项比例的 Wilson 95% 区间（小样本下比正态近似可靠）。"""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def universe_recognizer() -> FundEntityRecognizer:
    """用冻结的基金池（data/universe.yaml）建基金实体识别器；线上用 fund_data 里的同一批基金。"""
    u = yaml.safe_load((REPO_ROOT / "data" / "universe.yaml").read_text(encoding="utf-8"))
    return FundEntityRecognizer(
        FundEntry(
            fund_code=f["code"],
            fund_name=f["name"],
            full_name=f["name"],
            share_codes=tuple(sc["code"] for sc in f["share_classes"]),
        )
        for f in u["funds"]
    )


def load_pairs(split: str) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r["split"] == split]


def dev_concepts() -> frozenset[str]:
    """「只依据 dev 建立」的词典：dev 问题里词典实际命中过的概念（见 B_dev 配置）。"""
    return observed_concepts([q for p in load_pairs("dev") for q in (p["q1"], p["q2"])])


def score_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每对问题的余弦相似度（与线上一致：规范化 → embed_documents，不加查询指令）。"""
    emb = build_embedder(get_settings())
    qs = sorted({normalize_question(q) for p in pairs for q in (p["q1"], p["q2"])})
    vecs = dict(zip(qs, emb.embed_documents(qs), strict=True))
    rec = universe_recognizer().recognize
    dev_c = dev_concepts()
    out = []
    for p in pairs:
        a, b = vecs[normalize_question(p["q1"])], vecs[normalize_question(p["q2"])]
        sig1, sig2 = signature(p["q1"], rec), signature(p["q2"], rec)
        d1, d2 = signature(p["q1"], rec, dev_c), signature(p["q2"], rec, dev_c)
        out.append(
            {
                **p,
                "sim": cosine(a, b),
                "guard_ok": sig1 == sig2,
                "guard_ok_dev": d1 == d2,
                "guard_diff": {k: [list(x) for x in v] for k, v in sig1.diff(sig2).items()},
            }
        )
    return out


def metrics_at(
    scored: list[dict[str, Any]], t: float, guard: bool = False, key: str = "guard_ok"
) -> dict[str, Any]:
    """命中 = 相似度 ≥ t（guard=True 时还要求关键要素签名一致）。

    key 选哪套词典：guard_ok = 全量，guard_ok_dev = 只含 dev 问题里出现过的概念。
    """

    def hit(r: dict[str, Any]) -> bool:
        return r["sim"] >= t and (r[key] or not guard)

    pos = [r for r in scored if r["label"] == "same"]
    hard = [r for r in scored if r["category"] == "hard_negative"]
    unrel = [r for r in scored if r["category"] == "unrelated"]
    tp = sum(hit(r) for r in pos)
    fp_hard = sum(hit(r) for r in hard)
    fp_unrel = sum(hit(r) for r in unrel)
    return {
        "threshold": t,
        "guard": guard,
        "n_pos": len(pos),
        "n_hard_neg": len(hard),
        "n_unrelated": len(unrel),
        "tp": tp,
        "fn": len(pos) - tp,
        "fp_hard": fp_hard,
        "fp_unrelated": fp_unrel,
        "recall": tp / len(pos) if pos else None,
        "recall_ci95": wilson(tp, len(pos)),
        "precision_vs_hard": tp / (tp + fp_hard) if tp + fp_hard else None,
        "false_hit_rate_hard": fp_hard / len(hard) if hard else None,
        "false_hit_rate_hard_ci95": wilson(fp_hard, len(hard)),
        "false_hit_rate_all_neg": (fp_hard + fp_unrel) / (len(hard) + len(unrel))
        if hard or unrel
        else None,
    }


B_FLOOR = 0.80


def select_threshold(dev_scored: list[dict[str, Any]], guard: bool) -> dict[str, Any]:
    """规则见模块文档（A：只用相似度；B：相似度 + 守卫，阈值不低于 0.80，难负例与无关对都不能误命中）。"""
    for t in GRID:
        if guard and t < B_FLOOR:
            continue
        m = metrics_at(dev_scored, t, guard)
        if m["fp_hard"] == 0 and (not guard or m["fp_unrelated"] == 0):
            rule = (
                "B: min t>=0.80 with 0 dev false hits (hard + unrelated), similarity AND guard"
                if guard
                else "A: min t with 0 dev hard-negative false hits, similarity only"
            )
            return {"rule": rule, "threshold": t, "dev": m}
    raise RuntimeError("网格内没有满足规则的阈值：检查数据集是否有重复问题")


def _quantile(sorted_values: list[float], f: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(f * len(sorted_values)))]


def distribution(scored: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        by[r["category"]].append(r["sim"])
        if r["category"] == "hard_negative":
            by["hard_negative:" + r["subtype"].split(":")[0]].append(r["sim"])
    out = {}
    for k, v in sorted(by.items()):
        v.sort()
        out[k] = {
            "n": len(v),
            "min": v[0],
            "p25": _quantile(v, 0.25),
            "median": _quantile(v, 0.5),
            "p75": _quantile(v, 0.75),
            "max": v[-1],
        }
    return out


def per_group(
    scored: list[dict[str, Any]], t: float, key: str, guard: bool, gkey: str = "guard_ok"
) -> dict[str, Any]:
    g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        g[r[key]].append(r)

    def hit(r: dict[str, Any]) -> bool:
        return r["sim"] >= t and (r[gkey] or not guard)

    return {
        k: {
            "n_pos": sum(r["label"] == "same" for r in v),
            "tp": sum(r["label"] == "same" and hit(r) for r in v),
            "n_hard_neg": sum(r["category"] == "hard_negative" for r in v),
            "fp_hard": sum(r["category"] == "hard_negative" and hit(r) for r in v),
        }
        for k, v in sorted(g.items())
    }


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def env_block(split: str) -> dict[str, Any]:
    s = get_settings()
    manifest = REPO_ROOT / "data" / "MANIFEST.json"
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "dataset": DATASET.name,
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "data_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "data_as_of": json.loads(manifest.read_text(encoding="utf-8"))["data_as_of"],
        "embedding_model": s.embedding_model,
        "split": split,
        "machine": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "command": "cd ai-service && python -m fund_ai.eval.cache_calibration "
        + " ".join(sys.argv[1:]),
    }


def _cfg_block(
    scored: list[dict[str, Any]],
    sel: dict[str, Any] | None,
    t: float,
    guard: bool,
    key: str = "guard_ok",
) -> dict[str, Any]:
    return {
        "guard": guard,
        "guard_lexicon": key,
        "threshold": t,
        "selection": sel,
        "at_threshold": metrics_at(scored, t, guard, key),
        "by_subtype": per_group(scored, t, "subtype", guard, key),
        "by_intent": per_group(scored, t, "intent", guard, key),
    }


def render(summary: dict[str, Any]) -> str:
    env = summary["env"]
    split = summary["split"]
    cfgs = summary["configs"]
    any_m = next(iter(cfgs.values()))["at_threshold"]
    lines = [
        f"# 语义缓存阈值校准 — {split}（{summary['created_utc']}）",
        "",
        f"- 数据集：`{env['dataset']}` sha256 `{env['dataset_sha256'][:16]}…`；嵌入模型 `{env['embedding_model']}`；"
        f"git `{env['git_commit'][:10]}`（dirty={env['git_dirty']}）",
        f"- 本 split：同义改写对 n={any_m['n_pos']}，难负例对 n={any_m['n_hard_neg']}，无关对 n={any_m['n_unrelated']}",
        f"- 复现：`{env['command']}`",
        "",
    ]
    names = {
        "A": "A：只用向量相似度",
        "B": "B：相似度 + 关键要素守卫，全量词典（线上默认）",
        "B_dev": "B_dev：相似度 + 守卫，词典只含 dev 问题里出现过的概念（样本外估计）",
    }
    for key, cfg in cfgs.items():
        m = cfg["at_threshold"]
        origin = (
            "dev 上按预先写定的规则选出"
            if split == "dev"
            else f"取自 dev（{summary['threshold_from_dev_run']}），未在 test 上调整"
        )
        lines += [
            f"## 配置 {names[key]}：阈值 {m['threshold']}（{origin}）",
            "",
            "| 指标 | 值 | 分子 / 分母 | 95% 区间（Wilson） |",
            "|---|---|---|---|",
            f"| 同义改写命中率（recall） | {m['recall']:.3f} | {m['tp']} / {m['n_pos']} | {_ci(m['recall_ci95'])} |",
            f"| **难负例误命中率** | {m['false_hit_rate_hard']:.3f} | {m['fp_hard']} / {m['n_hard_neg']} | {_ci(m['false_hit_rate_hard_ci95'])} |",  # noqa: E501
            f"| 无关对误命中率 | {m['fp_unrelated'] / max(1, m['n_unrelated']):.3f} | {m['fp_unrelated']} / {m['n_unrelated']} | — |",  # noqa: E501
            f"| precision（对难负例） | {_f(m['precision_vs_hard'])} | {m['tp']} / {m['tp'] + m['fp_hard']} | — |",
            "",
        ]
    lines += [
        "## 相似度分布（余弦）",
        "",
        "| 类别 | n | min | p25 | median | p75 | max |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, d in summary["distribution"].items():
        lines.append(
            f"| {k} | {d['n']} | {d['min']:.3f} | {d['p25']:.3f} | {d['median']:.3f} | {d['p75']:.3f} | {d['max']:.3f} |"  # noqa: E501
        )
    g = summary["guard_alone"]
    lines += [
        "",
        "## 守卫单独的效果（不看相似度，即所有相似度都放行）",
        "",
        f"- 同义改写被守卫放行：{g['tp']} / {g['n_pos']}；"
        f"难负例被守卫放行（误命中）：{g['fp_hard']} / {g['n_hard_neg']}；"
        f"无关对被放行：{g['fp_unrelated']} / {g['n_unrelated']}",
        "",
        "词典只含 dev 问题里出现过的概念时（B_dev）："
        f"同义改写放行 {summary['guard_alone_dev_lexicon']['tp']} / {summary['guard_alone_dev_lexicon']['n_pos']}；"
        f"难负例放行（误命中）{summary['guard_alone_dev_lexicon']['fp_hard']} / {summary['guard_alone_dev_lexicon']['n_hard_neg']}；"  # noqa: E501
        f"无关对放行 {summary['guard_alone_dev_lexicon']['fp_unrelated']} / {summary['guard_alone_dev_lexicon']['n_unrelated']}",  # noqa: E501
        "",
        "## 各阈值的 recall / 误命中（步长 0.01，完整网格见 summary.json）",
        "",
        "| 阈值 | A recall | A 难负例误命中 | A 无关误命中 | B recall | B 难负例误命中 | B 无关误命中 |",
        "|---|---|---|---|---|---|---|",
    ]
    for ra, rb in zip(summary["sweep_sim_only"], summary["sweep_with_guard"], strict=True):
        if round(ra["threshold"] * 200) % 2 == 0:
            lines.append(
                f"| {ra['threshold']:.2f} | {ra['recall']:.3f} ({ra['tp']}/{ra['n_pos']}) | {ra['fp_hard']}/{ra['n_hard_neg']} | {ra['fp_unrelated']}/{ra['n_unrelated']} "  # noqa: E501
                f"| {rb['recall']:.3f} ({rb['tp']}/{rb['n_pos']}) | {rb['fp_hard']}/{rb['n_hard_neg']} | {rb['fp_unrelated']}/{rb['n_unrelated']} |"  # noqa: E501
            )
    cb = cfgs["B"]
    lines += [
        "",
        "## 配置 B 按类型：难负例误命中 / n，同义改写命中 / n",
        "",
        "| 类型 | 难负例误命中 / n | 同义命中 / n |",
        "|---|---|---|",
    ]
    for k, gg in cb["by_subtype"].items():
        lines.append(f"| {k} | {gg['fp_hard']} / {gg['n_hard_neg']} | {gg['tp']} / {gg['n_pos']} |")
    lines += [
        "",
        "## 配置 B 按意图",
        "",
        "| 意图 | 难负例误命中 / n | 同义命中 / n |",
        "|---|---|---|",
    ]
    for k, gg in cb["by_intent"].items():
        if not (
            gg["n_pos"] or gg["n_hard_neg"]
        ):  # 「意图 a|意图 b」的无关对分组没有同义 / 难负例，不列
            continue
        lines.append(f"| {k} | {gg['fp_hard']} / {gg['n_hard_neg']} | {gg['tp']} / {gg['n_pos']} |")
    return "\n".join(lines) + "\n"


def _ci(ci: list[float] | None) -> str:
    return "—" if ci is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"


def _f(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def _write(run_dir: Path, scored: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pair_scores.jsonl").write_text(
        "\n".join(json.dumps({**r, "sim": round(r["sim"], 6)}, ensure_ascii=False) for r in scored)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (run_dir / "report.md").write_text(render(summary), encoding="utf-8", newline="\n")


def _common(split: str, scored: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "split": split,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "env": env_block(split),
        "distribution": distribution(scored),
        "guard_alone": metrics_at(scored, 0.0, True),
        "guard_alone_dev_lexicon": metrics_at(scored, 0.0, True, "guard_ok_dev"),
        "sweep_sim_only": [metrics_at(scored, x, False) for x in GRID],
        "sweep_with_guard": [metrics_at(scored, x, True) for x in GRID],
    }


def run_dev() -> Path:
    scored = score_pairs(load_pairs("dev"))
    sel_a, sel_b = select_threshold(scored, guard=False), select_threshold(scored, guard=True)
    summary = {
        **_common("dev", scored),
        "configs": {
            "A": _cfg_block(scored, sel_a, sel_a["threshold"], False),
            "B": _cfg_block(scored, sel_b, sel_b["threshold"], True),
            "B_dev": _cfg_block(scored, sel_b, sel_b["threshold"], True, "guard_ok_dev"),
        },
        "dev_concepts": sorted(dev_concepts()),
    }
    run_dir = OUT_ROOT / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_dev")
    _write(run_dir, scored, summary)
    return run_dir


def run_test(dev_run: Path, allow_rerun: bool) -> Path:
    dev = json.loads((dev_run / "summary.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    if dev["env"]["dataset_sha256"] != sha:
        raise SystemExit("dev 结果对应的数据集与当前 cache_pairs_v1.jsonl 不一致，拒绝跑 test")
    if MARKER.exists() and not allow_rerun:
        raise SystemExit(f"test 已经跑过（{MARKER}）。红线 2：test 只跑一次；不要重跑。")
    ta, tb = dev["configs"]["A"]["threshold"], dev["configs"]["B"]["threshold"]
    scored = score_pairs(load_pairs("test"))
    summary = {
        **_common("test", scored),
        "threshold_from_dev_run": dev_run.name,
        "rerun": MARKER.exists(),
        "configs": {  # 阈值取自 dev；sweep 仅供对照，不据此改阈值
            "A": _cfg_block(scored, None, ta, False),
            "B": _cfg_block(scored, None, tb, True),
            "B_dev": _cfg_block(scored, None, tb, True, "guard_ok_dev"),
        },
        "dev_concepts": sorted(dev_concepts()),
    }
    run_dir = OUT_ROOT / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_test")
    _write(run_dir, scored, summary)
    if not MARKER.exists():
        MARKER.write_text(
            json.dumps(
                {
                    "run": run_dir.name,
                    "dataset_sha256": sha,
                    "thresholds": {"A": ta, "B": tb},
                    "note": "test 只跑一次；这个文件存在就表示已经用掉了",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return run_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dev")
    t = sub.add_parser("test")
    t.add_argument("--dev-run", type=Path, required=True)
    t.add_argument("--allow-rerun", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    out = run_dev() if args.cmd == "dev" else run_test(args.dev_run, args.allow_rerun)
    print(f"{args.cmd}: wrote {out}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
