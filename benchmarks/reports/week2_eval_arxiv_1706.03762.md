# Evaluation report

- diff: `/tmp/pdfanno_arxiv_spike/diff_h3b.json`
- ground truth: `/tmp/pdfanno_arxiv_spike/gt.json`
- total annotations: gt=39, diff=39, paired=39
- **overall accuracy (status match)**: 92.3%
- **location accuracy** (predicted quad center within 15.0 pt of gt center, same page): 56.4% (22/39)

## Confusion matrix

| pred \ gt | preserved | relocated | changed | ambiguous | broken | unsupported |
|---|---|---|---|---|---|---|
| preserved | 7 | 3 | 0 | 0 | 0 | 0 |
| relocated | 0 | 29 | 0 | 0 | 0 | 0 |
| changed | 0 | 0 | 0 | 0 | 0 | 0 |
| ambiguous | 0 | 0 | 0 | 0 | 0 | 0 |
| broken | 0 | 0 | 0 | 0 | 0 | 0 |
| unsupported | 0 | 0 | 0 | 0 | 0 | 0 |

## Per-status precision / recall / F1

| status | tp | fp | fn | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| preserved | 7 | 3 | 0 | 70.0% | 100.0% | 82.4% |
| relocated | 29 | 0 | 3 | 100.0% | 90.6% | 95.1% |
| changed | 0 | 0 | 0 | — | — | — |
| ambiguous | 0 | 0 | 0 | — | — | — |
| broken | 0 | 0 | 0 | — | — | — |
| unsupported | 0 | 0 | 0 | — | — | — |

## Failure cases (11)

| id | text | pred_status | gt_status | pred_p | gt_p | conf | ctx_sim | gt_reason |
|---|---|---|---|---:|---:|---:|---:|---|
| anc_5bdb9e48 | 'WMT 2014' | relocated | relocated | 7 | 6 | 84.1% | 53.0% | page moved from 7 to 6 |
| anc_22128e33 | 'Multi-Head Attention' | relocated | relocated | 1 | 0 | 97.5% | 99.8% | page moved from 1 to 0 |
| anc_68ab8fb0 | 'Multi-Head Attention' | relocated | relocated | 3 | 2 | 63.7% | 0.8% | page moved from 3 to 2 |
| anc_375aab95 | 'Multi-Head Attention' | relocated | relocated | 3 | 2 | 90.9% | 73.3% | page moved from 3 to 2 |
| anc_ef1075cc | 'WMT 2014' | relocated | relocated | 9 | 7 | 77.2% | 29.3% | page moved from 9 to 7 |
| anc_fb5847c6 | 'Scaled Dot-Product Attention' | relocated | relocated | 3 | 0 | 71.7% | 42.2% | page moved from 2 to 0 |
| anc_75144146 | 'BLEU' | preserved | relocated | 7 | 7 | 100.0% | 36.3% | same page but shifted 284.8 pt (> 15.0 pt) |
| anc_a3d72903 | 'WMT 2014' | relocated | relocated | 7 | 6 | 87.1% | 77.5% | same page but shifted 452.7 pt (> 15.0 pt) |
| anc_26d8bd34 | 'BLEU' | preserved | relocated | 7 | 7 | 100.0% | 99.0% | same page but shifted 351.4 pt (> 15.0 pt) |
| anc_076db7ad | 'WMT 2014' | relocated | relocated | 9 | 7 | 93.3% | 83.7% | page moved from 9 to 7 |
| anc_06813260 | 'Scaled Dot-Product Attention' | preserved | relocated | 3 | 3 | 100.0% | 53.7% | same page but shifted 205.3 pt (> 15.0 pt) |
