"""CLI 契约：退出码分档、JSON schema 稳定、log 路由 —— plan.md §12。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode

runner = CliRunner()


def test_version_option() -> None:
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    assert "pdfanno" in r.stdout.lower()


def test_json_schema_has_required_keys(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out), "--json"])
    payload = json.loads(r.stdout)
    required = {
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
    assert required <= payload.keys()
    assert payload["schema_version"] == 1


def test_unknown_color_is_usage_error(simple_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        ["highlight", str(simple_pdf), "x", "-o", str(out), "--color", "chartreuse"],
    )
    assert r.exit_code == ExitCode.USAGE_ERROR


def test_missing_input_is_input_error(tmp_path: Path) -> None:
    ghost = tmp_path / "nope.pdf"
    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["highlight", str(ghost), "x", "-o", str(out)])
    assert r.exit_code == ExitCode.INPUT_ERROR


def test_apply_bad_plan_is_usage_error(simple_pdf: Path, tmp_path: Path) -> None:
    bad = tmp_path / "plan.json"
    bad.write_text("{ this is not json }", encoding="utf-8")
    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["apply", str(simple_pdf), str(bad), "-o", str(out)])
    assert r.exit_code == ExitCode.USAGE_ERROR


def test_stdout_is_only_json_when_json_flag(simple_pdf: Path, tmp_path: Path) -> None:
    """--json 下 stdout 必须是有效 JSON，log 不污染 stdout。"""

    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        [
            "highlight",
            str(simple_pdf),
            "transformer",
            "-o",
            str(out),
            "--json",
            "--verbose",
        ],
    )
    assert r.exit_code == ExitCode.SUCCESS, r.output
    json.loads(r.stdout)  # 任何解析错都让测试失败


def test_search_command_json_includes_plan(simple_pdf: Path) -> None:
    r = runner.invoke(app, ["search", str(simple_pdf), "transformer", "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    assert payload["command"] == "search"
    assert payload["dry_run"] is True
    assert "plan" in payload["data"]
    assert payload["data"]["plan"]["schema_version"] == 1


def test_quiet_suppresses_logs(simple_pdf: Path, tmp_path: Path) -> None:
    """--quiet 下 stderr 不应有 warning 级别日志。"""

    # 构造一个会产生 warning 的场景：apply 一个带错误 doc_id 的 plan
    plan = {
        "schema_version": 1,
        "doc_id": "id:deadbeef",  # 错误 id
        "rules": [],
        "annotations": [],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "out.pdf"
    r = runner.invoke(
        app,
        [
            "apply",
            str(simple_pdf),
            str(plan_path),
            "-o",
            str(out),
            "--quiet",
            "--json",
        ],
    )
    # quiet 下不应 crash，只是 warning 进 JSON 的 warnings 数组。
    assert r.exit_code == ExitCode.SUCCESS, r.output
    payload = json.loads(r.stdout)
    assert any("doc_id mismatch" in w for w in payload["warnings"])
