# Synthetic diff stress report

- corpus root: `/tmp/pdfanno-stress-synthetic`
- scenarios: 16
- expected annotations: 87
- page_window: 3
- runtime: 815.5 ms
- total findings: 0
- known-hard findings: 0

## Scenario coverage

| scenario | annotations | results | status counts | findings | runtime |
|---|---:|---:|---|---:|---:|
| basic_mixed | 4 | 4 | preserved=1, relocated=1, changed=1, broken=1 | 0 | 18.8 ms |
| page_insert_shift | 3 | 3 | relocated=3 | 0 | 7.9 ms |
| same_page_geometry | 3 | 3 | preserved=1, relocated=2 | 0 | 12.9 ms |
| two_column_reorder | 3 | 3 | relocated=3 | 0 | 11.6 ms |
| multiline_quad | 2 | 2 | preserved=1, relocated=1 | 0 | 14.4 ms |
| tight_layout | 3 | 3 | preserved=3 | 0 | 13.0 ms |
| repeated_short_tokens | 3 | 3 | preserved=1, relocated=1, broken=1 | 0 | 11.3 ms |
| punctuation_hyphenation | 3 | 3 | changed=3 | 0 | 6.5 ms |
| case_only_edit | 1 | 1 | changed=1 | 0 | 3.1 ms |
| near_duplicate_deleted | 4 | 4 | preserved=2, broken=2 | 0 | 15.2 ms |
| rotated_page | 2 | 2 | preserved=2 | 0 | 5.7 ms |
| annotation_kinds | 3 | 3 | preserved=3 | 0 | 7.6 ms |
| unsupported_annotations | 2 | 2 | unsupported=2 | 0 | 2.6 ms |
| far_page_movement | 1 | 1 | relocated=1 | 0 | 7.2 ms |
| no_annotations | 0 | 0 | - | 0 | 3.4 ms |
| high_volume | 50 | 50 | preserved=32, relocated=12, broken=6 | 0 | 674.1 ms |

## Finding types

No findings.

## Findings

No mismatches found against synthetic expectations.

## Behavior probes

| probe | page_window | status | new_page | confidence | message |
|---|---:|---|---:|---:|---|
| far_page_movement | 0 | relocated | 5 | 0.600 | Exact match on page 5 (was 0). |
| far_page_movement | 1 | relocated | 5 | 0.600 | Exact match on page 5 (was 0). |
| far_page_movement | 3 | relocated | 5 | 0.600 | Exact match on page 5 (was 0). |
| far_page_movement | 8 | relocated | 5 | 0.637 | Exact match on page 5 (was 0). |

## Scenario notes

- `basic_mixed`: single-column preserved / relocated / changed / broken baseline
- `page_insert_shift`: whole-document page insertion shifts all targets by one page
- `same_page_geometry`: same-page y-shift, near-threshold shift, and column transfer
- `two_column_reorder`: two-column x/y relocation without changing text
- `multiline_quad`: phrases whose highlights consist of multiple quads across wrapped lines
- `tight_layout`: dense line spacing designed to expose selected_text leakage
- `repeated_short_tokens`: three identical short-token highlights with one removed and one moved
- `punctuation_hyphenation`: small textual edits involving hyphenation, numbers, and punctuation
- `case_only_edit`: case-only text edit; PyMuPDF search_for is case-insensitive for ASCII
- `near_duplicate_deleted`: deleted sentences differ from surviving sentences by only a small number token
- `rotated_page`: 90-degree rotated page with preserved text highlights
- `annotation_kinds`: underline / strikeout / squiggly text-coverage annotations
- `unsupported_annotations`: non-text-coverage annotations should exercise the advertised unsupported status
- `far_page_movement`: exact relocation beyond the default nominal page window
- `no_annotations`: empty annotation set; verifies zero-anchor reports do not crash
- `high_volume`: 50 annotations mixing preserved, relocated, and broken cases

## Initial interpretation

- The `unsupported_annotations` scenario asserts that non-text annotations produce the advertised `unsupported` status.
- The `case_only_edit` scenario asserts that PyMuPDF's ASCII case-insensitive `search_for` behavior does not turn case-only edits into `preserved`.
- The `high_volume` scenario guards against fuzzy false positives on near-duplicate deleted text. Deleted numbered sentences must not reuse surviving exact anchors' text slots.
- Treat `known hard` repeated-token findings separately from regressions. These are the Week 6-11 scorer boundary cases made reproducible in a small fixture.
- The far-page probe checks whether `page_window` behaves as a hard search window or only as a scoring input.
