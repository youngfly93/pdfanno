"""对比当前 eval 与 baseline 快照 —— 用于 v0.2.1 之后每次改动的回归防线。

用法：
    python -m benchmarks.tools.compare_baseline \
        benchmarks/baselines/v0.2.0.json \
        CURRENT_ARXIV.json CURRENT_ARXIV_GT.json arxiv_1706.03762_v1_v5 \
        CURRENT_REV.json CURRENT_REV_GT.json revised_synthetic

或更简洁：把当前 eval 先写成快照再 diff。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.tools.evaluate import evaluate


def _load(p: str | Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _current_snapshot(pairs: list[tuple[str, str, str]]) -> dict:
    """`pairs` 是 [(name, diff_path, gt_path), ...]。返回 name -> 关键数字字典。"""

    out: dict[str, dict] = {}
    for name, diff_path, gt_path in pairs:
        r = evaluate(_load(diff_path), _load(gt_path))
        out[name] = {
            "overall_accuracy": round(r["overall_accuracy"], 4),
            "location_accuracy": (
                round(r["location_accuracy"], 4) if r["location_accuracy"] is not None else None
            ),
            "failure_count": len(r["failure_cases"]),
        }
    return out


def compare(baseline_path: str | Path, current: dict) -> tuple[bool, list[str]]:
    """返回 (is_regression, lines)。lines 是人类可读的对比报告。"""

    baseline = _load(baseline_path)
    base_bench = baseline["benchmarks"]
    lines: list[str] = [f"baseline: {baseline['version']} @ {baseline.get('commit', '?')}"]
    regressed = False
    for name, cur in current.items():
        base = base_bench.get(name)
        if base is None:
            lines.append(f"  {name}: no baseline entry (new benchmark)")
            continue
        oa_base, oa_cur = base["overall_accuracy"], cur["overall_accuracy"]
        loc_base, loc_cur = base["location_accuracy"], cur["location_accuracy"]
        fc_base, fc_cur = base["failure_count"], cur["failure_count"]
        lines.append(f"  {name}:")
        lines.append(f"    overall : {oa_base:.4f} -> {oa_cur:.4f}  (Δ={oa_cur - oa_base:+.4f})")
        if loc_base is not None and loc_cur is not None:
            lines.append(
                f"    location: {loc_base:.4f} -> {loc_cur:.4f}  (Δ={loc_cur - loc_base:+.4f})"
            )
        lines.append(f"    failures: {fc_base} -> {fc_cur}")
        if oa_cur < oa_base - 1e-4:
            regressed = True
            lines.append("    ⚠️  status accuracy regressed")
        if loc_base is not None and loc_cur is not None and loc_cur < loc_base - 1e-4:
            regressed = True
            lines.append("    ⚠️  location accuracy regressed")
    return regressed, lines


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("baseline", type=Path)
    ap.add_argument(
        "triples",
        nargs="+",
        help="(diff_path gt_path name) 三个一组，至少一组",
    )
    args = ap.parse_args()

    if len(args.triples) % 3 != 0:
        print("triples 必须是 3 的倍数：diff_path gt_path name ...", file=sys.stderr)
        return 2

    pairs = [
        (args.triples[i + 2], args.triples[i], args.triples[i + 1])
        for i in range(0, len(args.triples), 3)
    ]
    current = _current_snapshot(pairs)
    regressed, lines = compare(args.baseline, current)
    for line in lines:
        print(line)
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(_main())
