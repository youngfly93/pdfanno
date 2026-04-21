# Evaluation report

- diff: `/tmp/pdfanno_arxiv_spike/diff_h2c.json`
- ground truth: `/tmp/pdfanno_arxiv_spike/gt.json`
- total annotations: gt=39, diff=39, paired=39
- **overall accuracy (status match)**: 89.7%
- **location accuracy** (predicted quad center within 15.0 pt of gt center, same page): 46.2% (18/39)

## Confusion matrix

| pred \ gt | preserved | relocated | changed | ambiguous | broken | unsupported |
|---|---|---|---|---|---|---|
| preserved | 7 | 4 | 0 | 0 | 0 | 0 |
| relocated | 0 | 28 | 0 | 0 | 0 | 0 |
| changed | 0 | 0 | 0 | 0 | 0 | 0 |
| ambiguous | 0 | 0 | 0 | 0 | 0 | 0 |
| broken | 0 | 0 | 0 | 0 | 0 | 0 |
| unsupported | 0 | 0 | 0 | 0 | 0 | 0 |

## Per-status precision / recall / F1

| status | tp | fp | fn | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| preserved | 7 | 4 | 0 | 63.6% | 100.0% | 77.8% |
| relocated | 28 | 0 | 4 | 100.0% | 87.5% | 93.3% |
| changed | 0 | 0 | 0 | — | — | — |
| ambiguous | 0 | 0 | 0 | — | — | — |
| broken | 0 | 0 | 0 | — | — | — |
| unsupported | 0 | 0 | 0 | — | — | — |

## Failure cases (12)

| id | text | pred_status | gt_status | pred_p | gt_p | conf | ctx_sim | gt_reason |
|---|---|---|---|---:|---:|---:|---:|---|
| anc_5bdb9e48 | 'WMT 2014' | relocated | relocated | 7 | 6 | 82.4% | 53.0% | page moved from 7 to 6 |
| anc_ef1075cc | 'WMT 2014' | relocated | relocated | 9 | 7 | 73.5% | 29.3% | page moved from 9 to 7 |
| anc_68ab8fb0 | 'Multi-Head Attention' | preserved | relocated | 3 | 2 | 100.0% | 73.3% | page moved from 3 to 2 |
| anc_0fd9bbaa | 'positional encoding' | relocated | relocated | 4 | 5 | 62.5% | 0.0% | page moved from 4 to 5 |
| anc_75144146 | 'BLEU' | preserved | relocated | 7 | 7 | 100.0% | 36.3% | same page but shifted 284.8 pt (> 15.0 pt) |
| anc_fb5847c6 | 'Scaled Dot-Product Attention' | relocated | relocated | 3 | 0 | 79.9% | 57.6% | page moved from 2 to 0 |
| anc_a3d72903 | 'WMT 2014' | relocated | relocated | 7 | 6 | 87.4% | 77.5% | same page but shifted 452.7 pt (> 15.0 pt) |
| anc_076db7ad | 'WMT 2014' | relocated | relocated | 9 | 7 | 93.9% | 83.7% | page moved from 9 to 7 |
| anc_375aab95 | 'Multi-Head Attention' | relocated | relocated | 3 | 2 | 73.4% | 29.0% | page moved from 3 to 2 |
| anc_22128e33 | 'Multi-Head Attention' | relocated | relocated | 1 | 0 | 99.9% | 99.8% | page moved from 1 to 0 |
| anc_26d8bd34 | 'BLEU' | preserved | relocated | 7 | 7 | 100.0% | 99.0% | same page but shifted 351.4 pt (> 15.0 pt) |
| anc_06813260 | 'Scaled Dot-Product Attention' | preserved | relocated | 3 | 3 | 100.0% | 53.7% | same page but shifted 205.3 pt (> 15.0 pt) |
