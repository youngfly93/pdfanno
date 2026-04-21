# Week 1 PoC Spike: Attention Is All You Need v1 → v5

**Date**: 2026-04-21
**Input**: arXiv 1706.03762, v1 vs v5 (both 15 pages, ~2 MB each)
**Commit**: `fb7cbe0` on `v0.2-diff`
**Tool**: `pdfanno diff` (Week 1 PoC)

## Setup

Applied 10 queries as highlights to v1 via `pdfanno highlight`, producing 39
annotations spread across all pages. Ran `pdfanno diff v1_hl.pdf v5.pdf --json`.

## Headline numbers

| Status | Count | % |
|---|---:|---:|
| preserved | 24 | 61% |
| relocated | 15 | 39% |
| broken | 0 | 0% |
| runtime | ~1 s | |

All 15 relocated hits share `page_delta=+1`, consistent with v5 inserting a page
of content early in the document and the rest shifting down.

## Per-query breakdown

| Query | Total | preserved | relocated | broken |
|---|---:|---:|---:|---:|
| BLEU | 11 | 8 | 3 | 0 |
| WMT 2014 | 7 | 6 | 1 | 0 |
| positional encoding | 5 | 1 | 4 | 0 |
| Multi-Head Attention | 4 | 4 | 0 | 0 |
| Scaled Dot-Product Attention | 4 | 2 | 2 | 0 |
| residual connection | 3 | 0 | 3 | 0 |
| byte-pair encoding | 2 | 1 | 1 | 0 |
| Attention Is All You Need | 1 | 1 | 0 | 0 |
| Adam optimizer | 1 | 1 | 0 | 0 |
| label smoothing | 1 | 0 | 1 | 0 |

## Hard findings (must fix in Week 2)

### 1. `preserved` false positives on short tokens

`BLEU` is 4 characters. In v5 the same page very likely holds multiple `BLEU`
occurrences. Current algorithm judges `preserved` purely by "`norm_sel in
page.normalized`" — it cannot distinguish "the original highlight really did
not move" from "something matches on the same page but it's a different
occurrence of the same token". All 8 `BLEU` preserved results are at risk.

**Week 2 fix**: before declaring `preserved`, require the quad center (or
bbox of the matched span) to be within a small geometric threshold of the old
quad center. If the same-page match lives far from the old anchor, downgrade
to `changed` or the multi-candidate path.

### 2. Candidate-to-anchor allocation is not 1:1

v1 has 3 highlights of `residual connection` on page 1. All three are
`relocated` to v5 page 2. The matcher returns the *first* occurrence of the
substring and has no mechanism to consume a candidate so later anchors pick a
different one. Result: 3 v1 annotations are mapped to the same v5 position.
On migrate this would produce 3 highlights stacked on one span.

**Week 2 fix**: build a candidate pool for each old anchor, and when a
candidate is assigned, remove it from the pool for subsequent anchors (simple
greedy or Hungarian-style assignment). Scoring order matters: process
highest-confidence anchors first.

### 3. `new_anchor.quads` is empty

Every matched `DiffResult.new_anchor` has `quads: []`. We know the page and
matched text, but migration (Week 4-5) needs the geometry to write the
highlight back. The PoC deferred this.

**Week 2 fix**: after a candidate is confirmed, rerun
`new_page.search_for(matched_text, quads=True)` (or derive from word-level
offsets) and populate `new_anchor.quads`. This also feeds the 1:1 allocation
in finding #2 — we can compare geometry, not just presence.

## Soft findings (Week 2 will pick these up anyway)

- `match_reason.context_similarity` and `layout_score` are always 0 (not
  computed in the PoC). PRD §8.3 weights them at 30% + 15% combined;
  Week 2's context/layout scoring work already has this on the list.
- Broken-rate of 0 on this specific pair is expected because v1→v5 is the
  same paper's revisions and text changes are minimal. A harsher pair (e.g.,
  biorxiv manuscript before/after peer review) should be added to the eval
  set to stress `broken` and `changed` buckets.

## Implication for Week 2 prioritization

The 3 hard findings reorder the PRD §10 Week 2-3 plan:

1. **First**: implement candidate-pool + 1:1 allocation + quad reconstruction
   in the match layer. Without these, context/layout scoring improvements
   will be on top of a wrong match in many cases.
2. **Second**: add bbox/quad proximity check to `preserved`. Short-token
   false positives are the single biggest blow to trust in the algorithm.
3. **Third**: context_similarity + layout_score per PRD §8.3. These will
   mostly help the `ambiguous`/`changed` buckets, which are still 0 here —
   we need harsher fixtures before this matters.

## Fixture to add to the locked eval set

The v1_hl.pdf + v5.pdf pair, with its 39 curated annotations per the queries
above, becomes fixture #1 of the eval set. Category: "minor revision, minor
pagination shift, no deletions". Known-true distribution:

- preserved: entries where v1 and v5 pages + quads coincide (TBD by manual
  annotation once the bbox check lands).
- relocated: entries where the same textual content persists but its page
  shifted by +1.
- broken: 0 expected.
- changed: 0 expected.

## Raw data

Diff report saved to `/tmp/pdfanno_arxiv_spike/diff.json`
(39 entries, ~74 KB). Not committed to the repo — regenerate from the
session fixtures as needed.

---

## Week 2 rerun (after H1 fixes)

Algorithm rewritten per §"Hard findings" above: candidate pool, global 1:1
greedy assignment, quad reconstruction from `search_for(..., quads=True)`,
and quad-proximity check for `preserved`.

| Metric | Week 1 PoC | Week 2 H1 |
|---|---:|---:|
| preserved | 24 (61%) | **11 (28%)** |
| relocated | 15 (39%) | **28 (72%)** |
| broken | 0 | 0 |
| 1:1 assignment | ❌ (3× `residual connection` → same v5 position) | ✅ 39 anchors → 39 unique (page, cx, cy) |
| `new_anchor.quads` populated | 0 / 39 | **39 / 39** |

Per-query redistribution shows the algorithm now surfaces the shift for
every query where v5 actually moved the text:

| Query | Week 1 (preserved / relocated) | Week 2 (preserved / relocated) |
|---|---|---|
| BLEU (×11) | 8 / 3 | 6 / 5 |
| WMT 2014 (×7) | 6 / 1 | 2 / 5 |
| positional encoding (×5) | 1 / 4 | **0 / 5** |
| residual connection (×3) | 0 / 3 (all → same position) | **0 / 3 (3 distinct positions)** |

The `preserved` count dropping from 24 → 11 is not a regression — it's the
algorithm no longer rubber-stamping same-page substring hits as unchanged.
Each of the 13 downgrades corresponds to either a quad that genuinely shifted
or a short-token false positive that has now been correctly claimed by the
1:1 allocator for a different anchor.

---

## Week 2 H2 rerun (after context_similarity + changed status)

Scoring now uses PRD §8.3 weights: 0.40 text + 0.30 context + 0.10 proximity,
normalized by `/ 0.80` since layout (0.15) and length (0.05) are still
deferred. `context_similarity` computed as mean of SequenceMatcher.ratio on
±300 char before/after. `changed` status added for fuzzy-text + strong-context
cases. Fuzzy matching switched from "longest common block / needle length" to
"align longest block then full ratio on equal-length window" — one-digit edits
now score ~0.98 instead of ~0.53.

### Status counts unchanged

| | H1 | H2 |
|---|---:|---:|
| preserved | 11 | 11 |
| relocated | 28 | 28 |
| changed | 0 | 0 |
| broken | 0 | 0 |

Expected: v5 of this paper repaginated content without in-place text edits, so
`changed` is correctly absent. To exercise `changed`, the eval set needs a
biorxiv-style revised-manuscript fixture.

### Context discriminates what H1 could not

Per-query mean `context_similarity` (sorted desc):

| Query | N | mean context_sim |
|---|---:|---:|
| Attention Is All You Need | 1 | 1.00 |
| Adam optimizer | 1 | 0.98 |
| byte-pair encoding | 2 | 0.87 |
| WMT 2014 | 7 | 0.69 |
| label smoothing | 1 | 0.58 |
| Multi-Head Attention | 4 | 0.51 |
| BLEU | 11 | 0.45 |
| Scaled Dot-Product Attention | 4 | 0.44 |
| residual connection | 3 | 0.39 |
| positional encoding | 5 | 0.36 |

The short / frequently-repeated tokens (BLEU, residual connection, positional
encoding) now carry honest low-ish confidence when they match mid-confidence
candidates. Previously they all carried 0.95 regardless of whether the
matched occurrence was "the one" from v1.

### Confidence distribution is now honest

Bottom-5 confidence results post-H2:

- 0.604 relocated: BLEU -> p7
- 0.610 relocated: residual connection -> p2
- 0.611 relocated: BLEU -> p7
- 0.625 relocated: positional encoding -> p4
- 0.628 relocated: Multi-Head Attention -> p3

These are the exact items a user should review: short tokens on pages with
many duplicates and only weak context carryover from v1. Pre-H2 they were
all locked at 0.95 with no signal that they might be wrong.

### Still deferred (Week 2 H3 and Week 3)

- `layout_score` (15% weight in PRD §8.3): relative y-position / column /
  line-span / block-order similarity. Will help reorder candidates that tie
  on text+context.
- `length_similarity` (5% weight).
- Ground-truth labels for this 39-annotation pair so it can join the locked
  eval set and produce honest precision/recall numbers per PRD §9.2.
- Anchor ID still always synthesizes `anc_*`; should prefer existing `/NM`
  before falling back for pdfanno-created anchors (finding #4 from Week 1
  spike, unchanged).
- Biorxiv revised-manuscript fixture to exercise `changed` / `ambiguous`.
