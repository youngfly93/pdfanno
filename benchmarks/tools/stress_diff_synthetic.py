"""Build and run a broad synthetic stress corpus for `pdfanno diff`.

The corpus is intentionally synthetic and deterministic. It complements the
real-paper benchmarks by covering edge cases that are hard to source from
public PDFs: unsupported annotations, tight line spacing, repeated short tokens,
rotated pages, multi-line quads, far page movement, and high annotation volume.

Usage:
    python -m benchmarks.tools.stress_diff_synthetic \
        --out-root /tmp/pdfanno-stress \
        --report benchmarks/reports/stress_diff_synthetic.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdfanno.diff.anchors import extract_anchors
from pdfanno.diff.match import diff_against
from pdfanno.diff.types import DiffReport, DiffResult
from pdfanno.pdf_core.document import compute_doc_id
from pdfanno.pdf_core.text import normalize_text

PAGE_W = 595
PAGE_H = 842
DEFAULT_PAGE_WINDOW = 3
LOCATION_STATUSES = {"preserved", "relocated"}


@dataclass(frozen=True)
class Expectation:
    note: str
    status: str
    page: int | None = None
    selected_text: str | None = None
    description: str = ""
    known_hard: bool = False


@dataclass(frozen=True)
class Scenario:
    name: str
    old_pdf: Path
    new_pdf: Path
    expectations: list[Expectation]
    description: str


@dataclass(frozen=True)
class Failure:
    scenario: str
    note: str
    kind: str
    expected: str
    actual: str
    detail: str
    known_hard: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, default=Path("/tmp/pdfanno-stress-synthetic"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("benchmarks/reports/stress_diff_synthetic.md"),
    )
    parser.add_argument("--json-out", type=Path, help="Optional raw metrics JSON path.")
    parser.add_argument("--page-window", type=int, default=DEFAULT_PAGE_WINDOW)
    args = parser.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)
    scenarios = build_corpus(args.out_root)
    run = run_corpus(scenarios, args.page_window)
    probes = run_behavior_probes(args.out_root)
    markdown = render_markdown(scenarios, run, probes, args.out_root, args.page_window)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown, encoding="utf-8")
    print(f"wrote stress report to {args.report}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "out_root": str(args.out_root),
            "page_window": args.page_window,
            "summary": run["summary"],
            "failures": [f.__dict__ for f in run["failures"]],
            "probes": probes,
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote raw stress metrics to {args.json_out}")

    return 0


def build_corpus(out_root: Path) -> list[Scenario]:
    builders = [
        build_basic_mixed,
        build_page_insert_shift,
        build_same_page_geometry,
        build_two_column_reorder,
        build_multiline_quad,
        build_tight_layout,
        build_repeated_short_tokens,
        build_punctuation_hyphenation,
        build_case_only_edit,
        build_near_duplicate_deleted,
        build_rotated_page,
        build_annotation_kinds,
        build_unsupported_annotations,
        build_far_page_movement,
        build_no_annotations,
        build_high_volume,
    ]
    return [builder(out_root / builder.__name__.replace("build_", "")) for builder in builders]


def run_corpus(scenarios: list[Scenario], page_window: int) -> dict:
    failures: list[Failure] = []
    scenario_rows: list[dict] = []
    total_expected = 0
    total_runtime = 0.0

    for scenario in scenarios:
        start = time.perf_counter()
        try:
            report = run_diff(scenario.old_pdf, scenario.new_pdf, page_window=page_window)
        except Exception as exc:  # pragma: no cover - stress tool should capture all failures
            failures.append(
                Failure(
                    scenario=scenario.name,
                    note="-",
                    kind="crash",
                    expected="diff report",
                    actual=type(exc).__name__,
                    detail=repr(exc),
                )
            )
            scenario_rows.append(
                {
                    "name": scenario.name,
                    "expected": len(scenario.expectations),
                    "results": 0,
                    "status": "crashed",
                    "mismatches": 1,
                    "runtime_ms": (time.perf_counter() - start) * 1000,
                    "summary": {},
                }
            )
            continue

        elapsed_ms = (time.perf_counter() - start) * 1000
        total_runtime += elapsed_ms
        write_diff_json(scenario, report)

        expected_by_note = {exp.note: exp for exp in scenario.expectations}
        results_by_note = {result.old_anchor.note: result for result in report.results}
        scenario_failures_before = len(failures)
        total_expected += len(expected_by_note)

        for note, exp in expected_by_note.items():
            result = results_by_note.get(note)
            if result is None:
                failures.append(
                    Failure(
                        scenario=scenario.name,
                        note=note,
                        kind="missing_result",
                        expected=exp.status,
                        actual="-",
                        detail="expected annotation note was not present in diff results",
                        known_hard=exp.known_hard,
                    )
                )
                continue
            compare_result(scenario.name, exp, result, failures)

        for note, result in results_by_note.items():
            if note not in expected_by_note:
                failures.append(
                    Failure(
                        scenario=scenario.name,
                        note=note or "-",
                        kind="unexpected_result",
                        expected="-",
                        actual=result.status,
                        detail="diff produced an annotation note not listed in expectations",
                    )
                )

        scenario_rows.append(
            {
                "name": scenario.name,
                "expected": len(scenario.expectations),
                "results": len(report.results),
                "status": "ok",
                "mismatches": len(failures) - scenario_failures_before,
                "runtime_ms": elapsed_ms,
                "summary": report.summary.model_dump(mode="json"),
            }
        )

    summary = {
        "scenarios": len(scenarios),
        "expected_annotations": total_expected,
        "failures": len(failures),
        "known_hard_failures": sum(1 for f in failures if f.known_hard),
        "runtime_ms": total_runtime,
    }
    return {"summary": summary, "rows": scenario_rows, "failures": failures}


def compare_result(
    scenario_name: str,
    exp: Expectation,
    result: DiffResult,
    failures: list[Failure],
) -> None:
    if result.status != exp.status:
        failures.append(
            Failure(
                scenario=scenario_name,
                note=exp.note,
                kind="status",
                expected=exp.status,
                actual=result.status,
                detail=_result_detail(result),
                known_hard=exp.known_hard,
            )
        )

    if exp.page is not None and result.status in LOCATION_STATUSES:
        actual_page = result.new_anchor.page_index if result.new_anchor else None
        if actual_page != exp.page:
            failures.append(
                Failure(
                    scenario=scenario_name,
                    note=exp.note,
                    kind="page",
                    expected=str(exp.page),
                    actual=str(actual_page),
                    detail=_result_detail(result),
                    known_hard=exp.known_hard,
                )
            )

    if exp.selected_text is not None:
        actual = normalize_text(result.old_anchor.selected_text)
        expected = normalize_text(exp.selected_text)
        if actual != expected:
            failures.append(
                Failure(
                    scenario=scenario_name,
                    note=exp.note,
                    kind="selected_text",
                    expected=expected,
                    actual=actual,
                    detail="old anchor selected_text differs from fixture target",
                    known_hard=exp.known_hard,
                )
            )


def run_behavior_probes(out_root: Path) -> list[dict]:
    """Run targeted probes that are not strict pass/fail scenario expectations."""

    scenario = build_far_page_movement(out_root / "probe_far_page_movement")
    rows: list[dict] = []
    for page_window in (0, 1, 3, 8):
        report = run_diff(scenario.old_pdf, scenario.new_pdf, page_window=page_window)
        result = report.results[0]
        rows.append(
            {
                "probe": "far_page_movement",
                "page_window": page_window,
                "status": result.status,
                "new_page": result.new_anchor.page_index if result.new_anchor else None,
                "confidence": result.confidence,
                "message": result.message,
            }
        )
    return rows


def run_diff(old_pdf: Path, new_pdf: Path, *, page_window: int) -> DiffReport:
    with pymupdf.open(old_pdf) as old_doc:
        old_doc_id = compute_doc_id(old_doc, old_pdf)
        anchors = extract_anchors(old_doc, old_doc_id)
    with pymupdf.open(new_pdf) as new_doc:
        new_doc_id = compute_doc_id(new_doc, new_pdf)
        return diff_against(anchors, new_doc, new_doc_id, page_window=page_window)


def write_diff_json(scenario: Scenario, report: DiffReport) -> None:
    path = scenario.old_pdf.parent / "diff.json"
    payload = report.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def render_markdown(
    scenarios: list[Scenario],
    run: dict,
    probes: list[dict],
    out_root: Path,
    page_window: int,
) -> str:
    failures: list[Failure] = run["failures"]
    fail_by_kind = Counter(f.kind for f in failures)
    summary = run["summary"]
    lines = [
        "# Synthetic diff stress report",
        "",
        f"- corpus root: `{out_root}`",
        f"- scenarios: {summary['scenarios']}",
        f"- expected annotations: {summary['expected_annotations']}",
        f"- page_window: {page_window}",
        f"- runtime: {summary['runtime_ms']:.1f} ms",
        f"- total findings: {summary['failures']}",
        f"- known-hard findings: {summary['known_hard_failures']}",
        "",
        "## Scenario coverage",
        "",
        "| scenario | annotations | results | status counts | findings | runtime |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for row in run["rows"]:
        counts = _summary_counts(row["summary"])
        lines.append(
            f"| {row['name']} | {row['expected']} | {row['results']} | {counts} | "
            f"{row['mismatches']} | {row['runtime_ms']:.1f} ms |"
        )

    lines.extend(["", "## Finding types", ""])
    if fail_by_kind:
        lines.extend(["| type | count |", "|---|---:|"])
        for kind, count in sorted(fail_by_kind.items()):
            lines.append(f"| {kind} | {count} |")
    else:
        lines.append("No findings.")

    lines.extend(["", "## Findings", ""])
    if failures:
        lines.extend(
            [
                "| scenario | note | type | expected | actual | known hard | detail |",
                "|---|---|---|---|---|---:|---|",
            ]
        )
        for failure in failures[:80]:
            lines.append(
                f"| {failure.scenario} | `{failure.note}` | {failure.kind} | "
                f"{failure.expected} | {failure.actual} | "
                f"{'yes' if failure.known_hard else 'no'} | {_escape_cell(failure.detail)} |"
            )
        if len(failures) > 80:
            lines.append(f"\nOmitted {len(failures) - 80} additional findings.")
    else:
        lines.append("No mismatches found against synthetic expectations.")

    lines.extend(["", "## Behavior probes", ""])
    lines.extend(["| probe | page_window | status | new_page | confidence | message |"])
    lines.extend(["|---|---:|---|---:|---:|---|"])
    for probe in probes:
        lines.append(
            f"| {probe['probe']} | {probe['page_window']} | {probe['status']} | "
            f"{probe['new_page']} | {probe['confidence']:.3f} | {_escape_cell(probe['message'])} |"
        )

    lines.extend(["", "## Scenario notes", ""])
    for scenario in scenarios:
        lines.append(f"- `{scenario.name}`: {scenario.description}")

    lines.extend(
        [
            "",
            "## Initial interpretation",
            "",
            "- The `unsupported_annotations` scenario asserts that non-text annotations produce the advertised `unsupported` status.",
            "- The `case_only_edit` scenario asserts that PyMuPDF's ASCII case-insensitive `search_for` behavior does not turn case-only edits into `preserved`.",
            "- The `high_volume` scenario guards against fuzzy false positives on near-duplicate deleted text. Deleted numbered sentences must not reuse surviving exact anchors' text slots.",
            "- Treat `known hard` repeated-token findings separately from regressions. These are the Week 6-11 scorer boundary cases made reproducible in a small fixture.",
            "- The far-page probe checks whether `page_window` behaves as a hard search window or only as a scoring input.",
        ]
    )
    return "\n".join(lines) + "\n"


def _summary_counts(summary: dict) -> str:
    if not summary:
        return "-"
    parts = []
    for key in ("preserved", "relocated", "changed", "ambiguous", "broken", "unsupported"):
        value = summary.get(key, 0)
        if value:
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "-"


def _result_detail(result: DiffResult) -> str:
    page = result.new_anchor.page_index if result.new_anchor else None
    ctx = result.match_reason.context_similarity if result.match_reason else None
    layout = result.match_reason.layout_score if result.match_reason else None
    return (
        f"status={result.status}, page={page}, conf={result.confidence:.3f}, "
        f"ctx={ctx}, layout={layout}, message={result.message}"
    )


def _escape_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


# ----- PDF builders -----------------------------------------------------


def build_basic_mixed(out_dir: Path) -> Scenario:
    old = _new_doc(2)
    page = old[0]
    _line(page, "Alpha preserved kinase marker stays unchanged in v2.", 72, 90)
    _line(page, "Beta relocation sentence moves to the second page.", 72, 120)
    _line(page, "Gamma expression increased by 2.4 fold after treatment.", 72, 150)
    _line(page, "Delta obsolete claim is removed from the revision.", 72, 180)
    _mark(page, "Alpha preserved kinase marker stays unchanged in v2.", "BM_PRE")
    _mark(page, "Beta relocation sentence moves to the second page.", "BM_REL")
    _mark(page, "Gamma expression increased by 2.4 fold after treatment.", "BM_CHG")
    _mark(page, "Delta obsolete claim is removed from the revision.", "BM_BRK")

    new = _new_doc(2)
    _line(new[0], "Alpha preserved kinase marker stays unchanged in v2.", 72, 90)
    _line(new[0], "Gamma expression increased by 3.1 fold after treatment.", 72, 150)
    _line(new[1], "Beta relocation sentence moves to the second page.", 72, 160)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "basic_mixed",
        old_pdf,
        new_pdf,
        [
            Expectation(
                "BM_PRE", "preserved", 0, "Alpha preserved kinase marker stays unchanged in v2."
            ),
            Expectation(
                "BM_REL", "relocated", 1, "Beta relocation sentence moves to the second page."
            ),
            Expectation(
                "BM_CHG", "changed", 0, "Gamma expression increased by 2.4 fold after treatment."
            ),
            Expectation(
                "BM_BRK", "broken", None, "Delta obsolete claim is removed from the revision."
            ),
        ],
        "single-column preserved / relocated / changed / broken baseline",
    )


def build_page_insert_shift(out_dir: Path) -> Scenario:
    old = _new_doc(2)
    texts = [
        "Insert shift anchor one stays textually identical after a cover page is added.",
        "Insert shift anchor two moves from page two to page three.",
        "Insert shift anchor three also moves by exactly one page.",
    ]
    _line(old[0], texts[0], 72, 90)
    _line(old[1], texts[1], 72, 90)
    _line(old[1], texts[2], 72, 130)
    for i, text in enumerate(texts):
        _mark(old[0 if i == 0 else 1], text, f"PIS_{i}")

    new = _new_doc(3)
    _line(new[0], "New cover page inserted before the annotated manuscript body.", 72, 90)
    _line(new[1], texts[0], 72, 90)
    _line(new[2], texts[1], 72, 90)
    _line(new[2], texts[2], 72, 130)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "page_insert_shift",
        old_pdf,
        new_pdf,
        [
            Expectation("PIS_0", "relocated", 1, texts[0]),
            Expectation("PIS_1", "relocated", 2, texts[1]),
            Expectation("PIS_2", "relocated", 2, texts[2]),
        ],
        "whole-document page insertion shifts all targets by one page",
    )


def build_same_page_geometry(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    texts = {
        "SG_SHIFT": "Same page shift target moves far enough to be relocation.",
        "SG_NEAR": "Near shift target moves less than the proximity threshold.",
        "SG_COLUMN": "Column transfer target moves from left to right.",
    }
    _line(old[0], texts["SG_SHIFT"], 72, 100)
    _line(old[0], texts["SG_NEAR"], 72, 170)
    _line(old[0], texts["SG_COLUMN"], 72, 240)
    for note, text in texts.items():
        _mark(old[0], text, note)

    new = _new_doc(1)
    _line(new[0], texts["SG_SHIFT"], 72, 250)
    _line(new[0], texts["SG_NEAR"], 72, 178)
    _line(new[0], texts["SG_COLUMN"], 330, 240)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "same_page_geometry",
        old_pdf,
        new_pdf,
        [
            Expectation("SG_SHIFT", "relocated", 0, texts["SG_SHIFT"]),
            Expectation("SG_NEAR", "preserved", 0, texts["SG_NEAR"]),
            Expectation("SG_COLUMN", "relocated", 0, texts["SG_COLUMN"]),
        ],
        "same-page y-shift, near-threshold shift, and column transfer",
    )


def build_two_column_reorder(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    left = "Left column anchor crosses right."
    right = "Right column anchor crosses left."
    lower = "Lower column anchor moves upward."
    _line(old[0], left, 54, 100)
    _line(old[0], right, 315, 100)
    _line(old[0], lower, 54, 180)
    _mark(old[0], left, "TC_LEFT")
    _mark(old[0], right, "TC_RIGHT")
    _mark(old[0], lower, "TC_LOWER")

    new = _new_doc(1)
    _line(new[0], right, 54, 100)
    _line(new[0], left, 315, 100)
    _line(new[0], lower, 315, 140)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "two_column_reorder",
        old_pdf,
        new_pdf,
        [
            Expectation("TC_LEFT", "relocated", 0, left),
            Expectation("TC_RIGHT", "relocated", 0, right),
            Expectation("TC_LOWER", "relocated", 0, lower),
        ],
        "two-column x/y relocation without changing text",
    )


def build_multiline_quad(out_dir: Path) -> Scenario:
    old = _new_doc(2)
    phrase_a = "adaptive threshold calibration keeps rare variant evidence together"
    phrase_b = "multi line relocation target wraps before moving to a later page"
    _textbox(
        old[0],
        "The paragraph begins before "
        + phrase_a
        + " across a wrapped line and then continues after the annotation.",
        pymupdf.Rect(72, 90, 260, 210),
    )
    _textbox(
        old[0],
        "A second paragraph contains " + phrase_b + " with enough words to force wrapping.",
        pymupdf.Rect(72, 250, 260, 370),
    )
    _mark(old[0], phrase_a, "ML_PRE", all_quads=True)
    _mark(old[0], phrase_b, "ML_REL", all_quads=True)

    new = _new_doc(2)
    _textbox(
        new[0],
        "The paragraph begins before "
        + phrase_a
        + " across a wrapped line and then continues after the annotation.",
        pymupdf.Rect(72, 90, 260, 210),
    )
    _textbox(
        new[1],
        "A relocated paragraph contains " + phrase_b + " with enough words to force wrapping.",
        pymupdf.Rect(72, 130, 260, 260),
    )

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "multiline_quad",
        old_pdf,
        new_pdf,
        [
            Expectation("ML_PRE", "preserved", 0, phrase_a),
            Expectation("ML_REL", "relocated", 1, phrase_b),
        ],
        "phrases whose highlights consist of multiple quads across wrapped lines",
    )


def build_tight_layout(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    targets = {
        "TL_0": "neural network language models",
        "TL_1": "vector representations",
        "TL_2": "probabilistic objective",
    }
    lines = [
        "pared to the previous baseline using narrow spacing",
        "neural network language models improve nearest neighbor retrieval",
        "mputational costs are still bounded in this synthetic sample",
        "vector representations remain stable under tight line spacing",
        "probabilistic objective values are not allowed to leak rows",
    ]
    for i, line in enumerate(lines):
        _line(old[0], line, 72, 90 + i * 9, size=8)
    for note, text in targets.items():
        _mark(old[0], text, note)

    new = _new_doc(1)
    for i, line in enumerate(lines):
        _line(new[0], line, 72, 90 + i * 9, size=8)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "tight_layout",
        old_pdf,
        new_pdf,
        [Expectation(note, "preserved", 0, text) for note, text in targets.items()],
        "dense line spacing designed to expose selected_text leakage",
    )


def build_repeated_short_tokens(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    lines = [
        "The BLEU metric improved after baseline tuning in the translation task.",
        "A later section says BLEU remains unreliable for noisy references.",
        "Final analysis drops BLEU entirely from qualitative claims.",
    ]
    for i, line in enumerate(lines):
        _line(old[0], line, 72, 100 + i * 40)
    for i in range(3):
        _mark(old[0], "BLEU", f"RST_BLEU_{i}", occurrence=i)

    new = _new_doc(1)
    _line(new[0], lines[0], 72, 100)
    _line(new[0], "A later section removes the metric entirely for noisy references.", 72, 140)
    _line(new[0], "Final analysis reports BLEU near a different qualitative claim.", 72, 220)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "repeated_short_tokens",
        old_pdf,
        new_pdf,
        [
            Expectation("RST_BLEU_0", "preserved", 0, "BLEU", known_hard=True),
            Expectation("RST_BLEU_1", "broken", None, "BLEU", known_hard=True),
            Expectation("RST_BLEU_2", "relocated", 0, "BLEU", known_hard=True),
        ],
        "three identical short-token highlights with one removed and one moved",
    )


def build_punctuation_hyphenation(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    old_a = "The state-of-the-art parser achieves stable extraction accuracy."
    old_b = "The measured score was 0.900 after calibration."
    old_c = "Trailing punctuation should not make extraction brittle."
    _line(old[0], old_a, 72, 100)
    _line(old[0], old_b, 72, 135)
    _line(old[0], old_c, 72, 170)
    _mark(old[0], "state-of-the-art parser", "PH_HYPHEN")
    _mark(old[0], old_b, "PH_NUMBER")
    _mark(old[0], old_c, "PH_TRAIL")

    new = _new_doc(1)
    _line(new[0], "The state of the art parser achieves stable extraction accuracy.", 72, 100)
    _line(new[0], "The measured score was 0.910 after calibration.", 72, 135)
    _line(new[0], "Trailing punctuation should not make extraction brittle!", 72, 170)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "punctuation_hyphenation",
        old_pdf,
        new_pdf,
        [
            Expectation("PH_HYPHEN", "changed", 0, "state-of-the-art parser"),
            Expectation("PH_NUMBER", "changed", 0, old_b),
            Expectation("PH_TRAIL", "changed", 0, old_c),
        ],
        "small textual edits involving hyphenation, numbers, and punctuation",
    )


def build_case_only_edit(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    text = "Case Only Biomarker ABC remains in the abstract."
    _line(old[0], text, 72, 100)
    _mark(old[0], text, "CASE_ONLY")

    new = _new_doc(1)
    _line(new[0], "case only biomarker abc remains in the abstract.", 72, 100)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "case_only_edit",
        old_pdf,
        new_pdf,
        [Expectation("CASE_ONLY", "changed", 0, text)],
        "case-only text edit; PyMuPDF search_for is case-insensitive for ASCII",
    )


def build_near_duplicate_deleted(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    texts = [
        "Near duplicate assay result 100 remains present in the revision.",
        "Near duplicate assay result 101 remains present in the revision.",
        "Near duplicate assay result 102 is deleted from the revision.",
        "Near duplicate assay result 103 is deleted from the revision.",
    ]
    for i, text in enumerate(texts):
        _line(old[0], text, 72, 100 + i * 35)
        _mark(old[0], text, f"ND_{i}")

    new = _new_doc(1)
    _line(new[0], texts[0], 72, 100)
    _line(new[0], texts[1], 72, 135)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "near_duplicate_deleted",
        old_pdf,
        new_pdf,
        [
            Expectation("ND_0", "preserved", 0, texts[0]),
            Expectation("ND_1", "preserved", 0, texts[1]),
            Expectation("ND_2", "broken", None, texts[2]),
            Expectation("ND_3", "broken", None, texts[3]),
        ],
        "deleted sentences differ from surviving sentences by only a small number token",
    )


def build_rotated_page(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    texts = [
        "Rotated page anchor one should preserve coordinates across rotation.",
        "Rotated page anchor two checks a lower line on the same page.",
    ]
    _line(old[0], texts[0], 72, 100)
    _line(old[0], texts[1], 72, 150)
    _mark(old[0], texts[0], "ROT_0")
    _mark(old[0], texts[1], "ROT_1")
    old[0].set_rotation(90)

    new = _new_doc(1)
    _line(new[0], texts[0], 72, 100)
    _line(new[0], texts[1], 72, 150)
    new[0].set_rotation(90)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "rotated_page",
        old_pdf,
        new_pdf,
        [
            Expectation("ROT_0", "preserved", 0, texts[0]),
            Expectation("ROT_1", "preserved", 0, texts[1]),
        ],
        "90-degree rotated page with preserved text highlights",
    )


def build_annotation_kinds(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    texts = {
        "KIND_UNDER": "Underline annotations should extract selected text.",
        "KIND_STRIKE": "Strikeout annotations should extract selected text.",
        "KIND_SQUIG": "Squiggly annotations should extract selected text.",
    }
    y = 100
    for text in texts.values():
        _line(old[0], text, 72, y)
        y += 40
    _mark(old[0], texts["KIND_UNDER"], "KIND_UNDER", kind="underline")
    _mark(old[0], texts["KIND_STRIKE"], "KIND_STRIKE", kind="strikeout")
    _mark(old[0], texts["KIND_SQUIG"], "KIND_SQUIG", kind="squiggly")

    new = _new_doc(1)
    y = 100
    for text in texts.values():
        _line(new[0], text, 72, y)
        y += 40

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "annotation_kinds",
        old_pdf,
        new_pdf,
        [Expectation(note, "preserved", 0, text) for note, text in texts.items()],
        "underline / strikeout / squiggly text-coverage annotations",
    )


def build_unsupported_annotations(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    page = old[0]
    _line(page, "Unsupported annotation page has text, notes, and a square marker.", 72, 100)
    note = page.add_text_annot((90, 150), "sticky note body")
    note.set_info(content="UNSUP_TEXT", title="stress")
    note.update()
    rect = pymupdf.Rect(72, 190, 180, 240)
    square = page.add_rect_annot(rect)
    square.set_info(content="UNSUP_SQUARE", title="stress")
    square.update()

    new = _new_doc(1)
    _line(new[0], "Unsupported annotation page has text, notes, and a square marker.", 72, 100)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "unsupported_annotations",
        old_pdf,
        new_pdf,
        [
            Expectation("UNSUP_TEXT", "unsupported", None, None),
            Expectation("UNSUP_SQUARE", "unsupported", None, None),
        ],
        "non-text-coverage annotations should exercise the advertised unsupported status",
    )


def build_far_page_movement(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    text = "Far page movement target lands five pages away in the revised file."
    _line(old[0], text, 72, 120)
    _mark(old[0], text, "FAR_MOVE")

    new = _new_doc(6)
    _line(new[5], text, 72, 120)

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "far_page_movement",
        old_pdf,
        new_pdf,
        [Expectation("FAR_MOVE", "relocated", 5, text)],
        "exact relocation beyond the default nominal page window",
    )


def build_no_annotations(out_dir: Path) -> Scenario:
    old = _new_doc(1)
    _line(old[0], "This old PDF intentionally has no annotations.", 72, 100)
    new = _new_doc(1)
    _line(new[0], "This new PDF intentionally has no annotations.", 72, 100)
    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "no_annotations",
        old_pdf,
        new_pdf,
        [],
        "empty annotation set; verifies zero-anchor reports do not crash",
    )


def build_high_volume(out_dir: Path) -> Scenario:
    old = _new_doc(4)
    expectations: list[Expectation] = []
    positions: dict[str, tuple[int, float]] = {}

    for i in range(50):
        page_idx = i // 14
        y = 80 + (i % 14) * 45
        text = f"High volume anchor {i:02d} remains individually identifiable in the corpus."
        _line(old[page_idx], text, 72, y, size=10)
        _mark(old[page_idx], text, f"HV_{i:02d}")
        positions[f"HV_{i:02d}"] = (page_idx, y)

    new = _new_doc(5)
    for i in range(50):
        note = f"HV_{i:02d}"
        text = f"High volume anchor {i:02d} remains individually identifiable in the corpus."
        if i >= 44:
            expectations.append(Expectation(note, "broken", None, text))
            continue
        old_page, old_y = positions[note]
        if i < 32:
            new_page = old_page
            new_y = old_y
            status = "preserved"
        else:
            new_page = min(old_page + 1, 4)
            new_y = 80 + ((i - 32) % 12) * 45
            status = "relocated"
        _line(new[new_page], text, 72, new_y, size=10)
        expectations.append(Expectation(note, status, new_page, text))

    old_pdf, new_pdf = _save_pair(out_dir, old, new)
    return Scenario(
        "high_volume",
        old_pdf,
        new_pdf,
        expectations,
        "50 annotations mixing preserved, relocated, and broken cases",
    )


def _new_doc(page_count: int) -> pymupdf.Document:
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page(width=PAGE_W, height=PAGE_H)
    return doc


def _line(page: pymupdf.Page, text: str, x: float, y: float, *, size: float = 11) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="helv")


def _textbox(page: pymupdf.Page, text: str, rect: pymupdf.Rect, *, size: float = 11) -> None:
    page.insert_textbox(rect, text, fontsize=size, fontname="helv")


def _mark(
    page: pymupdf.Page,
    text: str,
    note: str,
    *,
    kind: str = "highlight",
    occurrence: int = 0,
    all_quads: bool = False,
) -> None:
    quads = list(page.search_for(text, quads=True) or [])
    if not quads:
        raise RuntimeError(f"could not find text for annotation {note!r}: {text!r}")
    selected = quads if all_quads else [quads[occurrence]]
    if kind == "highlight":
        annot = page.add_highlight_annot(selected)
    elif kind == "underline":
        annot = page.add_underline_annot(selected)
    elif kind == "strikeout":
        annot = page.add_strikeout_annot(selected)
    elif kind == "squiggly":
        annot = page.add_squiggly_annot(selected)
    else:
        raise ValueError(f"unknown annotation kind: {kind}")
    annot.set_info(content=note, title="stress")
    annot.update()


def _save_pair(
    out_dir: Path,
    old_doc: pymupdf.Document,
    new_doc: pymupdf.Document,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    old_pdf = out_dir / "old.pdf"
    new_pdf = out_dir / "new.pdf"
    for path in (old_pdf, new_pdf):
        path.unlink(missing_ok=True)
    old_doc.save(old_pdf)
    new_doc.save(new_pdf)
    old_doc.close()
    new_doc.close()
    return old_pdf, new_pdf


if __name__ == "__main__":
    sys.exit(main())
