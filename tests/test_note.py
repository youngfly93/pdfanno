"""note 命令：创建 sticky text annotation 并幂等。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode
from pdfanno.pdf_core.annotations import existing_pdfanno_ids
from pdfanno.pdf_core.document import open_pdf

runner = CliRunner()


def test_note_writes_annotation(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        [
            "note",
            str(simple_pdf),
            "--page",
            "1",
            "--text",
            "important finding",
            "-o",
            str(out),
            "--json",
        ],
    )
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    assert payload["annotations_created"] == 1

    with open_pdf(out) as doc:
        ids = existing_pdfanno_ids(doc)
    assert len(ids) == 1


def test_note_dry_run_no_output(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        [
            "note",
            str(simple_pdf),
            "--page",
            "1",
            "--text",
            "xx",
            "-o",
            str(out),
            "--dry-run",
            "--json",
        ],
    )
    assert r.exit_code == ExitCode.SUCCESS, r.output
    assert not out.exists()
    payload = json.loads(r.stdout)
    assert payload["dry_run"] is True
    assert payload["annotations_planned"] == 1


def test_note_idempotent_on_rerun(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    first = runner.invoke(
        app,
        ["note", str(simple_pdf), "--page", "1", "--text", "x", "-o", str(out), "--json"],
    )
    assert first.exit_code == ExitCode.SUCCESS, first.output

    out2 = tmp_path / "out2.pdf"
    second = runner.invoke(
        app,
        ["note", str(out), "--page", "1", "--text", "x", "-o", str(out2), "--json"],
    )
    assert second.exit_code == ExitCode.SUCCESS, second.output
    assert json.loads(second.stdout)["annotations_created"] == 0


def test_note_page_out_of_range(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        ["note", str(simple_pdf), "--page", "99", "--text", "x", "-o", str(out)],
    )
    assert r.exit_code == ExitCode.USAGE_ERROR
