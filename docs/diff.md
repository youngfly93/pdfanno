# Migrating annotations across PDF versions (`pdfanno diff`)

When a paper gets a revision, you usually have a stash of highlights and
sticky notes tied to the old version. You don't want to throw them away; you
also don't want to trust a naive text search that silently puts your "BLEU"
highlight onto a random table cell. `pdfanno diff` is the dedicated tool for
this problem.

This doc walks through:

1. What `diff` does (and what it refuses to do).
2. How to read the output — status and confidence.
3. A concrete workflow: old PDF + new PDF → JSON → decisions.
4. How agents are expected to consume the JSON.
5. Known limitations and failure modes.
6. Research-only env switches.

---

## 1. What `diff` does

```
pdfanno diff OLD.pdf NEW.pdf [--json | --diff-out FILE] [--page-window N]
```

Inputs:

- `OLD.pdf` — has the annotations you want to migrate (possibly created by
  `pdfanno highlight`, possibly by another reader).
- `NEW.pdf` — the revised version. Never modified by `diff`.

For each annotation in `OLD.pdf`, `diff` tries to locate the corresponding
content in `NEW.pdf` and emits a `DiffResult` with:

- `status` — one of `preserved` / `relocated` / `changed` / `ambiguous` /
  `broken` / `unsupported`.
- `confidence` — `[0, 1]`, a weighted combination of text / context / layout
  / page proximity / length similarity.
- `new_anchor` — the page + quads in `NEW.pdf` that the match points to
  (null for `broken`).
- `match_reason` — per-signal breakdown, for debugging.

`diff` **does not write any annotations**. It just tells you what would map
where. The next step (`apply`, sidecar edits, or human review) is explicitly
your call.

### What it refuses to do

- Does not guess between multiple plausible candidates silently. When
  candidates score within a small epsilon of each other, the status is
  `ambiguous` with `review_required=true`.
- Does not assume "same page" means "same location". `preserved` requires
  both same page **and** the quad centers to be within ~15 pt.
- Does not modify either PDF or any sidecar. It's a read-only tool that
  emits JSON.

---

## 2. Reading the output

### Statuses

| Status | Meaning | Typical confidence |
|---|---|---|
| `preserved` | Text still present on the same page at essentially the same position. | ≥ 0.90 |
| `relocated` | Text present, but moved to a different page or a different region. | 0.60 – 0.95 |
| `changed` | Surrounding context is recognizably edited; text may still be there but in a different phrasing. | 0.50 – 0.80 |
| `ambiguous` | Two or more candidates with close scores; tool refuses to pick one for you. | tie-bearing candidates |
| `broken` | No plausible match found, or best candidate has context similarity too low. | < 0.50 |
| `unsupported` | Reserved for future: annotations whose kind `diff` cannot migrate (e.g. freehand ink). | — |

### Confidence

`confidence` combines five signals. The current default weights are:

| Signal | Weight | What it measures |
|---|---|---|
| `selected_text_similarity` | 0.40 | Character-level similarity between old and new matched text. |
| `context_similarity` | 0.30 | Similarity of the text surrounding the anchor (±CONTEXT_CHARS on each side). |
| `layout_score` | 0.15 | Combination of section / rank / y / x position alignment. |
| `page_delta` (proximity) | 0.10 | Distance between the old page and the new candidate page. |
| `length_similarity` | 0.05 | Ratio of selected-text lengths. |

Both the weights and the signals are exposed on `MatchReason` for every
result, so you can build your own decision rule on top. As a rule of thumb:

- `confidence ≥ 0.90` is safe to apply automatically.
- `0.70 – 0.90` warrants a quick human glance.
- `< 0.70` should not be auto-applied to the user's working PDF without
  review.

---

## 3. A concrete workflow

> For a fully worked walkthrough on a real paper with 39 annotations, see
> [`examples/arxiv_attention_v1_to_v5.md`](examples/arxiv_attention_v1_to_v5.md).

Say you have `paper_v1.pdf` (with ~30 highlights) and `paper_v2.pdf` (the
revised version). You want to carry highlights forward.

### Step 1 — run `diff`

```bash
pdfanno diff paper_v1.pdf paper_v2.pdf \
  --diff-out paper_v1_to_v2.diff.json
```

Terminal output (human-readable summary):

```
wrote diff report to paper_v1_to_v2.diff.json
diff: total=30 preserved=22 relocated=5 changed=1 ambiguous=0 broken=2
  [preserved] conf=0.98 page 0 -> 0 | same-page exact match
  [preserved] conf=0.97 page 1 -> 1 | same-page exact match
  [relocated] conf=0.86 page 2 -> 3 | cross-page relocation
  [relocated] conf=0.82 page 3 -> 3 | same-page position shift
  [changed]   conf=0.71 page 4 -> 4 | ctx changed, text still present
  [broken]    conf=0.35 page 5 -> -  | no candidate passed ctx floor
  ...
```

### Step 2 — review the JSON

The JSON report (`paper_v1_to_v2.diff.json`) contains everything the
terminal summary shows plus full `match_reason` breakdowns. Here is a
representative single `DiffResult`:

```jsonc
{
  "annotation_id": "2d71c6c4b24ca04985546051c6e295330280e8e91b214664ab755605b805ffc4",
  "status": "relocated",
  "confidence": 0.8617,
  "old_anchor": {
    "annotation_id": "2d71c6c4...ffc4",
    "doc_id": "id:6a43c29b17151dc2821dc38706681260",
    "kind": "highlight",
    "page_index": 2,
    "quads": [[72.12, 144.34, 130.55, 144.34, 72.12, 158.02, 130.55, 158.02]],
    "selected_text": "self-attention",
    "context_before": "... we now introduce ",
    "context_after": " as the mechanism used in ...",
    "section_path": "3 Model Architecture / 3.2 Attention",
    "occurrence_rank": 0,
    "total_occurrences": 4
  },
  "new_anchor": {
    "page_index": 3,
    "quads": [[73.01, 210.04, 131.62, 210.04, 73.01, 223.70, 131.62, 223.70]],
    "matched_text": "self-attention"
  },
  "match_reason": {
    "selected_text_similarity": 1.0,
    "context_similarity": 0.71,
    "layout_score": 0.82,
    "length_similarity": 1.0,
    "page_delta": 1,
    "candidate_rank": 0
  },
  "review_required": false,
  "message": "cross-page relocation"
}
```

You can filter by status / confidence with `jq`:

```bash
# Only the items that need human eyes.
jq '.results[] | select(.review_required or .confidence < 0.75)' \
  paper_v1_to_v2.diff.json

# Sanity-check: any broken annotations?
jq '.results[] | select(.status == "broken") | {text: .old_anchor.selected_text, page: .old_anchor.page_index}' \
  paper_v1_to_v2.diff.json

# Summary counts
jq '.summary' paper_v1_to_v2.diff.json
```

### Step 3 — decide per annotation

There are three ways to use the output:

1. **Auto-migrate the confident ones.** Filter to `confidence ≥ 0.90` +
   `status ∈ {preserved, relocated}` and hand them to your own application
   layer (or to `pdfanno apply` after converting to an `AnnotationPlan`).
2. **Review the uncertain ones by hand.** Look at the PDFs and
   `context_before` / `context_after` side by side. We will provide a
   terminal review flow (`pdfanno review`) — see §6.
3. **Accept the verdict as a report.** For archival / diff-of-diffs work,
   the JSON is the deliverable.

`diff` deliberately does **not** have a `--apply` flag that mutates your new
PDF in-place. Writing annotations is `apply`'s job, and we want the two
decisions separated (detection vs. writeback).

### Step 4 — converting accepted results into an `AnnotationPlan`

If you want to write the accepted migrations back into `paper_v2.pdf`, you
will need to:

1. Drop `broken` / `ambiguous` / low-confidence results.
2. Convert each `(new_anchor.page_index, new_anchor.quads, old_anchor.kind,
   old_anchor.color, ...)` into an `AnnotationRecord` under a new
   `AnnotationPlan`.
3. Run `pdfanno apply paper_v2.pdf plan.json -o paper_v2.annotated.pdf`.

A helper command for this conversion is the natural next shipping piece,
but at v0.2.x it is still a manual (~20-line Python) step. The schemas for
both sides are stable and documented in `plan.md` §8.

---

## 4. Agent-facing contract

The full schema is in `pdfanno/diff/types.py`. Forward-compatibility rules:

- `schema_version` starts at `2` for `diff` output (`1` was the legacy
  `AnnotationPlan`). Agents should assert on `schema_version` before
  consuming unfamiliar shapes.
- All models use pydantic `extra="allow"`. Unknown keys in newer versions
  are safe to ignore.
- `status` is a `Literal[...]` of 6 strings. If you see a string outside
  this set, treat it as `unsupported` — new statuses may be added without
  a schema bump (this is the whole point of allowing `extra`).
- `new_anchor` is `null` when `status == "broken"`.
- `match_reason` is optional (`null`) for unsupported annotations.
- Quads are floats in PDF points, ordered
  `[ul_x, ul_y, ur_x, ur_y, ll_x, ll_y, lr_x, lr_y]`.

Exit codes follow the standard `pdfanno` convention (`0` / `2` / `3` /
`4` — see README "Exit codes"). `diff` does not currently define a
non-zero exit code for "some annotations were broken" — a broken result is
a normal outcome, not a failure.

---

## 5. Known limitations

These are open issues documented in
`benchmarks/reports/v0.2_scorer_summary.md`:

- **Short tokens repeated on the same page.** Anchors like "BLEU",
  "Multi-Head Attention", "Scaled Dot-Product Attention" occurring 3+ times
  per page are the dominant remaining failure class on arXiv 1706.03762.
  Six weeks of lever exploration (broken floor, concat context mode,
  ctx-aware preemption, Hungarian assignment variants) did not produce a
  default-on fix. We are sitting at 84.6% status / 76.5% location accuracy
  on this benchmark; BERT / Word2Vec / Seq2Seq are higher.
- **"First-match context" anchoring.** Internally, the context string
  used for similarity scoring is extracted from the **first** occurrence of
  the selected text on the page. This is load-bearing — per-anchor
  contexts regressed the overall benchmark by 3 – 8 percentage points when
  tried. The design trade-off is documented in `week4_ctx_experiment.md`.
  Effect: multiple anchors on the same page with the same selected text
  share a context string, and anchor-level disambiguation in that subgroup
  relies on `layout_score` (section, y/x) alone.
- **`changed` detection is rough.** The boundary between `relocated` and
  `changed` is a context-similarity threshold, not a semantic judgment.
  Substantial rewrites that preserve the exact highlighted span will often
  come back as `relocated` with moderate context similarity.
- **No multi-document support.** `diff` compares exactly two PDFs; there
  is no `diff OLD.pdf NEW_v1.pdf NEW_v2.pdf` triple-diff.
- **No OCR fallback.** If `NEW.pdf` is a scan with no text layer, every
  annotation will come back as `broken`.

---

## 6. Research-only switches

The v0.2 optimization work (Weeks 6 – 11) left several mechanisms in the
codebase as **opt-in** env toggles. They are **not** recommended for
everyday use; they exist for reproducing benchmark results and for future
research.

| Variable | Default | What it does |
|---|---|---|
| `PDFANNO_CTX_SIM_MODE` | `mean` | Switch matcher ctx similarity to `concat` (oracle-aligned). |
| `PDFANNO_BROKEN_CTX_FLOOR` | `0.0` | Force `broken` when `relocated` candidate's ctx < floor. |
| `PDFANNO_DISABLE_BROKEN_FLOOR` | unset | Force-disable the floor regardless of value. |
| `PDFANNO_CTX_AWARE_ASSIGN` | unset | Enable same-token ctx-aware preemption in the 1:1 assigner. |
| `PDFANNO_CTX_ASSIGN_EPSILON` | `0.05` | Score-closeness threshold for preemption. |
| `PDFANNO_CTX_ASSIGN_MIN_ADVANTAGE` | `0.10` | Ctx advantage required to preempt. |
| `PDFANNO_DISABLE_SECTION_SIM` | unset | Disable section subscore inside `layout_score`. |

Their individual effects are cross-benchmark non-monotonic: enabling any
one of them typically helps one benchmark while hurting another. See
`benchmarks/reports/v0.2_scorer_summary.md` for the full matrix.

---

## 7. Upcoming: `pdfanno review`

A small terminal review flow for `diff.json` is next on the roadmap. Goal:

```
pdfanno review diff.json
```

For each result, show the old anchor + new candidate snippets side-by-side
and accept a single-key decision:

- `y` — accept (include in the migration plan).
- `n` — reject (drop from the plan).
- `r` — mark for later (`review_required`).
- `q` — quit and save progress.

Output is a filtered `diff.json` with a `decisions` field attached, ready
to be turned into an `AnnotationPlan` by a helper command. It will **not**
require the full Phase 2 PDF reader TUI.

---

## 8. See also

- `README.md` — top-level overview and install.
- `plan.md` §5 / §8 — phase plan and schema definitions.
- `benchmarks/reports/v0.2_scorer_summary.md` — full rationale for the
  current scorer weights and frozen defaults.
- `pdfanno/diff/types.py` — authoritative schema.
- `pdfanno/diff/match.py` — scorer and 1:1 assigner (head-of-file
  docstrings explain each env toggle).
