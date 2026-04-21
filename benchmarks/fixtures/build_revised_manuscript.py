"""构造一个 "revised manuscript" fixture，在 v1 上 26 条 highlight 覆盖 4 种状态。

不是真实 biorxiv 论文（biorxiv 对 curl 403）；但结构和常见 revision 模式一致：
- preserved (7 条)：v2 未改的句子。
- relocated (6 条)：v2 把整段移到别的位置（跨页或同页偏移）。
- changed (7 条)：v2 原位置句子里改了具体数值 / 个别词 / 小修订。
- broken (6 条)：v2 删掉的句子或段落。

v1 / v2 各 4 页左右，每条 highlight 的 ground truth 已经在注释名里编码（见 NAMING），
这样 evaluate 阶段可以直接对比 oracle 的自动 GT 与硬编码 GT 是否一致（double check）。

用法：
    python -m benchmarks.fixtures.build_revised_manuscript OUT_DIR
生成 v1.pdf, v2.pdf。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

PAGE_W, PAGE_H = 595, 842
LINE_H = 22

# 每句独立编号 + 种子 text，避免短 token 冲突；同时把期望状态编进 key。
SENTENCES: dict[str, str] = {
    # preserved: v1 和 v2 完全相同位置 + 完全相同文本
    "PRE_01": "The alpha subunit of PROT123 regulates downstream signalling cascades during mitosis.",
    "PRE_02": "Samples collected from tissue biopsies were snap-frozen within ninety seconds of excision.",
    "PRE_03": "Statistical significance was assessed using the Wilcoxon rank-sum test with Benjamini-Hochberg correction.",
    "PRE_04": "We thank the Smith laboratory for providing the GENE42 knockout mouse line used in this study.",
    "PRE_05": "All animal experiments were approved by institutional protocols under IACUC number 2024-1138.",
    "PRE_06": "RNA sequencing libraries were prepared using the Illumina TruSeq stranded mRNA protocol.",
    "PRE_07": "Figure 1 shows the overall experimental design including all five independent biological replicates.",
    # changed: v2 原位置句子里改了具体数字或个别词
    "CHG_01_OLD": "The kinase KIN99 activity was measured at 37 degrees Celsius in triplicate experiments.",
    "CHG_02_OLD": "Five hundred twenty-three patients were recruited between January 2022 and June 2023.",
    "CHG_03_OLD": "Protein abundance in the nuclear fraction increased by 2.4 fold compared to controls.",
    "CHG_04_OLD": "The primary antibody was used at a 1:500 dilution overnight at four degrees Celsius.",
    "CHG_05_OLD": "Mean survival in the treatment group was 18.2 months with a p value of 0.003.",
    "CHG_06_OLD": "Cells were seeded at a density of fifty thousand cells per well in twelve-well plates.",
    "CHG_07_OLD": "The library was sequenced on an Illumina NovaSeq generating 50 million paired-end reads.",
    # relocated: v2 整句搬到别的 section / 别的 page
    "REL_01": "We previously reported that FACTOR_X interacts with RECEPTOR_Y in immortalized cell lines.",
    "REL_02": "Long-term follow-up analyses are ongoing and will be reported in a subsequent manuscript.",
    "REL_03": "This observation is consistent with prior work from the Johnson group in 2019.",
    "REL_04": "Raw sequencing data have been deposited in GEO under accession number GSE123456.",
    "REL_05": "Scripts used for differential expression analysis are available at our GitHub repository.",
    "REL_06": "The code for bootstrap confidence intervals follows the standard BCa implementation.",
    # broken: v2 删掉
    "BRK_01": "A minor batch effect between the first and second sequencing run was not corrected.",
    "BRK_02": "Preliminary experiments with COMPOUND_Z failed to reproduce the published phenotype.",
    "BRK_03": "We note that sample SAMPLE_99 was excluded due to low library complexity.",
    "BRK_04": "Previous versions of this manuscript included a supplementary Venn diagram analysis.",
    "BRK_05": "Discussions with the Peterson laboratory shaped the design of this pilot experiment.",
    "BRK_06": "These preliminary findings were shared as a poster at the 2023 ASBMB meeting.",
}

# changed 句子的新版本（和 OLD 保持 SequenceMatcher.ratio >= 0.85 便于被 fuzzy 接住）
CHANGED_NEW: dict[str, str] = {
    "CHG_01_OLD": "The kinase KIN99 activity was measured at 42 degrees Celsius in triplicate experiments.",
    "CHG_02_OLD": "Five hundred twenty-nine patients were recruited between January 2022 and June 2023.",
    "CHG_03_OLD": "Protein abundance in the nuclear fraction increased by 3.1 fold compared to controls.",
    "CHG_04_OLD": "The primary antibody was used at a 1:1000 dilution overnight at four degrees Celsius.",
    "CHG_05_OLD": "Mean survival in the treatment group was 22.5 months with a p value of 0.002.",
    "CHG_06_OLD": "Cells were seeded at a density of seventy thousand cells per well in twelve-well plates.",
    "CHG_07_OLD": "The library was sequenced on an Illumina NovaSeq generating 75 million paired-end reads.",
}


def build_v1(path: Path) -> dict[str, str]:
    """生成 v1.pdf，在关键句子上标 highlight。返回 key -> sentence 映射。"""

    doc = pymupdf.open()
    highlighted_sentences: dict[str, str] = {}

    # 组织 3 页
    pages = [
        ["PRE_01", "CHG_01_OLD", "PRE_02", "BRK_01", "REL_01", "CHG_02_OLD"],
        ["PRE_03", "BRK_02", "CHG_03_OLD", "PRE_04", "REL_02", "CHG_04_OLD", "BRK_03"],
        ["PRE_05", "REL_03", "CHG_05_OLD", "BRK_04", "REL_04", "PRE_06", "CHG_06_OLD"],
        ["PRE_07", "REL_05", "CHG_07_OLD", "REL_06", "BRK_05", "BRK_06"],
    ]

    for page_sentences in pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = 80
        for key in page_sentences:
            sentence = SENTENCES[key]
            page.insert_text((72, y), sentence, fontsize=11)
            y += LINE_H
            highlighted_sentences[key] = sentence

    # 一次性对每个目标句子加 highlight
    for page_idx, page_sentences in enumerate(pages):
        page = doc[page_idx]
        for key in page_sentences:
            text = SENTENCES[key]
            for q in page.search_for(text, quads=True):
                annot = page.add_highlight_annot(q)
                annot.set_info(title=f"revised-fixture/{key}", subject="Highlight")
                annot.update()

    doc.save(str(path))
    doc.close()
    return highlighted_sentences


def build_v2(path: Path) -> None:
    """生成 v2.pdf：preserved 同位置，changed 换新版，relocated 搬页，broken 删除。"""

    doc = pymupdf.open()

    # v2 的页面组织：
    # - preserved 句子保持在 v1 同页同位置（但因删除/编辑导致行号会漂，这也正是 relocated 场景）
    # - changed 句子替换为 CHANGED_NEW[key]
    # - relocated 句子搬到不同页
    # - broken 句子省略
    pages_v2 = [
        # Page 0: 原 PRE_01, CHG_01_NEW, PRE_02, REL_06（新搬来）
        [("PRE_01", SENTENCES["PRE_01"]),
         ("CHG_01", CHANGED_NEW["CHG_01_OLD"]),
         ("PRE_02", SENTENCES["PRE_02"]),
         ("REL_06", SENTENCES["REL_06"])],  # REL_06 从 page 3 搬到 page 0
        # Page 1: 原 CHG_02_NEW, PRE_03, CHG_03_NEW, PRE_04, CHG_04_NEW
        [("CHG_02", CHANGED_NEW["CHG_02_OLD"]),
         ("PRE_03", SENTENCES["PRE_03"]),
         ("CHG_03", CHANGED_NEW["CHG_03_OLD"]),
         ("PRE_04", SENTENCES["PRE_04"]),
         ("CHG_04", CHANGED_NEW["CHG_04_OLD"])],
        # Page 2: PRE_05, CHG_05_NEW, PRE_06, CHG_06_NEW, REL_01（搬来）
        [("PRE_05", SENTENCES["PRE_05"]),
         ("CHG_05", CHANGED_NEW["CHG_05_OLD"]),
         ("PRE_06", SENTENCES["PRE_06"]),
         ("CHG_06", CHANGED_NEW["CHG_06_OLD"]),
         ("REL_01", SENTENCES["REL_01"])],  # REL_01 从 page 0 搬到 page 2
        # Page 3: PRE_07, REL_05, CHG_07_NEW, REL_03 (搬来), REL_04 (保留但换位), REL_02 (搬来)
        [("PRE_07", SENTENCES["PRE_07"]),
         ("REL_05", SENTENCES["REL_05"]),
         ("CHG_07", CHANGED_NEW["CHG_07_OLD"]),
         ("REL_03", SENTENCES["REL_03"]),  # 保留
         ("REL_04", SENTENCES["REL_04"]),  # 保留
         ("REL_02", SENTENCES["REL_02"])],  # REL_02 从 page 1 搬到 page 3
    ]
    # broken 的 BRK_01..BRK_06 完全不出现在 v2 中。

    for page_entries in pages_v2:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = 80
        for _key, sentence in page_entries:
            page.insert_text((72, y), sentence, fontsize=11)
            y += LINE_H

    doc.save(str(path))
    doc.close()


def build_all(out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    v1 = out_dir / "revised_v1.pdf"
    v2 = out_dir / "revised_v2.pdf"
    build_v1(v1)
    build_v2(v2)
    return v1, v2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    v1, v2 = build_all(args.out_dir)
    print(f"built {v1}")
    print(f"built {v2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
