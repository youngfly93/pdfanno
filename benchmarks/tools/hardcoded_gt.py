"""对合成 fixture 生成"硬编码 ground truth"。

合成 fixture 的注释 title 携带了期望的状态前缀（PRE/REL/CHG/BRK）；这个工具读 title，
结合 PyMuPDF 原生 search_for 回填 gt_page / gt_quad，生成更精细的 ground truth：

- title 以 "revised-fixture/PRE_" 开头  → gt_status=preserved；gt 位置 = v2 上同文本首命中
- title 以 "revised-fixture/REL_" 开头  → gt_status=relocated；gt 位置 = v2 上同文本首命中
- title 以 "revised-fixture/CHG_" 开头  → gt_status=changed；gt 位置 = v2 上"新版本"（人工固定）
- title 以 "revised-fixture/BRK_" 开头  → gt_status=broken；gt 位置 = None

只用于合成 fixture。真实 PDF pair 请用 ground_truth.py 的 oracle。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

from benchmarks.tools.ground_truth import _quad_to_floats
from pdfanno.diff.anchors import extract_anchors
from pdfanno.pdf_core.document import compute_doc_id, open_pdf

# fixture 里 CHG_*_OLD 对应 v2 的新版本文本（从 build_revised_manuscript.CHANGED_NEW 搬过来）。
CHG_OLD_TO_NEW = {
    "The kinase KIN99 activity was measured at 37 degrees Celsius in triplicate experiments.": "The kinase KIN99 activity was measured at 42 degrees Celsius in triplicate experiments.",
    "Five hundred twenty-three patients were recruited between January 2022 and June 2023.": "Five hundred twenty-nine patients were recruited between January 2022 and June 2023.",
    "Protein abundance in the nuclear fraction increased by 2.4 fold compared to controls.": "Protein abundance in the nuclear fraction increased by 3.1 fold compared to controls.",
    "The primary antibody was used at a 1:500 dilution overnight at four degrees Celsius.": "The primary antibody was used at a 1:1000 dilution overnight at four degrees Celsius.",
    "Mean survival in the treatment group was 18.2 months with a p value of 0.003.": "Mean survival in the treatment group was 22.5 months with a p value of 0.002.",
    "Cells were seeded at a density of fifty thousand cells per well in twelve-well plates.": "Cells were seeded at a density of seventy thousand cells per well in twelve-well plates.",
    "The library was sequenced on an Illumina NovaSeq generating 50 million paired-end reads.": "The library was sequenced on an Illumina NovaSeq generating 75 million paired-end reads.",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v1", type=Path)
    parser.add_argument("v2", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    with open_pdf(args.v1) as d1:
        v1_doc_id = compute_doc_id(d1, args.v1)
        anchors = extract_anchors(d1, v1_doc_id)

    v2_doc = pymupdf.open(str(args.v2))
    labels: list[dict] = []
    for anchor in anchors:
        title = anchor.note  # fixture 把 key 放在 title/subject；anchors.py 把 title 存到 note 里其实是 content；title 本身通过 annot.info["title"] 传入 "revised-fixture/{key}"
        # 重新从 v1 读 annot title：extract_anchors 目前不暴露 title；只能通过 selected_text 做反查。
        # 为方便：用 selected_text 直接映射到类别。
        text = anchor.selected_text
        key_prefix = _classify_by_text(text)
        if key_prefix == "PRE" or key_prefix == "REL":
            gt_page, gt_quad = _find_exact(v2_doc, text)
            status = "preserved" if key_prefix == "PRE" else "relocated"
            if gt_page is None:
                status = "needs_review"
            labels.append(_label(anchor, status, gt_page, gt_quad))
        elif key_prefix == "CHG":
            new_text = CHG_OLD_TO_NEW.get(text)
            gt_page, gt_quad = _find_exact(v2_doc, new_text) if new_text else (None, None)
            labels.append(
                _label(
                    anchor,
                    "changed",
                    gt_page,
                    gt_quad,
                    reason=f"in-place edit; v2 text={new_text!r}",
                )
            )
        elif key_prefix == "BRK":
            labels.append(_label(anchor, "broken", None, None, reason="deleted in v2"))
        else:
            labels.append(_label(anchor, "needs_review", None, None, reason="unknown category"))
    v2_doc.close()

    report = {
        "schema_version": 2,
        "source": "hardcoded",
        "old_pdf": str(args.v1),
        "new_pdf": str(args.v2),
        "total_labels": len(labels),
        "summary": _summary(labels),
        "labels": labels,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out} ({len(labels)} labels)")
    print(f"summary: {report['summary']}")
    return 0


def _classify_by_text(text: str) -> str:
    """根据 fixture 里固定的句式前缀分类（不走 title，因为 anchors.py 目前不暴露 title）。"""

    # 直接和 build_revised_manuscript.SENTENCES 里的内容对应
    PRE_MARKERS = (
        "The alpha subunit of PROT123",
        "Samples collected from tissue biopsies",
        "Statistical significance was assessed using the Wilcoxon",
        "We thank the Smith laboratory",
        "All animal experiments were approved",
        "RNA sequencing libraries were prepared using the Illumina",
        "Figure 1 shows the overall experimental design",
    )
    CHG_MARKERS = (
        "The kinase KIN99 activity was measured",
        "Five hundred twenty-three patients",
        "Protein abundance in the nuclear fraction increased",
        "The primary antibody was used at a 1:500",
        "Mean survival in the treatment group was 18.2",
        "Cells were seeded at a density of fifty thousand",
        "The library was sequenced on an Illumina NovaSeq generating 50",
    )
    REL_MARKERS = (
        "We previously reported that FACTOR_X",
        "Long-term follow-up analyses are ongoing",
        "This observation is consistent with prior work from the Johnson",
        "Raw sequencing data have been deposited in GEO",
        "Scripts used for differential expression analysis",
        "The code for bootstrap confidence intervals",
    )
    BRK_MARKERS = (
        "A minor batch effect between the first",
        "Preliminary experiments with COMPOUND_Z",
        "We note that sample SAMPLE_99",
        "Previous versions of this manuscript included",
        "Discussions with the Peterson laboratory",
        "These preliminary findings were shared as a poster",
    )
    for m in PRE_MARKERS:
        if m in text:
            return "PRE"
    for m in CHG_MARKERS:
        if m in text:
            return "CHG"
    for m in REL_MARKERS:
        if m in text:
            return "REL"
    for m in BRK_MARKERS:
        if m in text:
            return "BRK"
    return "UNK"


def _find_exact(doc: pymupdf.Document, text: str | None) -> tuple[int | None, list[float] | None]:
    if not text:
        return None, None
    for p_idx in range(doc.page_count):
        quads = doc[p_idx].search_for(text, quads=True) or []
        if quads:
            return p_idx, _quad_to_floats(quads[0])
    return None, None


def _label(
    anchor,
    status: str,
    gt_page: int | None,
    gt_quad: list[float] | None,
    reason: str | None = None,
) -> dict:
    return {
        "annotation_id": anchor.annotation_id,
        "selected_text": anchor.selected_text,
        "v1_page": anchor.page_index,
        "v1_quad": anchor.quads[0] if anchor.quads else None,
        "gt_status": status,
        "gt_page": gt_page,
        "gt_quad": gt_quad,
        "gt_reason": reason or f"hardcoded fixture label: {status}",
    }


def _summary(labels: list[dict]) -> dict:
    out = {"preserved": 0, "relocated": 0, "changed": 0, "broken": 0, "needs_review": 0}
    for lbl in labels:
        s = lbl["gt_status"]
        out[s] = out.get(s, 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
