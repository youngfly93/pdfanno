"""list 命令的 schema 稳定性。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode

runner = CliRunner()


def test_list_empty_pdf(simple_pdf: Path) -> None:
    """没有任何注释的 PDF 应返回空列表，但 schema 仍完整。"""

    result = runner.invoke(app, ["list", str(simple_pdf), "--json"])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "list"
    assert payload["matches"] == 0
    assert "annotations" in payload["data"]
    assert payload["data"]["annotations"] == []


def test_list_after_highlight_reports_written(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    hl = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out)])
    assert hl.exit_code == ExitCode.SUCCESS, hl.output

    result = runner.invoke(app, ["list", str(out), "--json"])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["matches"] >= 1
    records = payload["data"]["annotations"]
    assert all({"page", "xref", "kind", "rect", "annotation_id"} <= r.keys() for r in records)
    # pdfanno 创建的注释必须有稳定的 annotation_id。
    assert all(r["annotation_id"] for r in records if r["subject"] == "pdfanno")


def test_list_preserves_external_annotations(existing_annotations_pdf: Path) -> None:
    result = runner.invoke(app, ["list", str(existing_annotations_pdf), "--json"])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    payload = json.loads(result.stdout)
    assert payload["matches"] >= 1


def test_highlight_on_existing_annotations_does_not_clobber(
    existing_annotations_pdf: Path, tmp_path: Path
) -> None:
    """对已有外部注释的 PDF 高亮新词后，外部注释仍在。"""

    local_input = tmp_path / "with_annots.pdf"
    shutil.copyfile(existing_annotations_pdf, local_input)
    out = tmp_path / "out.pdf"

    # 加入一个不与外部 annot 重叠的新高亮。
    result = runner.invoke(app, ["highlight", str(local_input), "another line", "-o", str(out)])
    assert result.exit_code == ExitCode.SUCCESS, result.output

    listed = runner.invoke(app, ["list", str(out), "--json"])
    payload = json.loads(listed.stdout)
    subjects = [r["subject"] for r in payload["data"]["annotations"]]
    # 至少一条外部注释 + 一条 pdfanno 注释共存。
    assert "pdfanno" in subjects
    # 外部 fixture 的 subject 是 "Highlight"；不要求相等，只要求它没消失。
    assert len(payload["data"]["annotations"]) >= 2
