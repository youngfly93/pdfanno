"""pdfanno CLI —— Typer app。

实现 Phase 0 + Phase 1 无 sidecar 部分的命令：highlight / list / search / note /
extract / apply。--dry-run 与 apply 共用 AnnotationPlan，避免预览和真实执行分叉。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from pydantic import ValidationError

from pdfanno import __version__
from pdfanno.exit_codes import ExitCode
from pdfanno.logging import Logger, build_logger
from pdfanno.models import AnnotationPlan, AnnotationRecord, CliResult, PlannedAnnotation, Rule
from pdfanno.pdf_core.annotations import (
    ExistingAnnotation,
    add_highlight,
    add_note,
    existing_pdfanno_ids,
    read_annotations,
)
from pdfanno.pdf_core.colors import parse_color
from pdfanno.pdf_core.document import compute_doc_id, inspect_safety, open_pdf, resolve_path
from pdfanno.pdf_core.save import InPlaceSaveRefused, save_in_place, save_to_new_file
from pdfanno.rules.idempotency import compute_annotation_id
from pdfanno.rules.match import plan_for_query
from pdfanno.store.sidecar import (
    SOURCE_PDF,
    SOURCE_SIDECAR,
    STATE_DRAFT,
    STATE_WRITTEN,
    Sidecar,
)

app = typer.Typer(
    name="pdfanno",
    help="Agent-friendly CLI for PDF annotation writeback.",
    no_args_is_help=True,
    add_completion=False,
)

DEFAULT_NOTE_POINT = (50.0, 50.0)
NOTE_RULE_HASH = "note:direct"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pdfanno {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(  # noqa: B008
        False, "--version", callback=_version_callback, is_eager=True, help="打印版本并退出。"
    ),
) -> None:
    """pdfanno 根入口。子命令通过 `pdfanno SUBCOMMAND ...` 调用。"""

    _ = version


# ----- highlight -----


@app.command()
def highlight(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    needle: str = typer.Argument(..., help="要搜索并高亮的 literal 字符串。"),
    output: Path | None = typer.Option(  # noqa: B008
        None, "-o", "--output", help="输出 PDF 路径。与 --in-place / --sidecar 互斥。"
    ),
    in_place: bool = typer.Option(False, "--in-place", help="原地增量写回。安全检查不过则拒绝。"),
    sidecar: bool = typer.Option(False, "--sidecar", help="仅写入 sidecar 草稿，不触碰 PDF。"),
    color: str = typer.Option("yellow", "--color", help="命名色或 'r,g,b' 三元组。"),
    page_range: str | None = typer.Option(None, "--pages", help="限制页号，如 '1-3,5'。"),
    ignore_case: bool = typer.Option(False, "--ignore-case", help="不区分大小写。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只生成 plan，不写任何文件。"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON 到 stdout。"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format", help="text 或 json。"),
) -> None:
    """在 INPUT 中搜索 NEEDLE 并写 highlight 到 OUTPUT、原地或 sidecar。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    _reject_conflicting_write_targets(sidecar, in_place, output, logger)
    output_path = (
        None
        if (sidecar or dry_run)
        else _resolve_write_target(input_path, output, in_place, dry_run, logger)
    )

    try:
        color_rgb = parse_color(color)
    except ValueError as exc:
        logger.error("invalid color", error=str(exc))
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR)) from exc

    try:
        with open_pdf(input_path) as doc:
            if in_place and not dry_run:
                _precheck_in_place(doc)

            doc_id = compute_doc_id(doc, input_path)
            plan = plan_for_query(
                doc,
                doc_id,
                query=needle,
                kind="highlight",
                mode="ignore-case" if ignore_case else "literal",
                color=color_rgb,
                page_range=page_range,
            )

            if dry_run:
                result = _result_from_plan(
                    command="highlight",
                    input_path=input_path,
                    output_path=None,
                    dry_run=True,
                    plan=plan,
                    created=0,
                )
                _emit(result, plan=plan, as_json=as_json)
                raise typer.Exit(code=int(ExitCode.SUCCESS))

            if sidecar:
                created = _write_plan_to_sidecar(input_path, plan)
                result = _result_from_plan(
                    command="highlight",
                    input_path=input_path,
                    output_path=None,
                    dry_run=False,
                    plan=plan,
                    created=created,
                )
                _emit(result, plan=None, as_json=as_json)
                raise typer.Exit(code=int(ExitCode.SUCCESS))

            created, warnings = _apply_plan_to_doc(doc, plan)
            _save(doc, input_path, output_path, in_place, logger)

            result = _result_from_plan(
                command="highlight",
                input_path=input_path,
                output_path=output_path,
                dry_run=False,
                plan=plan,
                created=created,
                warnings=warnings,
            )
            _emit(result, plan=None, as_json=as_json)
    except typer.Exit:
        raise
    except InPlaceSaveRefused as exc:
        logger.error("in-place save refused", reasons=",".join(exc.reasons))
        raise typer.Exit(code=int(ExitCode.PROCESSING_ERROR)) from exc
    except FileNotFoundError as exc:
        logger.error("input file error", error=repr(exc))
        raise typer.Exit(code=int(ExitCode.INPUT_ERROR)) from exc
    except ValueError as exc:
        logger.error("usage error", error=str(exc))
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR)) from exc


# ----- list -----


@app.command(name="list")
def list_cmd(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="输出 JSON 到 stdout。"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """列出 PDF 中现有的所有 annotation。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    try:
        with open_pdf(input_path) as doc:
            annotations = read_annotations(doc)
    except FileNotFoundError as exc:
        logger.error("input file error", error=repr(exc))
        raise typer.Exit(code=int(ExitCode.INPUT_ERROR)) from exc

    payload = _serialize_existing(annotations)
    result = CliResult(
        command="list",
        input=str(input_path),
        matches=len(payload),
        data={"annotations": payload},
    )

    if as_json:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{len(payload)} annotation(s) in {input_path}")
        for a in payload:
            typer.echo(
                f"  page {a['page']} xref={a['xref']} kind={a['kind']} "
                f"id={a['annotation_id'] or '-'}"
            )


# ----- search -----


@app.command()
def search(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    needle: str = typer.Argument(..., help="要搜索的 literal 字符串。"),
    page_range: str | None = typer.Option(None, "--pages", help="限制页号，如 '1-3,5'。"),
    ignore_case: bool = typer.Option(False, "--ignore-case"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON 到 stdout。"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """只搜索不写入，返回命中位置与稳定 annotation_id。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    with open_pdf(input_path) as doc:
        doc_id = compute_doc_id(doc, input_path)
        plan = plan_for_query(
            doc,
            doc_id,
            query=needle,
            kind="highlight",
            mode="ignore-case" if ignore_case else "literal",
            page_range=page_range,
        )

    result = _result_from_plan(
        command="search",
        input_path=input_path,
        output_path=None,
        dry_run=True,
        plan=plan,
        created=0,
    )
    result.data = {"plan": plan.model_dump(mode="json")}
    _emit(result, plan=plan if not as_json else None, as_json=as_json)


# ----- note -----


@app.command()
def note(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    page: int = typer.Option(..., "--page", help="页号，1-indexed。"),
    text: str = typer.Option(..., "--text", help="注释正文。"),
    point: str = typer.Option("50,50", "--point", help="PDF 点坐标 'x,y'，默认 '50,50'。"),
    output: Path | None = typer.Option(  # noqa: B008
        None, "-o", "--output", help="输出 PDF 路径。与 --in-place / --sidecar 互斥。"
    ),
    in_place: bool = typer.Option(False, "--in-place"),
    sidecar: bool = typer.Option(False, "--sidecar"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """在 PAGE 指定位置添加 sticky text annotation。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    _reject_conflicting_write_targets(sidecar, in_place, output, logger)
    output_path = (
        None
        if (sidecar or dry_run)
        else _resolve_write_target(input_path, output, in_place, dry_run, logger)
    )

    try:
        pt = _parse_point(point)
    except ValueError as exc:
        logger.error("invalid --point", error=str(exc))
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR)) from exc

    try:
        with open_pdf(input_path) as doc:
            if in_place and not dry_run:
                _precheck_in_place(doc)
            page_idx = _resolve_page_index(page, doc.page_count)
            doc_id = compute_doc_id(doc, input_path)
            note_plan = _build_note_plan(doc_id, page_idx, pt, text)

            if dry_run:
                result = _result_from_plan(
                    command="note",
                    input_path=input_path,
                    output_path=None,
                    dry_run=True,
                    plan=note_plan,
                    created=0,
                )
                _emit(result, plan=note_plan, as_json=as_json)
                raise typer.Exit(code=int(ExitCode.SUCCESS))

            if sidecar:
                created = _write_plan_to_sidecar(input_path, note_plan)
                result = _result_from_plan(
                    command="note",
                    input_path=input_path,
                    output_path=None,
                    dry_run=False,
                    plan=note_plan,
                    created=created,
                )
                _emit(result, plan=None, as_json=as_json)
                raise typer.Exit(code=int(ExitCode.SUCCESS))

            already = existing_pdfanno_ids(doc)
            created = 0
            annot_plan = note_plan.annotations[0]
            if annot_plan.annotation_id not in already:
                add_note(
                    doc,
                    doc[annot_plan.page],
                    point=pt,
                    contents=text,
                    annotation_id=annot_plan.annotation_id,
                )
                created = 1

            _save(doc, input_path, output_path, in_place, logger)

            result = _result_from_plan(
                command="note",
                input_path=input_path,
                output_path=output_path,
                dry_run=False,
                plan=note_plan,
                created=created,
            )
            _emit(result, plan=None, as_json=as_json)
    except typer.Exit:
        raise
    except InPlaceSaveRefused as exc:
        logger.error("in-place save refused", reasons=",".join(exc.reasons))
        raise typer.Exit(code=int(ExitCode.PROCESSING_ERROR)) from exc
    except ValueError as exc:
        logger.error("usage error", error=str(exc))
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR)) from exc


# ----- extract -----


@app.command()
def extract(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    fmt: str = typer.Option("json", "--format", help="json 或 markdown。"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """把 PDF 里的 annotation 导出为 JSON 或 Markdown。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    if fmt not in ("json", "markdown"):
        logger.error("format must be json or markdown", got=fmt)
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))

    with open_pdf(input_path) as doc:
        annotations = read_annotations(doc)

    if fmt == "json":
        payload = {
            "schema_version": 1,
            "input": str(input_path),
            "annotations": _serialize_existing(annotations),
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"# Annotations extracted from {input_path.name}\n")
        for a in annotations:
            typer.echo(
                f"- page {a.page + 1} [{a.kind}] id={a.name or '-'} subject={a.subject or '-'}"
            )
            if a.contents:
                typer.echo(f"  > {a.contents}")


# ----- apply -----


@app.command()
def apply(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    plan_file: Path = typer.Argument(..., help="AnnotationPlan JSON 文件。"),  # noqa: B008
    output: Path | None = typer.Option(  # noqa: B008
        None, "-o", "--output", help="输出 PDF 路径。与 --in-place 互斥。"
    ),
    in_place: bool = typer.Option(False, "--in-place"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_duplicates: bool = typer.Option(False, "--allow-duplicates"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """按照 AnnotationPlan JSON 批量创建 annotation。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    plan_path = resolve_path(plan_file)
    if not plan_path.exists():
        logger.error("plan file not found", path=str(plan_path))
        raise typer.Exit(code=int(ExitCode.INPUT_ERROR))

    output_path = _resolve_write_target(input_path, output, in_place, dry_run, logger)

    try:
        plan = AnnotationPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        logger.error("plan schema error", error=str(exc))
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR)) from exc

    try:
        with open_pdf(input_path) as doc:
            if in_place and not dry_run:
                _precheck_in_place(doc)
            actual_doc_id = compute_doc_id(doc, input_path)
            warnings: list[str] = []
            if plan.doc_id and plan.doc_id != actual_doc_id:
                warnings.append(f"doc_id mismatch: plan={plan.doc_id} actual={actual_doc_id}")

            if dry_run:
                result = _result_from_plan(
                    command="apply",
                    input_path=input_path,
                    output_path=None,
                    dry_run=True,
                    plan=plan,
                    created=0,
                    warnings=warnings,
                )
                _emit(result, plan=plan, as_json=as_json)
                raise typer.Exit(code=int(ExitCode.SUCCESS))

            created, extra_warnings = _apply_plan_to_doc(
                doc, plan, allow_duplicates=allow_duplicates
            )
            warnings.extend(extra_warnings)
            _save(doc, input_path, output_path, in_place, logger)

            result = _result_from_plan(
                command="apply",
                input_path=input_path,
                output_path=output_path,
                dry_run=False,
                plan=plan,
                created=created,
                warnings=warnings,
            )
            _emit(result, plan=None, as_json=as_json)
    except typer.Exit:
        raise
    except InPlaceSaveRefused as exc:
        logger.error("in-place save refused", reasons=",".join(exc.reasons))
        raise typer.Exit(code=int(ExitCode.PROCESSING_ERROR)) from exc


# ----- sidecar commands: status / import / export / rebind -----


@app.command()
def status(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """报告 INPUT 对应 sidecar 中的 draft / written 状态。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    with open_pdf(input_path) as doc:
        doc_id = compute_doc_id(doc, input_path)
    with Sidecar() as store:
        binding = store.get_binding(doc_id) or {}
        entries = store.list_entries(doc_id)

    draft = [e for e in entries if e["state"] == STATE_DRAFT]
    written = [e for e in entries if e["state"] == STATE_WRITTEN]

    payload = {
        "schema_version": 1,
        "command": "status",
        "input": str(input_path),
        "doc_id": doc_id,
        "binding": binding,
        "counts": {
            "total": len(entries),
            "draft": len(draft),
            "written": len(written),
        },
        "entries": entries,
    }
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(
            f"doc_id={doc_id} drafts={len(draft)} written={len(written)} total={len(entries)}"
        )
        for e in entries:
            typer.echo(
                f"  [{e['state']}] page {e['page']} kind={e['kind']} id={e['annotation_id'][:16]}…"
            )


@app.command(name="import")
def import_cmd(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """把 PDF 内既有注释复制为 sidecar 记录（只读，不修改 PDF）。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    with open_pdf(input_path) as doc:
        doc_id = compute_doc_id(doc, input_path)
        annotations = read_annotations(doc)

    imported = 0
    with Sidecar() as store:
        store.touch_doc(doc_id, input_path)
        existing = store.existing_annotation_ids(doc_id)
        for a in annotations:
            synthetic_id = a.name or f"import:{doc_id}:{a.xref}"
            if synthetic_id in existing:
                continue
            rec = _existing_to_record(a, doc_id, synthetic_id)
            store.upsert_entry(rec, state=STATE_WRITTEN)
            imported += 1

    result = CliResult(
        command="import",
        input=str(input_path),
        matches=len(annotations),
        annotations_planned=len(annotations),
        annotations_created=imported,
        data={"doc_id": doc_id, "imported": imported},
    )
    _emit(result, plan=None, as_json=as_json)


@app.command()
def export(
    input: Path = typer.Argument(..., help="输入 PDF 路径。"),  # noqa: B008
    output: Path = typer.Option(  # noqa: B008
        ..., "-o", "--output", help="输出 PDF 路径。"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """把 sidecar 中的 draft 条目写入 OUTPUT。原 INPUT 不被修改。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)
    output_path = resolve_path(output)
    if output_path == input_path:
        logger.error("export 的 -o 不能等于输入路径")
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))

    with open_pdf(input_path) as doc:
        doc_id = compute_doc_id(doc, input_path)

    with Sidecar() as store:
        drafts = store.list_entries(doc_id, state=STATE_DRAFT)

    if dry_run:
        result = CliResult(
            command="export",
            input=str(input_path),
            output=str(output_path),
            dry_run=True,
            matches=len(drafts),
            annotations_planned=len(drafts),
            annotations_created=0,
            data={"drafts": drafts},
        )
        _emit(result, plan=None, as_json=as_json)
        return

    try:
        with open_pdf(input_path) as doc:
            created = 0
            for e in drafts:
                if e["page"] < 0 or e["page"] >= doc.page_count:
                    continue
                page = doc[e["page"]]
                if e["kind"] == "highlight":
                    add_highlight(
                        doc,
                        page,
                        quads_floats=e["quads"],
                        color=e["color"],
                        annotation_id=e["annotation_id"],
                        contents=e["contents"],
                    )
                    created += 1
                elif e["kind"] == "note":
                    pt = _note_point_from_quads(e["quads"])
                    add_note(
                        doc,
                        page,
                        point=pt,
                        contents=e["contents"] or e["matched_text"],
                        annotation_id=e["annotation_id"],
                    )
                    created += 1
            save_to_new_file(doc, output_path)
    except Exception as exc:  # pragma: no cover
        logger.error("export save failed", error=repr(exc))
        raise typer.Exit(code=int(ExitCode.PROCESSING_ERROR)) from exc

    # 标记写回的 drafts 为 written（sidecar 状态）。
    with Sidecar() as store:
        for e in drafts:
            store.mark_written(doc_id, e["annotation_id"], pdf_xref=0)

    result = CliResult(
        command="export",
        input=str(input_path),
        output=str(output_path),
        dry_run=False,
        matches=len(drafts),
        annotations_planned=len(drafts),
        annotations_created=created,
        data={"doc_id": doc_id},
    )
    _emit(result, plan=None, as_json=as_json)


@app.command()
def rebind(
    old_path: Path = typer.Argument(..., help="旧 PDF 路径。"),  # noqa: B008
    new_path: Path = typer.Argument(..., help="新 PDF 路径。"),  # noqa: B008
    explicit_doc_id: str | None = typer.Option(
        None, "--doc-id", help="旧 PDF 已不可访问时显式提供 doc_id。"
    ),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """把 sidecar 中绑定到 OLD 的条目迁到 NEW。"""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    new_resolved = resolve_path(new_path)
    if not new_resolved.exists():
        logger.error("new path not found", path=str(new_resolved))
        raise typer.Exit(code=int(ExitCode.INPUT_ERROR))

    if explicit_doc_id:
        old_doc_id = explicit_doc_id
    else:
        old_resolved = resolve_path(old_path)
        if not old_resolved.exists():
            logger.error(
                "旧 PDF 不存在且未给 --doc-id；请传 --doc-id OLD_DOC_ID",
                path=str(old_resolved),
            )
            raise typer.Exit(code=int(ExitCode.INPUT_ERROR))
        with open_pdf(old_resolved) as doc:
            old_doc_id = compute_doc_id(doc, old_resolved)

    with open_pdf(new_resolved) as doc:
        new_doc_id = compute_doc_id(doc, new_resolved)

    with Sidecar() as store:
        migrated = store.rebind(old_doc_id, new_doc_id, new_resolved)

    result = CliResult(
        command="rebind",
        input=str(new_resolved),
        matches=migrated,
        annotations_planned=migrated,
        annotations_created=migrated,
        data={"old_doc_id": old_doc_id, "new_doc_id": new_doc_id, "migrated": migrated},
    )
    _emit(result, plan=None, as_json=as_json)


# ----- shared helpers -----


def _existing_to_record(
    annotation: ExistingAnnotation, doc_id: str, synthetic_id: str
) -> AnnotationRecord:
    return AnnotationRecord(
        id="",
        annotation_id=synthetic_id,
        doc_id=doc_id,
        page=annotation.page,
        kind=annotation.kind,
        quads=[],  # 已有 annot 的 quad 通过 xref 查，sidecar 层不复原
        color=annotation.color or [1.0, 1.0, 0.0],
        contents=annotation.contents,
        matched_text="",
        rule_hash="",
        query="",
        source=SOURCE_PDF,
        pdf_xref=annotation.xref,
    )


def _require_input_exists(path: Path, logger: Logger) -> None:
    if not path.exists():
        logger.error("input file not found", path=str(path))
        raise typer.Exit(code=int(ExitCode.INPUT_ERROR))


def _resolve_write_target(
    input_path: Path,
    output: Path | None,
    in_place: bool,
    dry_run: bool,
    logger: Logger,
) -> Path | None:
    """解析写回目标路径。dry-run 时返回 None；in-place 返回 input_path；否则要求 -o。"""

    if dry_run:
        return None
    if in_place and output is not None:
        logger.error("--in-place 与 -o 互斥")
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))
    if in_place:
        return input_path
    if output is None:
        logger.error("必须给 -o OUTPUT 或 --in-place 二选一")
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))
    resolved = resolve_path(output)
    if resolved == input_path:
        logger.error("-o 路径不能等于输入路径。需要原地写请改用 --in-place。")
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))
    return resolved


def _reject_conflicting_write_targets(
    sidecar: bool, in_place: bool, output: Path | None, logger: Logger
) -> None:
    """--sidecar / --in-place / -o 三者互斥，最多给一个。"""

    active = sum(1 for flag in (sidecar, in_place, output is not None) if flag)
    if active > 1:
        logger.error("--sidecar / --in-place / -o 三选一，最多给一个")
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))


def _write_plan_to_sidecar(input_path: Path, plan: AnnotationPlan) -> int:
    """把 plan.annotations 以 draft 状态写入 sidecar。返回新增条目数。"""

    created = 0
    with Sidecar() as store:
        store.touch_doc(plan.doc_id, input_path)
        existing = store.existing_annotation_ids(plan.doc_id)
        for annot in plan.annotations:
            if annot.annotation_id in existing:
                continue
            rec = _plan_annotation_to_record(annot, plan.doc_id, source=SOURCE_SIDECAR)
            store.upsert_entry(rec, state=STATE_DRAFT)
            created += 1
    return created


def _plan_annotation_to_record(
    annot: PlannedAnnotation, doc_id: str, *, source: str
) -> AnnotationRecord:
    """把 PlannedAnnotation 转换为 AnnotationRecord 以供 sidecar 存储。"""

    return AnnotationRecord(
        id="",
        annotation_id=annot.annotation_id,
        doc_id=doc_id,
        page=annot.page,
        kind=annot.kind,
        quads=annot.quads,
        color=annot.color,
        contents=annot.contents,
        matched_text=annot.matched_text,
        rule_hash="",
        query="",
        source=source,
    )


def _precheck_in_place(doc) -> None:
    """--in-place 入口拦截：任何安全标志命中就提前抛 InPlaceSaveRefused。

    避免在不安全文档上做无用的 plan_for_query（它本身可能抛加密/权限异常，
    让退出码分档错位）。
    """

    flags = inspect_safety(doc)
    reasons: list[str] = []
    if not flags.can_save_incrementally:
        reasons.append("cannot save incrementally")
    if flags.is_encrypted:
        reasons.append("encrypted")
    if flags.is_signed:
        reasons.append("contains digital signature")
    if flags.is_permission_restricted:
        reasons.append("permission-restricted")
    if flags.has_xfa:
        reasons.append("XFA form")
    if flags.has_javascript:
        reasons.append("embedded JavaScript")
    if reasons:
        raise InPlaceSaveRefused(reasons)


def _save(
    doc,
    input_path: Path,
    output_path: Path | None,
    in_place: bool,
    logger: Logger,
) -> None:
    """根据 in_place 标志选择 save_in_place 或 save_to_new_file。"""

    if in_place:
        try:
            save_in_place(doc, input_path)
        except InPlaceSaveRefused:
            # 让上层分类为 PROCESSING_ERROR。
            raise
        return
    if output_path is None:
        raise ValueError("no output path and not in-place")
    try:
        save_to_new_file(doc, output_path)
    except Exception as exc:  # pragma: no cover
        logger.error("save failed", error=repr(exc))
        raise


def _apply_plan_to_doc(
    doc, plan: AnnotationPlan, *, allow_duplicates: bool = False
) -> tuple[int, list[str]]:
    """按 plan 写注释到 doc。返回 (created_count, warnings)。"""

    warnings: list[str] = []
    already = existing_pdfanno_ids(doc) if not allow_duplicates else set()
    created = 0
    for annot_plan in plan.annotations:
        if annot_plan.annotation_id in already:
            continue
        if annot_plan.page < 0 or annot_plan.page >= doc.page_count:
            warnings.append(f"skip page out of range: {annot_plan.page}")
            continue
        page = doc[annot_plan.page]
        if annot_plan.kind == "highlight":
            add_highlight(
                doc,
                page,
                quads_floats=annot_plan.quads,
                color=annot_plan.color,
                annotation_id=annot_plan.annotation_id,
                contents=annot_plan.contents,
            )
            created += 1
        elif annot_plan.kind == "note":
            pt = _note_point_from_quads(annot_plan.quads)
            add_note(
                doc,
                page,
                point=pt,
                contents=annot_plan.contents or annot_plan.matched_text,
                annotation_id=annot_plan.annotation_id,
            )
            created += 1
        else:
            warnings.append(f"unsupported annotation kind: {annot_plan.kind}")
    return created, warnings


def _note_point_from_quads(quads: list[list[float]]) -> tuple[float, float]:
    if not quads or not quads[0]:
        return DEFAULT_NOTE_POINT
    q = quads[0]
    return (float(q[0]), float(q[1]))


def _build_note_plan(
    doc_id: str, page_idx: int, point: tuple[float, float], contents: str
) -> AnnotationPlan:
    rule_hash = NOTE_RULE_HASH
    synthetic_quad = [point[0], point[1]] * 4  # 8 floats，退化为点，用于幂等 hash。
    annotation_id = compute_annotation_id(
        doc_id=doc_id,
        kind="note",
        page=page_idx,
        quads=[synthetic_quad],
        matched_text=contents,
        rule_hash=rule_hash,
    )
    rule = Rule(
        rule_id="note-direct",
        kind="note",
        query=contents,
        mode="literal",
        color=[1.0, 1.0, 0.0],
    )
    annotation = PlannedAnnotation(
        annotation_id=annotation_id,
        rule_id=rule.rule_id,
        kind="note",
        page=page_idx,
        matched_text=contents,
        quads=[synthetic_quad],
        color=[1.0, 1.0, 0.0],
        contents=contents,
    )
    return AnnotationPlan(doc_id=doc_id, rules=[rule], annotations=[annotation])


def _parse_point(value: str) -> tuple[float, float]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"--point 必须是 'x,y' 两数，收到 {value!r}")
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise ValueError(f"--point 分量必须是 float，收到 {value!r}") from exc


def _resolve_page_index(page_one_indexed: int, page_count: int) -> int:
    if page_one_indexed < 1 or page_one_indexed > page_count:
        raise ValueError(
            f"页号越界：--page {page_one_indexed}，文档共 {page_count} 页（1-indexed）"
        )
    return page_one_indexed - 1


def _serialize_existing(annotations: list[ExistingAnnotation]) -> list[dict]:
    return [
        {
            "page": a.page,
            "xref": a.xref,
            "kind": a.kind,
            "rect": list(a.rect),
            "color": a.color,
            "contents": a.contents,
            "title": a.title,
            "subject": a.subject,
            "annotation_id": a.name,
        }
        for a in annotations
    ]


def _result_from_plan(
    *,
    command: str,
    input_path: Path,
    output_path: Path | None,
    dry_run: bool,
    plan: AnnotationPlan,
    created: int,
    warnings: list[str] | None = None,
) -> CliResult:
    return CliResult(
        command=command,
        input=str(input_path),
        output=str(output_path) if output_path else None,
        dry_run=dry_run,
        matches=len(plan.annotations),
        annotations_planned=len(plan.annotations),
        annotations_created=created,
        warnings=list(warnings) if warnings else [],
        data={"plan": plan.model_dump(mode="json")} if dry_run else None,
    )


def _emit(result: CliResult, *, plan: AnnotationPlan | None, as_json: bool) -> None:
    if as_json:
        try:
            typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        except ValidationError as exc:  # pragma: no cover
            typer.echo(f"schema error: {exc}", err=True)
            sys.exit(int(ExitCode.PROCESSING_ERROR))
        return

    line = (
        f"{result.command}: matches={result.matches} "
        f"planned={result.annotations_planned} created={result.annotations_created}"
    )
    if result.dry_run:
        line += " (dry-run)"
    if result.output:
        line += f" -> {result.output}"
    typer.echo(line)
    if plan is not None and plan.annotations:
        for a in plan.annotations[:10]:
            typer.echo(f"  page {a.page} id={a.annotation_id[:12]}… text={a.matched_text!r}")
        if len(plan.annotations) > 10:
            typer.echo(f"  … {len(plan.annotations) - 10} more")


if __name__ == "__main__":
    app()
