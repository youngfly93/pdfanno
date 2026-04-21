"""highlight 命令的端到端行为。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode

runner = CliRunner()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_highlight_export_creates_output(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    result = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out)])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert out.exists()
    assert out.stat().st_size > 0


def test_highlight_does_not_modify_input(simple_pdf: Path, tmp_path: Path) -> None:
    # 先把原 fixture 拷贝到 tmp，再用 tmp 的作为 input；校验 input 字节不变。
    local_input = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local_input)
    before = _sha256(local_input)

    out = tmp_path / "paper.annotated.pdf"
    result = runner.invoke(app, ["highlight", str(local_input), "transformer", "-o", str(out)])
    assert result.exit_code == ExitCode.SUCCESS, result.output

    after = _sha256(local_input)
    assert before == after, "--output 不应修改输入文件"


def test_highlight_dry_run_writes_nothing(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    result = runner.invoke(
        app,
        ["highlight", str(simple_pdf), "transformer", "-o", str(out), "--dry-run", "--json"],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert not out.exists(), "--dry-run 不能创建 output 文件"

    payload = json.loads(result.stdout)
    assert payload["command"] == "highlight"
    assert payload["dry_run"] is True
    assert payload["annotations_created"] == 0
    assert payload["matches"] >= 1
    assert "plan" in payload["data"]
    assert payload["data"]["plan"]["schema_version"] == 1


def test_highlight_json_output_schema(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    result = runner.invoke(
        app, ["highlight", str(simple_pdf), "transformer", "-o", str(out), "--json"]
    )
    assert result.exit_code == ExitCode.SUCCESS, result.output
    payload = json.loads(result.stdout)
    expected_keys = {
        "schema_version",
        "command",
        "input",
        "output",
        "dry_run",
        "matches",
        "annotations_planned",
        "annotations_created",
        "warnings",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["matches"] == payload["annotations_planned"]
    assert payload["annotations_created"] == payload["annotations_planned"]


def test_highlight_missing_input_returns_input_error(tmp_path: Path) -> None:
    ghost = tmp_path / "does_not_exist.pdf"
    out = tmp_path / "out.pdf"
    result = runner.invoke(app, ["highlight", str(ghost), "x", "-o", str(out)])
    assert result.exit_code == ExitCode.INPUT_ERROR
    assert not out.exists()


def test_highlight_rejects_same_path_as_output(simple_pdf: Path, tmp_path: Path) -> None:
    local_input = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local_input)
    result = runner.invoke(
        app, ["highlight", str(local_input), "transformer", "-o", str(local_input)]
    )
    assert result.exit_code == ExitCode.USAGE_ERROR


def _build_mixed_case_pdf(path: Path) -> None:
    """构造含 'Transformer'（大写 T）的单页 PDF，用于 case-sensitive 测试。"""

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Transformer is capitalized here")
    page.insert_text((72, 140), "and Transformer appears again")
    doc.save(str(path))
    doc.close()


def test_literal_mode_is_case_sensitive(tmp_path: Path) -> None:
    """literal 模式下，小写 query 不应命中大写文本。"""

    fx = tmp_path / "mixed.pdf"
    _build_mixed_case_pdf(fx)
    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["highlight", str(fx), "transformer", "-o", str(out), "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    assert json.loads(r.stdout)["matches"] == 0


def test_ignore_case_matches_mixed_case(tmp_path: Path) -> None:
    """--ignore-case 下，小写 query 命中大写 'Transformer'。"""

    fx = tmp_path / "mixed.pdf"
    _build_mixed_case_pdf(fx)
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        ["highlight", str(fx), "transformer", "-o", str(out), "--ignore-case", "--json"],
    )
    assert r.exit_code == ExitCode.SUCCESS, r.output
    assert json.loads(r.stdout)["matches"] >= 2
