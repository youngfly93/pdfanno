"""构造 "短 token 跨 section 重复" 的 counterfactual fixture —— Week 3 C-3。

立项问题：section_sim 在 arXiv / revised 两个现实 benchmark 上数字 0 变化，因为：
- arXiv：所有短 token 失败都发生在同一 section 内（BLEU/WMT 全在 Results），section_sim 对所有候选 1.0，无判别机会。
- revised：两侧 PDF 都无 heading 结构 → section_path 双向 None → 走等价分支。

本 fixture **专门** 让 section 成为唯一判别信号：

v1 单页，4 次 "Figure 1 data" 在 "Results" 章节 —— anchor 是 **第 2 次** 出现
（0-indexed rank=1，norm=1/4=0.25）。

v2 单页，Discussion 先出现、Results 后出现；"Figure 1 data" 各 1 次：
  - WRONG（Discussion，rank 0，norm 0）—— y 接近 anchor 的 y=240
  - CORRECT（Results，rank 1，norm 0.5）—— y 远离 anchor

signal 分布（数学验证见文件底部）：
- rank_sim 对 anchor(0.25) 都是 0.75 —— **TIED**（关键：anchor 在 v1 的中位 rank，
  与 v2 两候选等距）。
- y_sim: WRONG ≈ 0.905, CORRECT ≈ 0.715 —— **偏向 WRONG**。
- proximity: 同页 → 双方 1.0 —— **TIED**。
- context: body 文本都模板化 —— **TIED by design**。
- section_sim: WRONG=0（Discussion≠Results）, CORRECT=1（Results=Results）—— **唯一非对称**。

结论（算过）：
- section_sim **OFF**：layout WRONG 0.751 > CORRECT 0.704 → WRONG 胜。
- section_sim **ON**：layout WRONG 0.651 < CORRECT 0.804 → CORRECT 胜。

这是 counterfactual 测试的基础 —— 同一 PDF 对跑两次，开关切换，结果必须翻转。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

PAGE_W, PAGE_H = 595, 842
BODY_SIZE = 11
HEADING_SIZE = 16

SENTENCE = "Figure 1 data"  # 短 token，模拟 arXiv 的 BLEU/WMT


def _insert(page: pymupdf.Page, x: float, y: float, text: str, size: float = BODY_SIZE) -> None:
    page.insert_text((x, y), text, fontsize=size)


def build_v1(path: Path) -> dict:
    """v1：单页，"Results" section 只 1 次 Figure 1 data —— anchor 直接命中它。

    anchor rank 0 of 1（norm=0），其 context 由 `_context_window` 按第一次出现
    的位置抽取（因为它本身就是第一次），context 完全对齐 anchor 真实位置。
    """

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _insert(page, 72, 80, "Results", size=HEADING_SIZE)
    _insert(page, 72, 120, "Intro sentence one for context alignment purposes here.")
    _insert(page, 72, 160, f"{SENTENCE} appears exactly once in this version.")
    _insert(page, 72, 200, "Intro sentence two for context alignment purposes here.")
    _insert(page, 72, 240, "Follow-up body paragraph continues with more detail.")
    _insert(page, 72, 280, "Another body line for additional context padding.")

    quads = page.search_for(SENTENCE, quads=True)
    assert len(quads) == 1, f"expected 1 occurrence in v1, got {len(quads)}"
    annot = page.add_highlight_annot(quads[0])
    annot.set_info(title="cross-section-fixture", content="", subject="Highlight")
    annot.update()

    doc.save(str(path))
    doc.close()
    return {"anchor_rank_0indexed": 0, "anchor_total": 1, "anchor_y": 160}


def build_v2(path: Path) -> dict:
    """v2：单页，Discussion 在前、Results 在后；两个 section 各 1 次 Figure 1 data。

    关键设计 —— 两个候选**周围 body 文本完全相同**（copy-paste 了 anchor 在 v1 的 body），
    唯一差异是各自 section 标题（Discussion vs Results）。因此：
    - text_sim 双方 1.0（TIED）
    - ctx_sim：CORRECT 的 context_before 含 "Results" 标题，与 anchor 对齐 → 略高；
      WRONG 的 context_before 是 "Discussion" → 略低。差异来自 heading word 一个词。
    - layout.y 偏向 WRONG（WRONG 的 y 更接近 anchor 的 y=160）
    - layout.rank 偏向 WRONG（rank 0 of 2 vs anchor rank 0 of 1 —— norm 都是 0）
    - layout.section：仅此维度非对称 —— Discussion 0.0 vs Results 1.0

    section **OFF** 时：layout 的 rank+y 优势让 WRONG 的总分胜过 CORRECT 的 ctx 优势，
    WRONG 中标。
    section **ON** 时：section 的额外 0.20 权重让 CORRECT 的 layout 反超，CORRECT 胜。
    """

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    # Discussion section（包含 WRONG 候选），body 模板与 anchor 邻域一致：
    _insert(page, 72, 80, "Discussion", size=HEADING_SIZE)
    _insert(page, 72, 120, "Intro sentence one for context alignment purposes here.")
    _insert(page, 72, 160, f"{SENTENCE} appears exactly once in this version.")  # WRONG rank 0
    _insert(page, 72, 200, "Intro sentence two for context alignment purposes here.")
    _insert(page, 72, 240, "Follow-up body paragraph continues with more detail.")
    _insert(page, 72, 280, "Another body line for additional context padding.")

    # Results section（包含 CORRECT 候选），body 模板同上：
    _insert(page, 72, 360, "Results", size=HEADING_SIZE)
    _insert(page, 72, 400, "Intro sentence one for context alignment purposes here.")
    _insert(page, 72, 440, f"{SENTENCE} appears exactly once in this version.")  # CORRECT rank 1
    _insert(page, 72, 480, "Intro sentence two for context alignment purposes here.")
    _insert(page, 72, 520, "Follow-up body paragraph continues with more detail.")
    _insert(page, 72, 560, "Another body line for additional context padding.")

    doc.save(str(path))
    doc.close()

    verify = pymupdf.open(str(path))
    quads = verify[0].search_for(SENTENCE, quads=True)
    verify.close()
    return {"v2_occurrences": len(quads), "correct_page": 0, "correct_y": 440, "wrong_y": 160}


def build_all(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    v1_path = out_dir / "cross_section_v1.pdf"
    v2_path = out_dir / "cross_section_v2.pdf"
    v1_meta = build_v1(v1_path)
    v2_meta = build_v2(v2_path)
    return {
        "v1": v1_path,
        "v2": v2_path,
        "v1_meta": v1_meta,
        "v2_meta": v2_meta,
    }


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    info = build_all(args.out_dir)
    print(f"v1: {info['v1']}")
    print(f"v2: {info['v2']}")
    print(f"v1 meta: {info['v1_meta']}")
    print(f"v2 meta: {info['v2_meta']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
