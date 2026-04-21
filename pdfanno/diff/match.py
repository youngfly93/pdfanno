"""把旧 Anchor 映射到新 PDF 的位置 —— PRD §8.3，Week 2 重构版。

相对 Week 1 PoC 的改动（见 benchmarks/reports/week1_spike_arxiv_1706.03762.md）：

- 生成**所有候选**，不只取第一个（candidate pool）。
- **全局贪心 1:1 分配**：按 `score` 降序配对，每个候选只能被认领一次。
  避免 "同页 3 条同文本 anchor 都指向 v5 同一位置" 的 bug。
- `new_anchor.quads` 用 PyMuPDF `search_for(..., quads=True)` 回填，
  为 Week 4-5 migrate 写回 PDF 铺路。
- `preserved` 不再只看 "同页有 substring"：要求新 quad 中心到旧 quad 中心距离
  小于 `QUAD_PROXIMITY_THRESHOLD`（默认 15 PDF pt ≈ 一行）；否则降级 `relocated`。
  解决 spike 发现的 BLEU 短 token 假阳性。

Week 2+ 继续补齐：context_similarity / layout_score / changed / ambiguous。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pymupdf

from pdfanno.diff.types import (
    Anchor,
    DiffReport,
    DiffResult,
    DiffSummary,
    MatchReason,
    NewAnchor,
)
from pdfanno.pdf_core.text import normalize_text

DEFAULT_PAGE_WINDOW = 3
FUZZY_THRESHOLD = 0.85
# 同页 quad 中心距离（PDF pt）以内视为"同一位置" —— 约一行行高。
QUAD_PROXIMITY_THRESHOLD = 15.0
# score 下限：低于此值不进分配池，直接 broken。
MIN_CANDIDATE_SCORE = 0.50

# 打分权重（Week 2 初版；Week 3 会在 dev set 上校准）。
W_TEXT = 0.85
W_PROXIMITY = 0.15


@dataclass(frozen=True)
class _PageView:
    """新 PDF 单页工作视图 —— 保留 pymupdf.Page 引用，供 search_for 回填 quads。"""

    index: int
    page: pymupdf.Page
    normalized: str

    @classmethod
    def from_page(cls, page: pymupdf.Page) -> _PageView:
        raw = page.get_text("text") or ""
        return cls(index=page.number, page=page, normalized=normalize_text(raw))


@dataclass(frozen=True)
class _Candidate:
    """anchor → 新 PDF 上的一个候选位置。"""

    page_index: int
    quads: list[list[float]] = field(default_factory=list)
    matched_text: str = ""
    text_similarity: float = 0.0
    page_proximity: float = 0.0

    @property
    def score(self) -> float:
        return W_TEXT * self.text_similarity + W_PROXIMITY * self.page_proximity


def diff_against(
    old_anchors: list[Anchor],
    new_doc: pymupdf.Document,
    new_doc_id: str,
    *,
    page_window: int = DEFAULT_PAGE_WINDOW,
) -> DiffReport:
    """对一组旧 anchor 在 new_doc 中做全局 1:1 diff。"""

    if page_window < 0:
        raise ValueError(f"page_window must be >= 0, got {page_window}")

    pages = [_PageView.from_page(new_doc[i]) for i in range(new_doc.page_count)]

    # Phase 1: 每条 anchor 生成候选列表。
    anchor_candidates: list[tuple[Anchor, list[_Candidate]]] = [
        (anchor, _candidates_for(anchor, pages, page_window=page_window)) for anchor in old_anchors
    ]

    # Phase 2: 全局贪心 1:1 分配（score desc）。
    assignments = _assign_one_to_one(anchor_candidates)

    # Phase 3: 按分配结果分类并构造 DiffResult。
    results: list[DiffResult] = []
    for anchor, _cands in anchor_candidates:
        cand = assignments.get(anchor.annotation_id)
        if cand is None:
            results.append(_broken(anchor))
        else:
            results.append(_classify(anchor, cand))

    summary = _summarize(results)
    return DiffReport(
        old_doc_id=old_anchors[0].doc_id if old_anchors else "",
        new_doc_id=new_doc_id,
        summary=summary,
        results=results,
    )


# ---------- Phase 1: candidate generation ----------


def _candidates_for(
    anchor: Anchor, pages: list[_PageView], *, page_window: int
) -> list[_Candidate]:
    """枚举 anchor 在新 PDF 上的全部候选。

    策略：优先用 PyMuPDF `search_for` 拿到精确命中 + quads；
    若全文无 exact 命中，退而用 difflib 对每页整串 fuzzy 匹配（无 quads）。
    """

    norm_sel = normalize_text(anchor.selected_text)
    if not norm_sel:
        return []

    candidates = list(_exact_candidates(anchor, pages, norm_sel, page_window=page_window))
    if candidates:
        return candidates
    return list(_fuzzy_candidates(anchor, pages, norm_sel, page_window=page_window))


def _exact_candidates(
    anchor: Anchor,
    pages: list[_PageView],
    norm_sel: str,
    *,
    page_window: int,
):
    for pv in pages:
        for q in pv.page.search_for(norm_sel, quads=True) or []:
            yield _Candidate(
                page_index=pv.index,
                quads=[_quad_to_floats(q)],
                matched_text=norm_sel,
                text_similarity=1.0,
                page_proximity=_proximity(pv.index - anchor.page_index, page_window),
            )


def _fuzzy_candidates(
    anchor: Anchor,
    pages: list[_PageView],
    norm_sel: str,
    *,
    page_window: int,
):
    needle_len = max(len(norm_sel), 1)
    for pv in pages:
        match = SequenceMatcher(None, norm_sel, pv.normalized).find_longest_match(
            0, len(norm_sel), 0, len(pv.normalized)
        )
        if match.size == 0:
            continue
        sim = match.size / needle_len
        if sim < FUZZY_THRESHOLD:
            continue
        yield _Candidate(
            page_index=pv.index,
            quads=[],
            matched_text=norm_sel,
            text_similarity=sim,
            page_proximity=_proximity(pv.index - anchor.page_index, page_window),
        )


def _proximity(page_delta: int, page_window: int) -> float:
    """页距远近归一化到 [0, 1]。page_window=0 的极端情况下只要同页就算满分。"""

    if page_window <= 0:
        return 1.0 if page_delta == 0 else 0.0
    return max(0.0, 1.0 - abs(page_delta) / page_window)


# ---------- Phase 2: global greedy 1:1 assignment ----------


def _assign_one_to_one(
    anchor_candidates: list[tuple[Anchor, list[_Candidate]]],
) -> dict[str, _Candidate]:
    """按 score 降序贪心：每个 anchor 和每个候选都只能被认领一次。

    同分时以 anchor 原始顺序决定优先级 —— 保证确定性（同输入得同结果）。
    """

    indexed: list[tuple[float, int, int, str, _Candidate]] = []
    for a_idx, (anchor, cands) in enumerate(anchor_candidates):
        for c_idx, cand in enumerate(cands):
            if cand.score < MIN_CANDIDATE_SCORE:
                continue
            # 排序 key：(-score, a_idx, c_idx) 保证确定性
            indexed.append((cand.score, a_idx, c_idx, anchor.annotation_id, cand))
    indexed.sort(key=lambda t: (-t[0], t[1], t[2]))

    assignments: dict[str, _Candidate] = {}
    used: set[tuple] = set()
    for _score, _ai, _ci, aid, cand in indexed:
        if aid in assignments:
            continue
        key = _candidate_key(cand)
        if key in used:
            continue
        assignments[aid] = cand
        used.add(key)
    return assignments


def _candidate_key(cand: _Candidate) -> tuple:
    """候选去重 key：exact 有 quads → 按中心点（四舍五入到 0.1）；fuzzy 无 quads → 按页。"""

    if cand.quads:
        cx, cy = _quad_center(cand.quads[0])
        return ("exact", cand.page_index, round(cx, 1), round(cy, 1))
    return ("fuzzy", cand.page_index)


# ---------- Phase 3: status classification ----------


def _classify(anchor: Anchor, cand: _Candidate) -> DiffResult:
    """按 anchor + 已分配的 candidate 判 preserved / relocated / （未来）changed。"""

    page_delta = cand.page_index - anchor.page_index

    # 非完全匹配的 fuzzy 候选 → relocated，置信度取 text_similarity。
    if cand.text_similarity < 1.0:
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="relocated",
            confidence=round(cand.text_similarity, 3),
            old_anchor=anchor,
            new_anchor=NewAnchor(
                page_index=cand.page_index, quads=cand.quads, matched_text=cand.matched_text
            ),
            match_reason=MatchReason(
                selected_text_similarity=round(cand.text_similarity, 3),
                page_delta=page_delta,
                candidate_rank=1,
            ),
            review_required=cand.text_similarity < 0.90,
            message=f"Fuzzy match on page {cand.page_index} (score={cand.text_similarity:.2f}).",
        )

    # 以下都是 exact text 命中（quads 非空）。
    if page_delta != 0:
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="relocated",
            confidence=0.95,
            old_anchor=anchor,
            new_anchor=NewAnchor(
                page_index=cand.page_index, quads=cand.quads, matched_text=cand.matched_text
            ),
            match_reason=MatchReason(
                selected_text_similarity=1.0,
                page_delta=page_delta,
                candidate_rank=1,
            ),
            review_required=False,
            message=f"Exact match on page {cand.page_index} (was {anchor.page_index}).",
        )

    # 同页同文本：quad 近邻检查 —— 解决短 token 假阳性。
    if _quads_nearby(anchor.quads, cand.quads, threshold=QUAD_PROXIMITY_THRESHOLD):
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="preserved",
            confidence=1.0,
            old_anchor=anchor,
            new_anchor=NewAnchor(
                page_index=cand.page_index, quads=cand.quads, matched_text=cand.matched_text
            ),
            match_reason=MatchReason(
                selected_text_similarity=1.0,
                page_delta=0,
                candidate_rank=1,
            ),
            review_required=False,
            message=f"Exact match at same location on page {cand.page_index}.",
        )

    # 同页但 quad 明显偏离 → 同页不同位置（可能是同 token 的另一个实例）。
    distance = _quad_distance(anchor.quads, cand.quads)
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="relocated",
        confidence=0.90,
        old_anchor=anchor,
        new_anchor=NewAnchor(
            page_index=cand.page_index, quads=cand.quads, matched_text=cand.matched_text
        ),
        match_reason=MatchReason(
            selected_text_similarity=1.0,
            page_delta=0,
            candidate_rank=1,
        ),
        review_required=False,
        message=(
            f"Same page, shifted location "
            f"(quad center moved {distance:.1f} pt, threshold {QUAD_PROXIMITY_THRESHOLD:.1f})."
        ),
    )


def _broken(anchor: Anchor) -> DiffResult:
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="broken",
        confidence=0.0,
        old_anchor=anchor,
        new_anchor=None,
        match_reason=None,
        review_required=True,
        message="No candidate above threshold.",
    )


# ---------- quad geometry helpers ----------


def _quad_center(quad: list[float]) -> tuple[float, float]:
    xs = quad[0::2]
    ys = quad[1::2]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _quad_distance(old_quads: list[list[float]], new_quads: list[list[float]]) -> float:
    if not old_quads or not new_quads:
        return float("inf")
    oc = _quad_center(old_quads[0])
    nc = _quad_center(new_quads[0])
    return ((oc[0] - nc[0]) ** 2 + (oc[1] - nc[1]) ** 2) ** 0.5


def _quads_nearby(
    old_quads: list[list[float]], new_quads: list[list[float]], *, threshold: float
) -> bool:
    return _quad_distance(old_quads, new_quads) < threshold


def _quad_to_floats(quad: pymupdf.Quad) -> list[float]:
    return [
        quad.ul.x, quad.ul.y,
        quad.ur.x, quad.ur.y,
        quad.ll.x, quad.ll.y,
        quad.lr.x, quad.lr.y,
    ]  # fmt: skip


# ---------- summary ----------


def _summarize(results: list[DiffResult]) -> DiffSummary:
    summary = DiffSummary(total_annotations=len(results))
    for r in results:
        setattr(summary, r.status, getattr(summary, r.status, 0) + 1)
    return summary
