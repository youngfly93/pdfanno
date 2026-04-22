# pdfanno

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0-red.svg)](LICENSE)

**Agent-friendly CLI for PDF annotation writeback.**

`pdfanno` is the missing piece between "extract PDF text for an LLM" (many
tools) and "highlight matches back into the PDF" (almost none). Search, add
highlights and sticky notes, and write them back into a PDF — deterministically,
idempotently, and with structured I/O an agent can round-trip.

## Why yet another PDF tool

At the time of 0.1.0 (April 2026) the open-source CLI/MCP ecosystem for PDFs is
read-heavy: `pdfannots`, `pdf-reader-mcp`, `pdf-agent-mcp`, `pymupdf4llm-mcp`,
`docling-mcp` all extract. None write annotations back. `pdfanno` is the
reference implementation for the writeback side, with engineering properties
tuned for agents:

- **Stable `annotation_id`** across runs, library upgrades, and save→reopen
  round-trips. Formula: `sha256(doc_id + kind + page + normalized_quads +
  normalized_matched_text + rule_hash)` with quads rounded to 2 decimal places
  in PDF points.
- **Idempotent writes.** Re-running the same command never creates duplicate
  annotations unless you pass `--allow-duplicates`.
- **Sidecar-first safety.** `--sidecar` stores drafts in a local SQLite store;
  `export` commits them to a PDF copy; the original PDF is never modified
  unless you say `--in-place`.
- **`--in-place` pre-checks.** Refuses encrypted / signed / permission-
  restricted / XFA / JavaScript PDFs before touching bytes.
- **Shared schema for `--dry-run` and `apply`.** Preview output is a valid
  input to `apply`; no drift between what you plan and what you execute.
- **`schema_version: 1` JSON contract.** Fields are stable; breaking changes
  bump the version.

## Install

```bash
pip install pdfanno
```

Requires Python 3.12 or newer. Runtime deps: PyMuPDF, Typer, Pydantic.

## Quick tour

```bash
# Highlight a word, write to a new file (original never touched).
pdfanno highlight paper.pdf "transformer" -o paper.annotated.pdf

# Preview what would happen, as structured JSON.
pdfanno highlight paper.pdf "transformer" -o out.pdf --dry-run --json

# Add a sticky note on page 3.
pdfanno note paper.pdf --page 3 --text "revisit this claim" -o paper.noted.pdf

# List existing annotations.
pdfanno list paper.annotated.pdf --json

# Extract to JSON / Markdown.
pdfanno extract paper.annotated.pdf --format json > annotations.json
pdfanno extract paper.annotated.pdf --format markdown

# Apply a plan (the JSON from --dry-run or a hand-edited version).
pdfanno apply paper.pdf plan.json -o paper.applied.pdf --dry-run --json
pdfanno apply paper.pdf plan.json -o paper.applied.pdf

# Sidecar workflow: draft now, commit later.
pdfanno highlight paper.pdf "ATP synthase" --sidecar
pdfanno note     paper.pdf --page 2 --text "key result" --sidecar
pdfanno status   paper.pdf --json
pdfanno export   paper.pdf -o paper.annotated.pdf

# Imported a PDF from another reader? Pull its annotations into the sidecar.
pdfanno import paper.with_external_highlights.pdf

# Renamed or moved the PDF? Rebind the sidecar records to the new path.
pdfanno rebind old/path/paper.pdf new/path/paper.pdf
```

## Migrating annotations across PDF versions (`diff`)

`pdfanno diff OLD.pdf NEW.pdf` compares the annotations already stored in
`OLD.pdf` against `NEW.pdf` and classifies each one as:

| Status | Meaning |
|---|---|
| `preserved` | Same page, same location (text still there, centers within ~15 pt). |
| `relocated` | Same text found, but moved to another page or position. |
| `changed` | Text around the annotation is recognizably edited. |
| `ambiguous` | Multiple candidates with close scores — flagged for review. |
| `broken` | Text no longer found in the new version (or only unrecognizable candidates). |

Each result carries a `confidence` in `[0, 1]` decomposed into five signals
(text / context / layout / page proximity / length). Agents can filter by
`status` + `confidence` and pipe the rest to human review.

```bash
# Emit a diff report (JSON) for a paper that got revised.
pdfanno diff paper_v1.pdf paper_v2.pdf --json > diff.json

# Or write directly to a file, with a human-readable summary on stderr.
pdfanno diff paper_v1.pdf paper_v2.pdf --diff-out diff.json
```

See [`docs/diff.md`](docs/diff.md) for the full migration workflow, status
decision tree, and how to consume the JSON.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. Also returned when zero matches or zero new annotations after dedup. |
| 2 | Usage error (bad flags, unknown color, page out of range, invalid plan JSON). |
| 3 | Input/file error (missing file, can't open, encrypted without password). |
| 4 | Processing error (save failed, in-place refused, partial write failure). |

## AnnotationPlan schema

`--dry-run` and `apply` share the same JSON contract (see `plan.md` §8.3):

```json
{
  "schema_version": 1,
  "doc_id": "id:6a43c29b17151dc2821dc38706681260",
  "rules": [
    {
      "rule_id": "rule-001",
      "kind": "highlight",
      "query": "transformer",
      "mode": "literal",
      "color": [1.0, 1.0, 0.0],
      "page_range": null
    }
  ],
  "annotations": [
    {
      "annotation_id": "2d71c6c4b24ca04985546051c6e295330280e8e91b214664ab755605b805ffc4",
      "rule_id": "rule-001",
      "kind": "highlight",
      "page": 0,
      "matched_text": "transformer",
      "quads": [[72.12, 144.34, 130.55, 144.34, 72.12, 158.02, 130.55, 158.02]],
      "color": [1.0, 1.0, 0.0],
      "contents": "",
      "source": "plan"
    }
  ]
}
```

Consumers should treat unknown keys as forward-compatible additions — models
are pydantic `extra="allow"`.

## Document identity

`pdfanno` never uses whole-file hashing for document identity (incremental
saves change the bytes). It uses:

1. **Primary**: PDF trailer `/ID[0]`, prefixed `id:`.
2. **Fallback**: `fb:<page_count>:<first_page_text_hash>:<file_size>`.

If you move or rename a PDF, the identity is preserved; if the PDF's content
is edited elsewhere and the `/ID` regenerates, run `pdfanno rebind`.

## Non-goals (v1)

`pdfanno` intentionally stops before:

- Regex / sentence / section-scoped matching (slated for v1.5).
- Terminal UI (TUI) for the PDF reader — Phase 2. A narrower
  `pdfanno review diff.json` TUI (just for reviewing `diff` output) is on
  deck.
- Kitty/Sixel image rendering — v0.3.0 (Phase 3).
- OCR on scanned PDFs.
- Automatic merge between sidecar drafts and externally-edited PDF annotations.
- Multi-document knowledge bases.

See [`plan.md`](plan.md) for the full product spec.

## Safety defaults

- `pdfanno` **never overwrites the input PDF** unless you pass `--in-place`.
- `--in-place` refuses encrypted, signed, permission-restricted, XFA-form,
  and JavaScript-bearing PDFs (exit code 4 with a human-readable reason).
- Repeated runs of the same command dedupe on `annotation_id` and produce
  `annotations_created: 0` on subsequent runs.
- External annotations on the PDF are preserved — `pdfanno` only manages the
  annotations it created (identified via the `/NM` field).

## License

`pdfanno` depends on [PyMuPDF](https://pymupdf.readthedocs.io/), which is
distributed under AGPL-3.0 by [Artifex](https://artifex.com/). `pdfanno` is
therefore released under **AGPL-3.0-or-later**.

> **Commercial or closed-source distribution requires either AGPL-3.0
> compliance across the combined work, or a MuPDF commercial license from
> Artifex.**

See [`LICENSE`](LICENSE). SPDX identifier: `AGPL-3.0-or-later`.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff format --check .
ruff check .
pytest -v
```

Contributor conventions live in [`AGENTS.md`](AGENTS.md). Design rationale and
phase plan in [`plan.md`](plan.md).
