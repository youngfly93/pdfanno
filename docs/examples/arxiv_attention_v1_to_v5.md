# Example: migrating highlights across arXiv 1706.03762 v1 → v5

"Attention Is All You Need" went through five public versions on arXiv.
Someone who annotated v1 in 2017 now wants to carry their highlights to v5.
This page walks the actual `pdfanno diff` run on that pair: 39 highlights,
3 minutes.

All numbers below are from a real invocation — no hand-authored snippets.

## Setup

- `v1_hl.pdf` — v1 of the paper with 39 highlights already written in
  (titles, key section headings, "BLEU", "Multi-Head Attention", etc).
- `v5.pdf` — the final arXiv version.

```bash
pdfanno diff v1_hl.pdf v5.pdf --diff-out attention_v1_to_v5.diff.json
```

Terminal output:

```
wrote diff report to attention_v1_to_v5.diff.json
diff: total=39 preserved=10 relocated=29 changed=0 ambiguous=0 broken=0
  [preserved] conf=1.00 page 0 -> 0 | Exact match at same location on page 0.
  ...
  [relocated] conf=0.95 page 1 -> 1 | Same page, shifted location (quad center moved 110.9 pt, ...).
  [relocated] conf=0.62 page 3 -> 3 | Same page, shifted location (quad center moved 578.7 pt, ...).
  [relocated] conf=0.62 page 6 -> 7 | Exact match on page 7 (was 6).
  ...
```

30 seconds in, you already know: most highlights tracked cleanly, but a
handful of `relocated` results have confidence below 0.75 and deserve a
look.

## Three archetypal cases

### A. `preserved` — title on page 0

```json
{
  "status": "preserved",
  "confidence": 1.0,
  "old_anchor": { "page_index": 0, "selected_text": "Attention Is All You Need", ... },
  "new_anchor": { "page_index": 0, ... },
  "match_reason": {
    "selected_text_similarity": 1.0,
    "context_similarity": 1.0,
    "layout_score": 1.0,
    "length_similarity": 1.0,
    "page_delta": 0
  }
}
```

All five signals at 1.0 — the title literally did not move. Safe to apply
blindly.

### B. `relocated`, high confidence — "Multi-Head Attention" on page 1

```json
{
  "status": "relocated",
  "confidence": 0.949,
  "old_anchor": { "page_index": 1, "selected_text": "Multi-Head Attention", "occurrence_rank": 0, "total_occurrences": 8, ... },
  "new_anchor": { "page_index": 1, ... },
  "match_reason": {
    "selected_text_similarity": 1.0,
    "context_similarity": 0.998,   // almost identical surrounding text
    "layout_score": 0.665,         // position within the page shifted
    "length_similarity": 1.0,
    "page_delta": 0
  },
  "message": "Same page, shifted location (quad center moved 110.9 pt, threshold 15.0)."
}
```

Text is unchanged, context is nearly identical (the paragraph was
reflowed), page did not change. Layout dropped because the quad moved
~110 pt down the page. This is the textbook "relocated, trust it"
outcome — `confidence ≥ 0.90`, auto-apply is fine.

### C. `relocated`, low confidence — the one that needs review

The paper has **8 occurrences of "Multi-Head Attention"** across 3 pages.
One of them sits in the caption of Figure 2 on page 3. In v5 the figure
moved; v1's anchor in the caption now finds a scorer-chosen position on
the same page but **578 pt away**.

```json
{
  "annotation_id": "anc_68ab8fb01865d5a4",
  "status": "relocated",
  "confidence": 0.62,
  "old_anchor": {
    "page_index": 3,
    "selected_text": "Multi-Head Attention",
    "occurrence_rank": 3,
    "total_occurrences": 8,
    "section_path": "3 Model Architecture / 3.2 Attention / 3.2.3 Applications of Attention in our Model"
  },
  "new_anchor": { "page_index": 3, ... },
  "match_reason": {
    "selected_text_similarity": 1.0,
    "context_similarity": 0.008,   // ctx basically doesn't match
    "layout_score": 0.451,         // below neutral
    "length_similarity": 1.0,
    "page_delta": 0
  },
  "message": "Same page, shifted location (quad center moved 578.7 pt, threshold 15.0)."
}
```

Two things to notice:

1. `selected_text_similarity = 1.0` with `context_similarity = 0.008` is
   a loud signal. The exact string exists 8 times on 3 pages; the
   scorer picked one, but the surrounding paragraph is completely
   different from where v1 had it. Text alone cannot disambiguate.
2. `confidence = 0.62` reflects that — the scorer is not claiming to
   be sure.

This is a case a human should see before writing into v5. It is an
instance of the **repeated-short-token** failure class documented in
`benchmarks/reports/v0.2_scorer_summary.md`: when the selected text is
short and recurring, `pdfanno diff` cannot always tell which occurrence
is semantically "the same one".

## How to read `confidence` in practice

| Range | Recommendation |
|---|---|
| ≥ 0.90 | Safe to auto-apply. |
| 0.75 – 0.90 | Quick glance: is the new page/section plausible? |
| < 0.75 | Should not be auto-applied — expect to review. |

For this example:

```bash
# Everything worth reviewing in 3 lines.
jq '.results[] | select(.confidence < 0.75) | {
  text: .old_anchor.selected_text,
  old_page: .old_anchor.page_index,
  new_page: .new_anchor.page_index,
  conf: .confidence,
  msg: .message
}' attention_v1_to_v5.diff.json
```

Output (8 items out of 39; one of each class shown):

```jsonc
{ "text": "residual connection",    "old_page": 1, "new_page": 2, "conf": 0.669, "msg": "Exact match on page 2 (was 1)." }
{ "text": "Multi-Head Attention",   "old_page": 3, "new_page": 3, "conf": 0.620, "msg": "Same page, shifted location (quad center moved 578.7 pt, threshold 15.0)." }
{ "text": "positional encoding",    "old_page": 4, "new_page": 5, "conf": 0.698, "msg": "Exact match on page 5 (was 4)." }
{ "text": "BLEU",                   "old_page": 6, "new_page": 7, "conf": 0.620, "msg": "Exact match on page 7 (was 6)." }
```

All four are **repeated short tokens** — "residual connection",
"Multi-Head Attention", "positional encoding", "BLEU" each occur
multiple times across nearby pages. The scorer picked a candidate on the
next page but could not get context to agree. A human can judge this in
seconds; the tool should not.

## What to do with the reviewed results

Right now, the review step is manual — open v1 + v5 side by side, look at
each low-confidence hit, decide keep / drop / relabel. For the confident
majority:

```bash
# Everything that's ready to apply without review (30 of 39 here).
jq '.results[] | select(.confidence >= 0.90)' attention_v1_to_v5.diff.json
```

Converting those into an `AnnotationPlan` and running `pdfanno apply v5.pdf
plan.json -o v5.annotated.pdf` is a ~20-line Python script today. A
built-in "migrate" command is on the roadmap.

## Known limitations demonstrated here

- **Repeated short tokens.** 4 of the 8 low-confidence results on this
  paper. Long text selections ("Attention Is All You Need", a sentence
  from the abstract) converge on `confidence = 1.0`; 2-4 word jargon that
  recurs does not.
- **Context anchoring across same-token anchors.** v1's 3 highlights of
  "BLEU" on pages 6/7 each see the *first-occurrence* context of that
  page when scoring — so disambiguation within the group leans on
  `layout_score` (section + y/x) alone. This is a documented design
  trade-off (see `week4_ctx_experiment.md`); attempting to fix it
  regressed the overall benchmark.

Neither limitation produces wrong output silently — both surface as
`confidence < 0.75` and get filtered by the review workflow above.

## Coming next: `pdfanno review diff.json`

The JSON above is currently reviewed manually — open both PDFs, scan
context strings, decide. A built-in terminal workflow for this is the
next release:

```bash
pdfanno review attention_v1_to_v5.diff.json
# For each result (low-confidence first):
#   y = accept (include in migration plan)
#   n = reject (drop)
#   r = defer (mark review_required)
#   q = save progress and quit
# Writes attention_v1_to_v5.review.json with a `decisions` field.
```

No PDF rendering — just the old/new `selected_text` + `context_before` /
`context_after` + `match_reason` presented in a terminal. The PDF reader
TUI (Phase 2) remains a separate, larger piece of work.

## See also

- [`docs/diff.md`](../diff.md) — full `diff` reference (statuses,
  confidence, JSON schema, env toggles).
- [`benchmarks/reports/v0.2_scorer_summary.md`](../../benchmarks/reports/v0.2_scorer_summary.md)
  — why the repeated-short-token failure class exists.
- `pdfanno/diff/types.py` — authoritative schema for the JSON.
