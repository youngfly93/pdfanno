"""§11 保存策略：--in-place 安全检查、多栏搜索不跨栏、扫描件降级。"""

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


def test_in_place_on_simple_writes_back(simple_pdf: Path, tmp_path: Path) -> None:
    """正常 PDF 的原地写回：文件变化且包含新 annotation。"""

    local = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local)
    before = _sha256(local)
    r = runner.invoke(app, ["highlight", str(local), "transformer", "--in-place", "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    after = _sha256(local)
    assert before != after, "--in-place 必须修改原文件"


def test_in_place_rejects_encrypted(encrypted_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "enc.pdf"
    shutil.copyfile(encrypted_pdf, local)
    before = _sha256(local)
    r = runner.invoke(app, ["highlight", str(local), "encrypted", "--in-place", "--json"])
    assert r.exit_code == ExitCode.PROCESSING_ERROR
    after = _sha256(local)
    assert before == after, "被拒绝的 in-place 不应修改文件"


def test_in_place_and_output_are_mutually_exclusive(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    local = tmp_path / "in.pdf"
    shutil.copyfile(simple_pdf, local)
    r = runner.invoke(
        app,
        ["highlight", str(local), "x", "-o", str(out), "--in-place"],
    )
    assert r.exit_code == ExitCode.USAGE_ERROR


def test_two_column_search_does_not_merge(two_columns_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app, ["highlight", str(two_columns_pdf), "transformer", "-o", str(out), "--json"]
    )
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    # 双栏各一次 "transformer"，共应得到至少 2 条独立 annotation。
    assert payload["matches"] >= 2


def test_scanned_pdf_returns_zero_matches(scanned_no_text_pdf: Path, tmp_path: Path) -> None:
    """扫描件无 text layer → matches=0，退出码 0（非错误，plan.md §12）。"""

    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app, ["highlight", str(scanned_no_text_pdf), "anything", "-o", str(out), "--json"]
    )
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    assert payload["matches"] == 0
    assert payload["annotations_created"] == 0


def test_missing_output_without_in_place_errors(simple_pdf: Path) -> None:
    r = runner.invoke(app, ["highlight", str(simple_pdf), "transformer"])
    assert r.exit_code == ExitCode.USAGE_ERROR


def test_page_range_filters_pages(simple_pdf: Path, tmp_path: Path) -> None:
    # simple 只有一页，--pages 2 应 0 命中；--pages 1 应正常。
    out = tmp_path / "out.pdf"
    r_none = runner.invoke(
        app,
        [
            "highlight",
            str(simple_pdf),
            "transformer",
            "-o",
            str(out),
            "--pages",
            "2",
            "--json",
        ],
    )
    assert r_none.exit_code == ExitCode.SUCCESS, r_none.output
    assert json.loads(r_none.stdout)["matches"] == 0

    out2 = tmp_path / "out2.pdf"
    r_ok = runner.invoke(
        app,
        [
            "highlight",
            str(simple_pdf),
            "transformer",
            "-o",
            str(out2),
            "--pages",
            "1",
            "--json",
        ],
    )
    assert r_ok.exit_code == ExitCode.SUCCESS, r_ok.output
    assert json.loads(r_ok.stdout)["matches"] >= 1
