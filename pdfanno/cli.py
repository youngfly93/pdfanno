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
from pdfanno.diff.anchors import extract_anchors
from pdfanno.diff.match import diff_against
from pdfanno.exit_codes import ExitCode
from pdfanno.logging import Logger, build_logger
from pdfanno.models import AnnotationPlan, AnnotationRecord, CliResult, PlannedAnnotation, Rule
from pdfanno.pdf_core.annotations import (
    ExistingAnnotation,
    add_highlight,
    add_note,
    existing_pdfanno_ids,
    read_annotation_quads,
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
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """pdfanno root entry point. Invoke subcommands as `pdfanno SUBCOMMAND ...`."""

    _ = version


# ----- highlight -----


@app.command()
def highlight(
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    needle: str = typer.Argument(..., help="Literal string to search and highlight."),
    output: Path | None = typer.Option(  # noqa: B008
        None,
        "-o",
        "--output",
        help="Output PDF path. Mutually exclusive with --in-place / --sidecar.",
    ),
    in_place: bool = typer.Option(
        False,
        "--in-place",
        help="Save in place (incremental). Refuses encrypted / signed / XFA / JS PDFs.",
    ),
    sidecar: bool = typer.Option(
        False, "--sidecar", help="Write to the sidecar store as a draft; do not modify the PDF."
    ),
    color: str = typer.Option(
        "yellow",
        "--color",
        help="Named color (yellow/green/blue/pink/orange/red/purple) or 'r,g,b' triplet.",
    ),
    page_range: str | None = typer.Option(
        None, "--pages", help="Restrict to page numbers (1-indexed), e.g. '1-3,5'."
    ),
    ignore_case: bool = typer.Option(False, "--ignore-case", help="Case-insensitive matching."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build an AnnotationPlan without writing files."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option(
        "text", "--log-format", help="Log format: text or json (stderr)."
    ),
) -> None:
    """Search INPUT for NEEDLE and write highlights to OUTPUT, in place, or to the sidecar."""

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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """List every existing annotation in the PDF."""

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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    needle: str = typer.Argument(..., help="Literal string to search for."),
    page_range: str | None = typer.Option(
        None, "--pages", help="Restrict to page numbers (1-indexed), e.g. '1-3,5'."
    ),
    ignore_case: bool = typer.Option(False, "--ignore-case"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Search without writing; emit match locations and stable annotation IDs."""

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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    page: int = typer.Option(..., "--page", help="Page number (1-indexed)."),
    text: str = typer.Option(..., "--text", help="Annotation body text."),
    point: str = typer.Option(
        "50,50", "--point", help="PDF point coordinates 'x,y', default '50,50'."
    ),
    output: Path | None = typer.Option(  # noqa: B008
        None,
        "-o",
        "--output",
        help="Output PDF path. Mutually exclusive with --in-place / --sidecar.",
    ),
    in_place: bool = typer.Option(False, "--in-place"),
    sidecar: bool = typer.Option(False, "--sidecar"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Add a sticky text annotation at the given page and point."""

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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    fmt: str = typer.Option("json", "--format", help="Output format: json, markdown, or plan."),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Export annotations as JSON, Markdown, or a complete AnnotationPlan."""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    input_path = resolve_path(input)
    _require_input_exists(input_path, logger)

    if fmt not in ("json", "markdown", "plan"):
        logger.error("format must be json, markdown, or plan", got=fmt)
        raise typer.Exit(code=int(ExitCode.USAGE_ERROR))

    with open_pdf(input_path) as doc:
        doc_id = compute_doc_id(doc, input_path)
        if fmt == "plan":
            details = read_annotation_quads(doc)
        else:
            annotations = read_annotations(doc)

    if fmt == "json":
        payload = {
            "schema_version": 1,
            "input": str(input_path),
            "doc_id": doc_id,
            "annotations": _serialize_existing(annotations),
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    elif fmt == "plan":
        plan = _plan_from_existing(doc_id, details)
        typer.echo(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    plan_file: Path = typer.Argument(..., help="AnnotationPlan JSON file."),  # noqa: B008
    output: Path | None = typer.Option(  # noqa: B008
        None, "-o", "--output", help="Output PDF path. Mutually exclusive with --in-place."
    ),
    in_place: bool = typer.Option(False, "--in-place"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_duplicates: bool = typer.Option(False, "--allow-duplicates"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Apply an AnnotationPlan JSON file to the PDF (idempotent by default)."""

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


# ----- v0.2 diff / migrate -----


@app.command()
def diff(
    old_pdf: Path = typer.Argument(..., help="Old-version PDF (with existing annotations)."),  # noqa: B008
    new_pdf: Path = typer.Argument(..., help="New-version PDF to diff against."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="Emit the DiffReport as JSON to stdout."),
    diff_out: Path | None = typer.Option(  # noqa: B008
        None, "--diff-out", help="Write DiffReport JSON to a file instead of stdout."
    ),
    page_window: int = typer.Option(
        3, "--page-window", help="Search window in pages around the old page index."
    ),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Compare annotations across two PDF versions (preserved / relocated / broken)."""

    logger = build_logger(verbose=verbose, quiet=quiet, log_format=log_format)
    old_path = resolve_path(old_pdf)
    new_path = resolve_path(new_pdf)
    _require_input_exists(old_path, logger)
    _require_input_exists(new_path, logger)

    # Week 1 PoC 只用 page_window 当 match 阈值输入 —— 直接改 module-level 常量不合适，
    # 后续 Week 2 重构为 diff_against(..., page_window=...)。这里先警告不等于默认。
    from pdfanno.diff import match as _match_mod

    _match_mod.PAGE_WINDOW = page_window

    with open_pdf(old_path) as old_doc:
        old_doc_id = compute_doc_id(old_doc, old_path)
        anchors = extract_anchors(old_doc, old_doc_id)

    with open_pdf(new_path) as new_doc:
        new_doc_id = compute_doc_id(new_doc, new_path)
        report = diff_against(anchors, new_doc, new_doc_id)

    payload = report.model_dump(mode="json")

    if diff_out is not None:
        diff_out = resolve_path(diff_out)
        diff_out.parent.mkdir(parents=True, exist_ok=True)
        diff_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not as_json:
            typer.echo(f"wrote diff report to {diff_out}")
            _emit_diff_summary(report)
            return

    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _emit_diff_summary(report)


def _emit_diff_summary(report) -> None:
    s = report.summary
    typer.echo(
        f"diff: total={s.total_annotations} "
        f"preserved={s.preserved} relocated={s.relocated} "
        f"changed={s.changed} ambiguous={s.ambiguous} broken={s.broken}"
    )
    for r in report.results[:20]:
        loc = r.new_anchor.page_index if r.new_anchor else "-"
        typer.echo(
            f"  [{r.status}] conf={r.confidence:.2f} "
            f"page {r.old_anchor.page_index} -> {loc} | {r.message}"
        )
    if len(report.results) > 20:
        typer.echo(f"  ... {len(report.results) - 20} more (use --json to see all)")


# ----- sidecar commands: status / import / export / rebind -----


@app.command()
def status(
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Report draft / written sidecar state for INPUT."""

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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Copy existing PDF annotations into the sidecar (read-only; PDF is not modified)."""

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
    input: Path = typer.Argument(..., help="Input PDF path."),  # noqa: B008
    output: Path = typer.Option(  # noqa: B008
        ..., "-o", "--output", help="Output PDF path."
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Write sidecar drafts into OUTPUT. INPUT is left untouched."""

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
            written_ids: list[str] = []
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
                    written_ids.append(e["annotation_id"])
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
                    written_ids.append(e["annotation_id"])
            save_to_new_file(doc, output_path)
    except Exception as exc:  # pragma: no cover
        logger.error("export save failed", error=repr(exc))
        raise typer.Exit(code=int(ExitCode.PROCESSING_ERROR)) from exc

    # 重新打开 output 以读取每条注释在导出文件中的真实 xref，写回 sidecar。
    # 理由：doc.save 后内存 doc 的 xref 可能与文件中的不一致；sidecar 记录的是导出文件的 xref。
    with open_pdf(output_path) as out_doc:
        id_to_xref = {a.name: a.xref for a in read_annotations(out_doc) if a.name}

    with Sidecar() as store:
        for e in drafts:
            if e["annotation_id"] not in written_ids:
                continue
            store.mark_written(
                doc_id,
                e["annotation_id"],
                pdf_xref=id_to_xref.get(e["annotation_id"], 0),
            )

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
    old_path: Path = typer.Argument(..., help="Old PDF path."),  # noqa: B008
    new_path: Path = typer.Argument(..., help="New PDF path."),  # noqa: B008
    explicit_doc_id: str | None = typer.Option(
        None, "--doc-id", help="Provide doc_id explicitly when the old PDF is no longer accessible."
    ),
    as_json: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
    quiet: bool = typer.Option(False, "--quiet"),
    log_format: str = typer.Option("text", "--log-format"),
) -> None:
    """Migrate sidecar entries bound to OLD over to NEW."""

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


def _plan_from_existing(doc_id: str, details: list[dict]) -> AnnotationPlan:
    """把 `read_annotation_quads` 的输出打包成 AnnotationPlan，可直接喂给 `apply`。

    外部阅读器创建、没有 /NM 的注释会合成 `ext:<xref>` 作为 annotation_id —— 保证跨机器
    唯一（在 `apply` 里会被 dedup 视为同一条）。kind 不是 highlight/note 的记录跳过。
    """

    rule = Rule(
        rule_id="extracted",
        kind="highlight",
        query="<from extract>",
        mode="literal",
        color=[1.0, 1.0, 0.0],
    )
    annotations: list[PlannedAnnotation] = []
    for d in details:
        kind = d["kind"]
        if kind not in ("highlight", "text", "note"):
            continue
        annotation_id = d["annotation_id"] or f"ext:{doc_id}:{d['xref']}"
        annotations.append(
            PlannedAnnotation(
                annotation_id=annotation_id,
                rule_id=rule.rule_id,
                kind="highlight" if kind == "highlight" else "note",
                page=d["page"],
                matched_text=d["contents"] or "",
                quads=d["quads"],
                color=d["color"],
                contents=d["contents"] or "",
                source="pdf",
            )
        )
    return AnnotationPlan(doc_id=doc_id, rules=[rule], annotations=annotations)


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
