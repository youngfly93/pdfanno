# Changelog

All notable changes to `pdfanno` are documented here. Versioning follows
[Semantic Versioning](https://semver.org/) and dates are ISO-8601.

## [0.2.1] — 2026-04-22

Patch release: fixes selected-text extraction on tight-layout PDFs and
adds Word2Vec / Seq2Seq as regression cases. No change to existing
baselines.

### Fixed

- `_selected_text` extraction on tight-layout PDFs. The old
  `page.get_textbox(rect)` call leaked glyph fragments from adjacent
  lines (e.g. `'pared to the pre\\nneural network\\nmputational cos'`),
  breaking `ground_truth` re-location and `context_similarity` scoring
  for most conference-format papers. Fixed by `_clip_text_to_rect`
  using a hybrid strategy: char-level `get_textbox` first; if the
  result contains `\\n`, fall back to a word-level filter that keeps
  only words whose y-center falls inside the quad's y-range ±1pt.
  Loose-layout papers (arXiv preprints, BERT) keep the old precise
  x-clip behavior; tight-layout papers stop leaking.

### Added

- Word2Vec (arXiv 1301.3781 v1↔v3) and Seq2Seq (arXiv 1409.3215 v1↔v3)
  as benchmark papers specifically for tight-layout regression cases.
- `benchmarks/baselines/v0.2.1.json` — 5-benchmark baseline snapshot.

### Benchmarks

Existing 3 baselines unchanged:
- arXiv 1706.03762 v1↔v5: 92.3% status / 56.4% location.
- Revised synthetic: 88.5% status / 100% location.
- BERT 1810.04805 v1↔v2: 100% status / 78.6% location.

Newly passing (were blocked at anchor-extraction before this fix):
- Word2Vec 1301.3781 v1↔v3: 100% / 100%.
- Seq2Seq 1409.3215 v1↔v3: 100% / 100%.

### Deferred

- `section_sim` stays experimental. No change in this release.
- arXiv 1706's 11 short-token large-shift failures
  (BLEU / WMT 2014 / Multi-Head Attention in the Results section)
  remain unresolved — that is a separate "repeated short-token
  relocation" problem, not a tight-layout problem.

## [0.2.0] — 2026-04-21

`pdfanno diff` shipped — Week 1 PoC through Week 3 section-aware scoring.
Migrate annotations between two PDF versions with explicit status
(`preserved` / `relocated` / `changed` / `broken`) and stable per-anchor
identity across the migration.

### Added

- **`pdfanno diff OLD.pdf NEW.pdf`** — produces a `DiffReport` with one
  `DiffResult` per old anchor, including its best-match new position
  (quads), status, confidence, and per-signal reasoning.
- `--json` / `--diff-out FILE` / `--page-window N` flags.
- Anchor model in `pdfanno/diff/types.py` with frozen identity fields
  and per-anchor `occurrence_rank` / `total_occurrences` / `section_path`.
- `pdfanno/diff/match.py` — 5-signal scorer: text (0.40), context (0.30),
  layout (0.15, further decomposed into section / rank / y / x), page
  proximity (0.10), length (0.05). Greedy 1:1 assignment after Hungarian
  allocator regressed the baseline and was reverted (see
  `pdfanno/diff/_hungarian.py` docstring).
- `pdfanno/diff/sections.py` — section detection via `doc.get_toc()` then
  font-size heuristic; `SectionSpan.path` carries ancestor chain.

### Benchmarks (see `benchmarks/reports/`)

- **arXiv 1706.03762 v1→v5**: 92.3% status accuracy / 56.4% location
  accuracy (same-page 15pt threshold). 11 failure cases are cross-page
  / large-shift short-token repeats (BLEU, WMT, Multi-Head Attention).
- **Synthetic revised manuscript**: 88.5% status / 100% location across
  26 preserved / relocated / changed / broken cases.

### Experimental

- **`section_sim` is experimental in v0.2.0.** The signal is wired into
  `layout_score` (weight 0.20, see `match.W_LAYOUT_SECTION`) and verified
  directionally correct by `tests/test_cross_section.py`, but no
  benchmark in this release demonstrates end-to-end decision-flipping on
  cross-section short-token ambiguity. `_context_window` extracts the
  anchor's context from the first match position rather than the anchor's
  own position, which introduces ctx asymmetry that section_sim can't
  overcome at current weights. Fix + judicial test land in v0.2.1.
  Toggle off with `PDFANNO_DISABLE_SECTION_SIM=1` if needed.

### Deferred

- Large same-page shifts on repeated short tokens (the dominant arXiv
  failure class) → Week 4 / v0.2.1.
- Semantic (embedding-based) similarity → later; blocked on
  deps/speed/reproducibility analysis.

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

### Dependencies

- Python 3.12+
- PyMuPDF ≥ 1.24 (AGPL-3.0)
- Typer ≥ 0.12
- Pydantic ≥ 2.7
