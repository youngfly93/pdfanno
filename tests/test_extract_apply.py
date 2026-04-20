"""extract 与 apply 的 round-trip 行为。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode

runner = CliRunner()


def test_extract_json_schema(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "highlighted.pdf"
    hl = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out)])
    assert hl.exit_code == ExitCode.SUCCESS, hl.output

    ext = runner.invoke(app, ["extract", str(out), "--format", "json"])
    assert ext.exit_code == ExitCode.SUCCESS, ext.output
    payload = json.loads(ext.stdout)
    assert payload["schema_version"] == 1
    assert len(payload["annotations"]) >= 1
    for record in payload["annotations"]:
        assert {"page", "kind", "annotation_id"} <= record.keys()


def test_extract_markdown_format(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "hl.pdf"
    runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out)])
    r = runner.invoke(app, ["extract", str(out), "--format", "markdown"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    assert "page" in r.stdout.lower()


def test_apply_dry_run_schema_matches_real_run(simple_pdf: Path, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    dry = runner.invoke(
        app,
        ["highlight", str(simple_pdf), "transformer", "-o", "unused.pdf", "--dry-run", "--json"],
    )
    assert dry.exit_code == ExitCode.SUCCESS, dry.output
    plan_obj = json.loads(dry.stdout)["data"]["plan"]
    plan_path.write_text(json.dumps(plan_obj, ensure_ascii=False), encoding="utf-8")

    apply_dry = runner.invoke(
        app,
        ["apply", str(simple_pdf), str(plan_path), "-o", "out.pdf", "--dry-run", "--json"],
    )
    assert apply_dry.exit_code == ExitCode.SUCCESS, apply_dry.output
    dry_payload = json.loads(apply_dry.stdout)

    out_pdf = tmp_path / "real.pdf"
    apply_real = runner.invoke(
        app,
        ["apply", str(simple_pdf), str(plan_path), "-o", str(out_pdf), "--json"],
    )
    assert apply_real.exit_code == ExitCode.SUCCESS, apply_real.output
    real_payload = json.loads(apply_real.stdout)

    # dry-run 与真实执行的 annotations_planned 应一致；created 仅真实执行有。
    assert dry_payload["annotations_planned"] == real_payload["annotations_planned"]
    assert real_payload["annotations_created"] == real_payload["annotations_planned"]


def test_apply_roundtrip_from_extract(simple_pdf: Path, tmp_path: Path) -> None:
    """highlight → extract → 手工构造 plan → apply 到新文件 → list 回读 id 一致。"""

    hl_out = tmp_path / "hl.pdf"
    runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(hl_out)])

    ext = runner.invoke(app, ["extract", str(hl_out), "--format", "json"])
    src_records = json.loads(ext.stdout)["annotations"]
    planned_ids = sorted(r["annotation_id"] for r in src_records if r["annotation_id"])

    # 重新生成 plan 走 highlight --dry-run —— apply 以同样的 plan 回放
    dry = runner.invoke(
        app, ["highlight", str(simple_pdf), "transformer", "-o", "x.pdf", "--dry-run", "--json"]
    )
    plan = json.loads(dry.stdout)["data"]["plan"]
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    applied = tmp_path / "applied.pdf"
    a = runner.invoke(app, ["apply", str(simple_pdf), str(plan_file), "-o", str(applied), "--json"])
    assert a.exit_code == ExitCode.SUCCESS, a.output

    ext2 = runner.invoke(app, ["extract", str(applied), "--format", "json"])
    recovered_ids = sorted(
        r["annotation_id"] for r in json.loads(ext2.stdout)["annotations"] if r["annotation_id"]
    )
    assert recovered_ids == planned_ids
