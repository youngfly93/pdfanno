# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Terminal-based PDF annotation tool (`pdfhl`). **Planning phase** — no implementation yet. The authoritative design record is `@plan.md`; contributor conventions are in `@AGENTS.md`.

## Confirmed stack

- **Python 3.12+**
- **PyMuPDF (fitz)** — PDF rendering, text extraction, annotation read/write
- **Textual** — terminal UI
- **SQLite** — sidecar store for drafts, undo, sync metadata
- **ruff** — formatter + linter (run `ruff format .` and `ruff check .`)

Don't swap libraries without updating `plan.md` first.

## Non-negotiable rules

- **Follow `plan.md` phases in order.** Don't implement features from later phases before the current one is complete. When unclear which phase applies, ask before writing code.
- **Never overwrite existing PDF annotations.** Read and preserve any annotations already present in a PDF — losing a user's prior highlights from another reader is a correctness bug, not a UX issue. Prefer sidecar saves or exported copies over in-place incremental writes, especially for encrypted/signed PDFs.
- **Keyboard-only interaction.** No mouse dependencies in the TUI — every action must have a keybinding. Don't add features that require a pointer.

## Writing style

- Comments and docstrings in **Chinese** to match `plan.md` and workspace convention.
- Keep PDF coordinates in PDF space internally; convert to pixel/cell coordinates only at UI boundaries (see `viewport/`).

## Verify before declaring done

Run `/verify` (or `ruff format --check . && ruff check . && pytest`) before marking a task complete.

## Reference

- `@plan.md` — architecture, MVP phases, rejected alternatives, technical challenges
- `@AGENTS.md` — module layout, test naming, commit/PR conventions, security tips
