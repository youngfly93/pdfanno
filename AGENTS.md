# Repository Guidelines

## Project Structure & Module Organization

This repository is currently planning-stage; `plan.md` is the source of record for the intended architecture. Implement code in small, purpose-built packages:

- `pdf_core/`: PDF opening, rendering, text extraction, search, and annotation read/write using PyMuPDF.
- `viewport/`: coordinate transforms between PDF points, rendered pixels, and terminal cells; page/text caches.
- `store/`: SQLite sidecar storage for drafts, undo state, synchronization, and annotation metadata.
- `tui/`: Textual application, page view, command mode, panels, and note editor.
- `cli/`: command-line entry points such as `pdfhl`.
- `tests/`: unit and integration tests, mirroring package paths.
- `assets/` or `fixtures/`: sample PDFs and rendering fixtures; keep large or copyrighted PDFs out of Git.

## Build, Test, and Development Commands

Until project metadata is added, prefer standard Python commands:

- `python -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `python -m pip install -e ".[dev]"`: install the package once `pyproject.toml` exists.
- `pytest`: run the full test suite.
- `pytest tests/pdf_core/test_annotations.py -q`: run a focused annotation test.
- `textual run tui.app:PdfAnnotionApp`: run the TUI during development after the app is scaffolded.

## Coding Style & Naming Conventions

Target Python 3.12+. Use 4-space indentation, type hints for public functions, and dataclasses or typed dictionaries for structured annotation data. Keep PDF coordinates in PDF space internally; convert to screen coordinates only at UI boundaries. Use `snake_case` for modules, functions, variables, and CLI commands; use `PascalCase` for classes. Prefer clear module names such as `annotations.py`, `transform.py`, and `command_mode.py`.

## Testing Guidelines

Use `pytest`. Name test files `test_*.py` and test functions `test_*`. Cover coordinate transforms, search-to-quad conversion, sidecar persistence, and PDF annotation export. Add regression fixtures for encrypted, scanned, multi-column, rotated, and text-layer PDFs when possible. Tests that mutate PDFs should write to temporary paths, never overwrite fixtures.

## Commit & Pull Request Guidelines

No Git history is present in this checkout, so use concise imperative commits, for example `Add sidecar annotation schema` or `Fix quad transform for rotated pages`. Pull requests should include a short behavior summary, tests run, affected modules, and screenshots or terminal recordings for TUI changes. Link related issues or design notes from `plan.md` when relevant.

## Security & Configuration Tips

Treat PDFs as untrusted input. Avoid overwriting originals by default; prefer sidecar saves or exported copies. Do not commit private papers, generated annotated PDFs, local SQLite databases, or secrets.
