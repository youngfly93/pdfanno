# Evaluation report

- diff: `/tmp/pdfanno_revised/diff_final.json`
- ground truth: `/tmp/pdfanno_revised/gt_hard.json`
- total annotations: gt=26, diff=26, paired=26
- **overall accuracy (status match)**: 88.5%
- **location accuracy** (predicted quad center within 15.0 pt of gt center, same page): 100.0% (13/13)

## Confusion matrix

| pred \ gt | preserved | relocated | changed | ambiguous | broken | unsupported |
|---|---|---|---|---|---|---|
| preserved | 5 | 1 | 0 | 0 | 0 | 0 |
| relocated | 2 | 5 | 0 | 0 | 0 | 0 |
| changed | 0 | 0 | 7 | 0 | 0 | 0 |
| ambiguous | 0 | 0 | 0 | 0 | 0 | 0 |
| broken | 0 | 0 | 0 | 0 | 6 | 0 |
| unsupported | 0 | 0 | 0 | 0 | 0 | 0 |

## Per-status precision / recall / F1

| status | tp | fp | fn | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| preserved | 5 | 1 | 2 | 83.3% | 71.4% | 76.9% |
| relocated | 5 | 2 | 1 | 71.4% | 83.3% | 76.9% |
| changed | 7 | 0 | 0 | 100.0% | 100.0% | 100.0% |
| ambiguous | 0 | 0 | 0 | — | — | — |
| broken | 6 | 0 | 0 | 100.0% | 100.0% | 100.0% |
| unsupported | 0 | 0 | 0 | — | — | — |

## Failure cases (3)

| id | text | pred_status | gt_status | pred_p | gt_p | conf | ctx_sim | gt_reason |
|---|---|---|---|---:|---:|---:|---:|---|
| anc_08384e08 | 'Statistical significance was assessed us' | relocated | preserved | 1 | 1 | 83.3% | 44.8% | hardcoded fixture label: preserved |
| anc_4c9b6805 | 'RNA sequencing libraries were prepared u' | relocated | preserved | 2 | 2 | 85.1% | 51.6% | hardcoded fixture label: preserved |
| anc_163dda1e | 'Scripts used for differential expression' | preserved | relocated | 3 | 3 | 100.0% | 61.3% | hardcoded fixture label: relocated |
