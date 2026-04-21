"""v0.2 Week 1 PoC 测试：diff CLI 对 3 对 fixture 的期望行为。

10 条注释覆盖三档：identical -> 全 preserved；reordered -> 部分 relocated；
partial -> 被删的那条 broken。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode

runner = CliRunner()


def _run_diff(old: Path, new: Path) -> dict:
    result = runner.invoke(app, ["diff", str(old), str(new), "--json"])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    return json.loads(result.stdout)


def test_diff_identical_all_preserved(pair_identical) -> None:
    v1, v2 = pair_identical
    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 4
    assert s["preserved"] == 4
    assert s["relocated"] == 0
    assert s["broken"] == 0
    for r in report["results"]:
        assert r["status"] == "preserved"
        assert r["confidence"] == 1.0
        assert r["new_anchor"]["page_index"] == r["old_anchor"]["page_index"]


def test_diff_reordered_produces_relocated(pair_reordered) -> None:
    v1, v2 = pair_reordered
    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 3
    assert s["relocated"] >= 1, "reordered fixture must surface at least one relocated"
    assert s["broken"] == 0

    # 具体：LineA5 从 page 0 被挤到 page 1
    relocated = [r for r in report["results"] if r["status"] == "relocated"]
    assert any(
        "LineA5" in r["old_anchor"]["selected_text"]
        and r["new_anchor"]["page_index"] != r["old_anchor"]["page_index"]
        for r in relocated
    )


def test_diff_partial_deletion_is_broken(pair_partial) -> None:
    v1, v2 = pair_partial
    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 3
    assert s["broken"] == 1
    assert s["preserved"] == 2

    broken = [r for r in report["results"] if r["status"] == "broken"]
    assert len(broken) == 1
    assert "BetaUnique" in broken[0]["old_anchor"]["selected_text"]
    assert broken[0]["confidence"] == 0.0
    assert broken[0]["new_anchor"] is None
    assert broken[0]["review_required"] is True


def test_diff_schema_version_is_2(pair_identical) -> None:
    v1, v2 = pair_identical
    report = _run_diff(v1, v2)
    assert report["schema_version"] == 2
    assert "old_doc_id" in report
    assert "new_doc_id" in report
    assert report["old_doc_id"] != report["new_doc_id"] or len(report["results"]) == 0


def test_diff_writes_to_diff_out_file(pair_identical, tmp_path: Path) -> None:
    v1, v2 = pair_identical
    out = tmp_path / "diff.json"
    result = runner.invoke(app, ["diff", str(v1), str(v2), "--diff-out", str(out)])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["preserved"] == 4


def test_diff_total_annotations_matches_ten(pair_identical, pair_reordered, pair_partial) -> None:
    """PRD §10 Week 1 deliverable: ≥10 条注释的 diff JSON。"""

    total = 0
    for v1, v2 in (pair_identical, pair_reordered, pair_partial):
        report = _run_diff(v1, v2)
        total += report["summary"]["total_annotations"]
    assert total == 10


def test_diff_missing_input_returns_input_error(tmp_path: Path, simple_pdf: Path) -> None:
    ghost = tmp_path / "nope.pdf"
    result = runner.invoke(app, ["diff", str(ghost), str(simple_pdf)])
    assert result.exit_code == ExitCode.INPUT_ERROR


def test_diff_respects_backward_compat_exit_codes(simple_pdf: Path, tmp_path: Path) -> None:
    """新 diff 命令不得破坏 v0.1.x 已有 highlight 命令的退出码语义。"""

    out = tmp_path / "o.pdf"
    r = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out)])
    assert r.exit_code == ExitCode.SUCCESS
