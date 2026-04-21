"""sidecar 命令：highlight/note --sidecar、status、import、export、rebind。"""

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


def test_highlight_sidecar_does_not_modify_pdf(simple_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local)
    before = _sha256(local)
    r = runner.invoke(app, ["highlight", str(local), "transformer", "--sidecar", "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    after = _sha256(local)
    assert before == after, "--sidecar 不得修改 PDF"
    payload = json.loads(r.stdout)
    assert payload["annotations_created"] >= 1


def test_status_reports_drafts(simple_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local)
    runner.invoke(app, ["highlight", str(local), "transformer", "--sidecar"])
    r = runner.invoke(app, ["status", str(local), "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    assert payload["counts"]["draft"] >= 1
    assert payload["counts"]["written"] == 0


def test_sidecar_highlight_is_idempotent(simple_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local)
    r1 = runner.invoke(app, ["highlight", str(local), "transformer", "--sidecar", "--json"])
    created1 = json.loads(r1.stdout)["annotations_created"]
    r2 = runner.invoke(app, ["highlight", str(local), "transformer", "--sidecar", "--json"])
    created2 = json.loads(r2.stdout)["annotations_created"]
    assert created1 >= 1
    assert created2 == 0, "sidecar dedup 应使第二次 created=0"


def test_export_writes_drafts_and_marks_written(simple_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local)
    runner.invoke(app, ["highlight", str(local), "transformer", "--sidecar"])

    out = tmp_path / "out.pdf"
    exp = runner.invoke(app, ["export", str(local), "-o", str(out), "--json"])
    assert exp.exit_code == ExitCode.SUCCESS, exp.output
    payload = json.loads(exp.stdout)
    assert payload["annotations_created"] >= 1
    assert out.exists()

    # export 完成后，status 应显示 written 并携带真实 pdf_xref（非 0）。
    r = runner.invoke(app, ["status", str(local), "--json"])
    status_payload = json.loads(r.stdout)
    counts = status_payload["counts"]
    assert counts["written"] >= 1
    assert counts["draft"] == 0
    written_entries = [e for e in status_payload["entries"] if e["state"] == "written"]
    assert all(e["pdf_xref"] and e["pdf_xref"] > 0 for e in written_entries), (
        "written entries must carry a real pdf_xref from the exported file"
    )


def test_export_dry_run_writes_nothing(simple_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "paper.pdf"
    shutil.copyfile(simple_pdf, local)
    runner.invoke(app, ["highlight", str(local), "transformer", "--sidecar"])

    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["export", str(local), "-o", str(out), "--dry-run", "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    assert not out.exists()


def test_import_copies_existing_to_sidecar(existing_annotations_pdf: Path, tmp_path: Path) -> None:
    local = tmp_path / "paper.pdf"
    shutil.copyfile(existing_annotations_pdf, local)
    r = runner.invoke(app, ["import", str(local), "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    assert payload["data"]["imported"] >= 1

    # status 应看到 imported 的 written 记录。
    s = runner.invoke(app, ["status", str(local), "--json"])
    sp = json.loads(s.stdout)
    assert sp["counts"]["written"] >= 1


def test_rebind_migrates_entries(simple_pdf: Path, tmp_path: Path) -> None:
    """两个不同 doc_id 的 PDF：旧 doc 的 drafts rebind 到新 doc 后 status 能看到。"""

    old = tmp_path / "old.pdf"
    shutil.copyfile(simple_pdf, old)
    runner.invoke(app, ["highlight", str(old), "transformer", "--sidecar"])
    old_status = runner.invoke(app, ["status", str(old), "--json"])
    old_drafts = json.loads(old_status.stdout)["counts"]["draft"]
    assert old_drafts >= 1

    # 构造一个真正"不同身份"的 PDF —— 全新 document 有不同的 /ID。
    import pymupdf

    new = tmp_path / "new.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "completely different doc for rebind target")
    doc.save(str(new))
    doc.close()

    r = runner.invoke(app, ["rebind", str(old), str(new), "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    migrated = json.loads(r.stdout)["data"]["migrated"]
    assert migrated == old_drafts

    new_status = runner.invoke(app, ["status", str(new), "--json"])
    new_drafts = json.loads(new_status.stdout)["counts"]["draft"]
    assert new_drafts == old_drafts

    # 旧 doc_id 下应没有 entries 了。
    old_status2 = runner.invoke(app, ["status", str(old), "--json"])
    assert json.loads(old_status2.stdout)["counts"]["total"] == 0


def test_sidecar_conflicts_with_output_flag(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["highlight", str(simple_pdf), "x", "--sidecar", "-o", str(out)])
    assert r.exit_code == ExitCode.USAGE_ERROR
