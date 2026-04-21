"""对 pdfanno diff 输出做质量评估：对比 diff.json 与 ground_truth.json。

用法：
    python -m benchmarks.tools.evaluate DIFF.json GT.json --out REPORT.md

输出：
- confusion matrix（pred vs gt 的状态）
- per-status precision / recall / F1
- 位置准确率：当 status 都是 preserved/relocated 时，pdfanno 的 new_anchor 是否落在
  ground truth 指定的 (gt_page, gt_quad 中心 ± 15 pt) 内
- top failure modes（按 annotation 列出 pdfanno vs gt 不一致的案例）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

STATUSES = ("preserved", "relocated", "changed", "ambiguous", "broken", "unsupported")
LOCATION_THRESHOLD = 15.0  # PDF pt —— 与 match.QUAD_PROXIMITY_THRESHOLD 一致


def evaluate(diff: dict, gt: dict) -> dict:
    # 按 annotation_id 对齐
    gt_by_id = {lbl["annotation_id"]: lbl for lbl in gt["labels"]}
    diff_by_id = {r["annotation_id"]: r for r in diff["results"]}

    paired_ids = set(gt_by_id) & set(diff_by_id)
    missing_in_diff = set(gt_by_id) - set(diff_by_id)
    missing_in_gt = set(diff_by_id) - set(gt_by_id)

    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_status_counts = {s: {"tp": 0, "fp": 0, "fn": 0} for s in STATUSES}
    location_hits = 0
    location_attempts = 0
    failure_cases: list[dict] = []

    for aid in paired_ids:
        gt_lbl = gt_by_id[aid]
        diff_r = diff_by_id[aid]
        gt_status = gt_lbl["gt_status"]
        pred_status = diff_r["status"]
        confusion[(pred_status, gt_status)] += 1

        for s in STATUSES:
            if pred_status == s and gt_status == s:
                per_status_counts[s]["tp"] += 1
            elif pred_status == s and gt_status != s:
                per_status_counts[s]["fp"] += 1
            elif pred_status != s and gt_status == s:
                per_status_counts[s]["fn"] += 1

        # 位置准确率（只在 pred + gt 都 preserved/relocated 时评估）
        if (
            pred_status in ("preserved", "relocated")
            and gt_status in ("preserved", "relocated")
            and gt_lbl.get("gt_quad")
            and diff_r.get("new_anchor")
            and diff_r["new_anchor"].get("quads")
        ):
            gt_center = _quad_center(gt_lbl["gt_quad"])
            pred_center = _quad_center(diff_r["new_anchor"]["quads"][0])
            same_page = diff_r["new_anchor"]["page_index"] == gt_lbl["gt_page"]
            dist = (
                (gt_center[0] - pred_center[0]) ** 2 + (gt_center[1] - pred_center[1]) ** 2
            ) ** 0.5
            location_attempts += 1
            if same_page and dist < LOCATION_THRESHOLD:
                location_hits += 1

        # 失败记录
        if pred_status != gt_status or (
            pred_status in ("preserved", "relocated")
            and gt_lbl.get("gt_page") is not None
            and diff_r.get("new_anchor")
            and diff_r["new_anchor"].get("page_index") != gt_lbl["gt_page"]
        ):
            failure_cases.append(
                {
                    "annotation_id": aid,
                    "selected_text": gt_lbl["selected_text"][:60],
                    "pred_status": pred_status,
                    "gt_status": gt_status,
                    "pred_page": (
                        diff_r["new_anchor"]["page_index"] if diff_r.get("new_anchor") else None
                    ),
                    "gt_page": gt_lbl.get("gt_page"),
                    "gt_reason": gt_lbl.get("gt_reason", ""),
                    "pred_confidence": diff_r.get("confidence"),
                    "pred_context_sim": (
                        diff_r.get("match_reason", {}).get("context_similarity")
                        if diff_r.get("match_reason")
                        else None
                    ),
                }
            )

    totals = {s: sum(per_status_counts[s].values()) for s in STATUSES}
    metrics: dict[str, dict] = {}
    for s in STATUSES:
        tp = per_status_counts[s]["tp"]
        fp = per_status_counts[s]["fp"]
        fn = per_status_counts[s]["fn"]
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
        metrics[s] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    # overall accuracy = (sum tp) / (总配对数)
    total_tp = sum(per_status_counts[s]["tp"] for s in STATUSES)
    overall_accuracy = total_tp / len(paired_ids) if paired_ids else 0.0

    return {
        "n_total_gt": len(gt_by_id),
        "n_total_diff": len(diff_by_id),
        "n_paired": len(paired_ids),
        "n_missing_in_diff": len(missing_in_diff),
        "n_missing_in_gt": len(missing_in_gt),
        "overall_accuracy": overall_accuracy,
        "location_accuracy": (location_hits / location_attempts if location_attempts else None),
        "location_hits": location_hits,
        "location_attempts": location_attempts,
        "confusion_matrix": _confusion_to_nested(confusion),
        "per_status": metrics,
        "status_totals": totals,
        "failure_cases": failure_cases,
    }


def render_markdown(eval_result: dict, diff_path: str, gt_path: str) -> str:
    lines: list[str] = []
    lines.append("# Evaluation report\n")
    lines.append(f"- diff: `{diff_path}`")
    lines.append(f"- ground truth: `{gt_path}`")
    lines.append(
        f"- total annotations: gt={eval_result['n_total_gt']}, "
        f"diff={eval_result['n_total_diff']}, "
        f"paired={eval_result['n_paired']}"
    )
    acc = eval_result["overall_accuracy"]
    lines.append(f"- **overall accuracy (status match)**: {acc:.1%}")
    loc_acc = eval_result["location_accuracy"]
    if loc_acc is not None:
        lines.append(
            f"- **location accuracy** (predicted quad center within "
            f"{LOCATION_THRESHOLD} pt of gt center, same page): "
            f"{loc_acc:.1%} "
            f"({eval_result['location_hits']}/{eval_result['location_attempts']})"
        )

    # Confusion matrix
    lines.append("\n## Confusion matrix\n")
    lines.append("| pred \\ gt | " + " | ".join(STATUSES) + " |")
    lines.append("|---" * (len(STATUSES) + 1) + "|")
    for pred in STATUSES:
        row = [pred]
        for gt in STATUSES:
            row.append(str(eval_result["confusion_matrix"].get(pred, {}).get(gt, 0)))
        lines.append("| " + " | ".join(row) + " |")

    # Per-status metrics
    lines.append("\n## Per-status precision / recall / F1\n")
    lines.append("| status | tp | fp | fn | precision | recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for s in STATUSES:
        m = eval_result["per_status"][s]
        lines.append(
            f"| {s} | {m['tp']} | {m['fp']} | {m['fn']} | "
            f"{_fmt_pct(m['precision'])} | {_fmt_pct(m['recall'])} | {_fmt_pct(m['f1'])} |"
        )

    # Failures
    fcs = eval_result["failure_cases"]
    lines.append(f"\n## Failure cases ({len(fcs)})\n")
    if fcs:
        lines.append(
            "| id | text | pred_status | gt_status | pred_p | gt_p | conf | ctx_sim | gt_reason |"
        )
        lines.append("|---|---|---|---|---:|---:|---:|---:|---|")
        for f in fcs[:20]:
            lines.append(
                f"| {f['annotation_id'][:12]} "
                f"| {f['selected_text'][:40]!r} "
                f"| {f['pred_status']} | {f['gt_status']} "
                f"| {f['pred_page']} | {f['gt_page']} "
                f"| {_fmt_pct(f['pred_confidence'])} | {_fmt_pct(f['pred_context_sim'])} "
                f"| {f['gt_reason'][:60]} |"
            )
        if len(fcs) > 20:
            lines.append(f"\n*(... {len(fcs) - 20} more failure cases)*")
    else:
        lines.append("*(none)*")

    # Warnings
    if eval_result["n_missing_in_diff"] or eval_result["n_missing_in_gt"]:
        lines.append("\n## Warnings")
        if eval_result["n_missing_in_diff"]:
            lines.append(
                f"- {eval_result['n_missing_in_diff']} annotations in ground truth "
                f"but not in diff results"
            )
        if eval_result["n_missing_in_gt"]:
            lines.append(
                f"- {eval_result['n_missing_in_gt']} annotations in diff results "
                f"but not in ground truth"
            )

    return "\n".join(lines) + "\n"


def _confusion_to_nested(
    confusion: dict[tuple[str, str], int],
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for (pred, gt), n in confusion.items():
        out.setdefault(pred, {})[gt] = n
    return out


def _quad_center(quad: list[float]) -> tuple[float, float]:
    xs = quad[0::2]
    ys = quad[1::2]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.1%}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diff", type=Path, help="pdfanno diff JSON")
    parser.add_argument("gt", type=Path, help="ground_truth.json")
    parser.add_argument("--out", type=Path, help="Write markdown report to this path")
    parser.add_argument("--json-out", type=Path, help="Write raw JSON metrics")
    args = parser.parse_args()

    diff = json.loads(args.diff.read_text(encoding="utf-8"))
    gt = json.loads(args.gt.read_text(encoding="utf-8"))
    result = evaluate(diff, gt)

    md = render_markdown(result, str(args.diff), str(args.gt))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"wrote markdown report to {args.out}")
    else:
        print(md)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote raw metrics to {args.json_out}")

    # 简短 stdout 总结
    print(
        f"overall accuracy: {result['overall_accuracy']:.1%}  "
        f"paired={result['n_paired']}  failures={len(result['failure_cases'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
