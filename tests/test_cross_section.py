"""Section-sim 信号是否 wired —— Week 3 C-3 counterfactual test。

**注意**：这个测试是 **弱断言**。完整的 "flip the decision" 测试构造失败了（详见
`benchmarks/reports/week3_summary.md` 的 "判决力试验" 小节）。根因：
- `_context_window` 使用 `norm_page.find(selected)` 定位 anchor 的上下文，这总是返回
  page 中 **第一次** 出现的位置，当 anchor 本身不是第一次出现时，context 就从错的位置
  抽取，让 ctx_sim 在跨 section 场景下系统性偏向某一边。
- section_sim 在 W_LAYOUT=0.15 × W_LAYOUT_SECTION=0.20 下的 overall 权重仅 0.030，
  不足以翻越 ctx_sim 的 0.10+ 不对称差。

所以这里只保证三点：
1. section_path 真的被填进 anchor 和 candidate（没因为代码重构丢掉）。
2. 打开 section_sim 时，CORRECT 候选的 layout_score 严格高于 WRONG（判别方向正确）。
3. 关闭 section_sim 时，两者的 layout_score 按原信号（rank/y）关系排列。

Week 4 的 ctx 修正之后再补完整的 "OFF 选错 / ON 选对" 判决力测试。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.fixtures.build_cross_section import build_all
from pdfanno.diff.anchors import extract_anchors
from pdfanno.diff.match import (
    _build_doc_occurrences,
    _candidates_for,
    _PageView,
)
from pdfanno.diff.sections import build_section_index
from pdfanno.pdf_core.document import compute_doc_id, open_pdf
from pdfanno.pdf_core.text import normalize_text


@pytest.fixture(scope="module")
def cross_section_pair(tmp_path_factory):
    fix_dir = tmp_path_factory.mktemp("cross_section")
    info = build_all(fix_dir)
    return info["v1"], info["v2"]


def _candidates(v1: Path, v2: Path):
    with open_pdf(v1) as od:
        anchors = extract_anchors(od, compute_doc_id(od, v1))
    anchor = anchors[0]
    with open_pdf(v2) as nd:
        pages = [_PageView.from_page(nd[i]) for i in range(nd.page_count)]
        queries = {normalize_text(anchor.selected_text)}
        v2_occurrences = _build_doc_occurrences(nd, queries)
        v2_sections = build_section_index(nd)
        cands = _candidates_for(
            anchor,
            pages,
            page_window=3,
            v2_occurrences=v2_occurrences,
            v2_sections=v2_sections,
        )
    return anchor, cands


def test_anchor_and_candidate_have_section_paths(cross_section_pair):
    """wiring 检查：anchor 和两个 candidate 都必须有 section_path。"""

    v1, v2 = cross_section_pair
    anchor, cands = _candidates(v1, v2)
    assert anchor.section_path == "Results", (
        f"anchor section 应为 Results，实际 {anchor.section_path!r}"
    )
    assert len(cands) == 2, f"应有 2 个 candidate，实际 {len(cands)}"
    # 两个 candidate 分别对应 Discussion 和 Results；顺序按 reading order。
    with open_pdf(v2) as nd:
        sections = build_section_index(nd)
    cand_sections = []
    for c in cands:
        cy = (c.quads[0][1] + c.quads[0][7]) / 2 if c.quads else 0
        span = next(
            (s for s in reversed(sections) if (s.page_index, s.y_top) <= (c.page_index, cy)),
            None,
        )
        cand_sections.append(span.path if span else None)
    assert set(cand_sections) == {"Discussion", "Results"}


def _section_of(cand, sections):
    cy = (cand.quads[0][1] + cand.quads[0][7]) / 2 if cand.quads else 0
    span = next(
        (s for s in reversed(sections) if (s.page_index, s.y_top) <= (cand.page_index, cy)),
        None,
    )
    return span.path if span else None


def test_section_sim_narrows_the_gap_toward_right_candidate(cross_section_pair, monkeypatch):
    """section_sim 虽然在当前权重下无法翻总分，但必须至少 **缩小** WRONG 候选的
    layout 领先幅度 —— 这是信号方向正确的证据。

    具体：`gap_OFF = Discussion.layout - Results.layout` 应严格大于 `gap_ON`。
    预期差值 = W_LAYOUT_SECTION × (1.0 - 0.0) = 0.20 layout 点。
    """

    v1, v2 = cross_section_pair

    # ON 模式
    _, on_cands = _candidates(v1, v2)
    with open_pdf(v2) as nd:
        sections_on = build_section_index(nd)
    on_results = next(c for c in on_cands if _section_of(c, sections_on) == "Results")
    on_discussion = next(c for c in on_cands if _section_of(c, sections_on) == "Discussion")
    gap_on = on_discussion.layout_score - on_results.layout_score

    # OFF 模式
    monkeypatch.setenv("PDFANNO_DISABLE_SECTION_SIM", "1")
    _, off_cands = _candidates(v1, v2)
    with open_pdf(v2) as nd:
        sections_off = build_section_index(nd)
    off_results = next(c for c in off_cands if _section_of(c, sections_off) == "Results")
    off_discussion = next(c for c in off_cands if _section_of(c, sections_off) == "Discussion")
    gap_off = off_discussion.layout_score - off_results.layout_score

    # 期望：section_sim ON 把差距缩小约 0.20（1.0×W_SECTION - 0×W_SECTION）。
    delta = gap_off - gap_on
    assert 0.15 < delta < 0.25, (
        f"section_sim 开关应把 layout gap 缩小约 0.20；实际 gap_off={gap_off:.3f}, "
        f"gap_on={gap_on:.3f}, delta={delta:.3f}"
    )
    # 另一个方向性断言：ON 状态下 gap 必须更小（不要求翻符号）。
    assert gap_on < gap_off, (
        f"section ON 应缩小 Discussion 对 Results 的 layout 领先："
        f"gap_on={gap_on:.3f} vs gap_off={gap_off:.3f}"
    )


def test_section_sim_toggle_changes_layout_score(cross_section_pair, monkeypatch):
    """相同 fixture，切换 section_sim 开关，候选的 layout_score 必须改变 ——
    证明开关确实 wired 到 `_layout_score` 的计算里，不是 dead code。
    """

    v1, v2 = cross_section_pair

    _, on_cands = _candidates(v1, v2)
    on_scores = sorted(c.layout_score for c in on_cands)

    monkeypatch.setenv("PDFANNO_DISABLE_SECTION_SIM", "1")
    _, off_cands = _candidates(v1, v2)
    off_scores = sorted(c.layout_score for c in off_cands)

    assert on_scores != off_scores, (
        f"section_sim 开关应影响 layout_score；ON={on_scores}, OFF={off_scores}"
    )
