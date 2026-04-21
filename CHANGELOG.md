# Changelog

All notable changes to `pdfanno` are documented here. Versioning follows
[Semantic Versioning](https://semver.org/) and dates are ISO-8601.

## [0.1.0] — 2026-04-21

First public release. CLI is feature-complete for plan.md Phase 0 + Phase 1;
TUI (Phase 2) and image rendering (Phase 3) remain out of scope.

### Added

- `pdfanno highlight INPUT NEEDLE` — literal (case-sensitive) or case-
  insensitive search + quad-based highlight, with `--dry-run`, `--json`,
  `--color`, `--pages`, `--ignore-case`, `--sidecar`, `--in-place`.
- `pdfanno list` — enumerates existing annotations with stable `annotation_id`
  on pdfanno-created ones.
- `pdfanno search` — query without writing; emits `AnnotationPlan` JSON.
- `pdfanno note --page --text` — sticky text annotation, idempotent.
- `pdfanno extract --format json|markdown|plan` — export annotations; the
  `plan` format emits a complete `AnnotationPlan` directly consumable by
  `pdfanno apply`, closing the extract → apply loop.
- `pdfanno apply PLAN_JSON` — batch apply an `AnnotationPlan`, shares schema
  with `--dry-run`.
- `pdfanno status` / `import` / `export` / `rebind` — sidecar (SQLite) draft
  workflow.
- **Stable `annotation_id`**: sha256 over (doc_id, kind, page, normalized quads,
  normalized matched_text, rule_hash). Quads rounded to 2 decimal places in PDF
  points. Rotation-invariant by fixture tests.
- **Document identity**: PDF trailer `/ID[0]` primary, fallback to
  `page_count + first_page_text_hash + file_size`; path never participates in
  identity (use `rebind` for file moves).
- **Safety**: `--in-place` pre-checks `can_save_incrementally`, encryption,
  signature, permissions, XFA, JavaScript; refusal returns exit code 4.
- **Agent-first JSON contract**: `schema_version=1`, stable keys, exit codes
  `0/2/3/4` for success/usage/input/processing.
- Fixtures: `simple`, `existing_annotations`, `rotated_90`, `rotated_270`,
  `two_columns`, `scanned_no_text`, `encrypted` (generated in-session).

### Known limitations

- Regex / sentence-level / section-scoped queries deferred to v1.5.
- TUI (Textual, keyboard selection) deferred to v0.2.0 (Phase 2).
- Kitty/Sixel image rendering deferred to v0.3.0 (Phase 3).
- Sidecar sync stays conservative: `import` captures external annotations
  as read-only sidecar rows; no automatic merge when the underlying PDF is
  modified elsewhere (plan.md §9).
- **`section_sim` is experimental in v0.2.0.** The signal is wired into
  `layout_score` (weight 0.20, see `match.W_LAYOUT_SECTION`) and verified
  directionally correct by `tests/test_cross_section.py`, but no
  benchmark in this release demonstrates end-to-end decision-flipping on
  cross-section short-token ambiguity. `_context_window` extracts the
  anchor's context from the first match position rather than the anchor's
  own position, which introduces ctx asymmetry that section_sim can't
  overcome at current weights. Fix + judicial test land in v0.2.1.
  Toggle off with `PDFANNO_DISABLE_SECTION_SIM=1` if needed.

### Dependencies

- Python 3.12+
- PyMuPDF ≥ 1.24 (AGPL-3.0)
- Typer ≥ 0.12
- Pydantic ≥ 2.7
