---
name: verify
description: Run ruff formatting check, ruff lint, and pytest on the pdf_annotion project. Use before marking any task complete, or when the user asks to verify/check/validate their changes.
---

Run the project's verification chain and report results.

## Steps

1. `ruff format --check .` — fail if anything is unformatted. If it fails, show the user the diff (`ruff format --diff .`) and ask whether to apply `ruff format .`.
2. `ruff check .` — lint. Report violations grouped by rule code.
3. `pytest` — full test suite. If `pytest` isn't installed yet or `tests/` doesn't exist (project is in planning phase), say so and skip without treating it as a failure.

## Conventions

- Run from the project root (the directory containing `plan.md`).
- Chain the commands with `&&` so the first failure stops execution — but still report which step failed.
- Don't auto-fix lint violations or reformat files without asking first.
- If `ruff` isn't on PATH, suggest `pip install ruff` rather than silently skipping.
