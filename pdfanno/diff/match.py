"""把旧 Anchor 映射到新 PDF 的位置 —— PRD §8.3。

Week 2 H1 (candidate pool + 1:1 + quad reconstruction):
- 生成**所有候选**，不只取第一个。
- **全局贪心 1:1 分配**：按 `score` 降序配对，每个候选只被认领一次。
- `new_anchor.quads` 用 `search_for(..., quads=True)` 回填。
- `preserved` 要求新 quad 中心距旧 quad 中心 < QUAD_PROXIMITY_THRESHOLD。

Week 2 H2 (context_similarity + changed 状态):
- `context_similarity` 用 SequenceMatcher 比对旧 anchor 的 ±300 字 context 与
  新位置的 ±300 字 context。归一化后参与打分。
- 打分公式对齐 PRD §8.3 的 text(0.40) / context(0.30) / proximity(0.10)。
  layout(0.15) / length(0.05) 尚未实现，由归一化 `/ ACTIVE_SUM` 补偿。
- 新状态 `changed`：fuzzy text (≥0.60 且 <1.0) 但 context 强 (≥0.70) →
  原位置文本被编辑，review_required=True。

Week 2+ 继续补齐：layout_score / ambiguous / unsupported。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pymupdf

from pdfanno.diff.anchors import CONTEXT_CHARS
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
# 同页 quad 中心距离（PDF pt）以内视为"同一位置" —— 约一行行高。
QUAD_PROXIMITY_THRESHOLD = 15.0
# score 下限：低于此值不进分配池，直接 broken。
MIN_CANDIDATE_SCORE = 0.50
# `changed` 状态阈值：text 是 fuzzy 但 context 够强才判 in-place edit。
CHANGED_CONTEXT_THRESHOLD = 0.70
CHANGED_TEXT_MIN = 0.60
# fuzzy 候选下限：低于此值不产生候选。保持在 0.85 避免短 token 跨句误匹配
# （如 "keyword only appears in ..." 这种公用子串）。被 `changed` 认领需要
# fuzzy 通过阈值后 text_sim 仍在 [CHANGED_TEXT_MIN, 1.0) 区间 —— 即 0.85 以上。
FUZZY_THRESHOLD = 0.85

# 打分权重 —— 对齐 PRD §8.3。Week 2 H2 实装 text/context/proximity；
# layout(0.15) / length(0.05) 尚未实现，保持 0 并用 ACTIVE_SUM 归一化。
W_TEXT = 0.40
W_CONTEXT = 0.30
W_PROXIMITY = 0.10
W_LAYOUT = 0.0  # Week 2 H3 / Week 3
W_LENGTH = 0.0  # Week 2 H3 / Week 3
_ACTIVE_SUM = W_TEXT + W_CONTEXT + W_PROXIMITY  # 0.80


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
    context_similarity: float = 0.0
    page_proximity: float = 0.0

    @property
    def score(self) -> float:
        raw = (
            W_TEXT * self.text_similarity
            + W_CONTEXT * self.context_similarity
            + W_PROXIMITY * self.page_proximity
        )
        return raw / _ACTIVE_SUM if _ACTIVE_SUM else 0.0


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
    """每个 page.search_for 返回的 quad 与 normalized text 中 `norm_sel` 的出现按顺序配对，
    从而得到每个候选位置的 new context。单栏论文下顺序一致；多栏可能错位 —— Week 3 再处理。"""

    for pv in pages:
        quads = list(pv.page.search_for(norm_sel, quads=True) or [])
        if not quads:
            continue
        text_positions = _all_find(pv.normalized, norm_sel)
        for q_idx, q in enumerate(quads):
            text_idx = text_positions[q_idx] if q_idx < len(text_positions) else -1
            new_before, new_after = _slice_context(pv.normalized, text_idx, len(norm_sel))
            ctx_sim = _context_similarity(anchor, new_before, new_after)
            yield _Candidate(
                page_index=pv.index,
                quads=[_quad_to_floats(q)],
                matched_text=norm_sel,
                text_similarity=1.0,
                context_similarity=ctx_sim,
                page_proximity=_proximity(pv.index - anchor.page_index, page_window),
            )


def _fuzzy_candidates(
    anchor: Anchor,
    pages: list[_PageView],
    norm_sel: str,
    *,
    page_window: int,
):
    """每页用最长公共子串定位 + 等长窗口对齐，再算 SequenceMatcher.ratio()。

    为什么不直接用 `find_longest_match.size / len(needle)`：单字符中间编辑
    会把 LCS 拦腰斩成两块 —— ratio 会被低估为 ~0.5。对齐窗口后的 full ratio
    能正确给出 ~0.98 这种 "差一个数字" 的真实相似度。
    """

    n = len(norm_sel)
    if n == 0:
        return
    for pv in pages:
        if not pv.normalized:
            continue
        m = SequenceMatcher(None, norm_sel, pv.normalized).find_longest_match(
            0, n, 0, len(pv.normalized)
        )
        if m.size == 0:
            continue
        # 把 needle 的最长匹配块 [m.a..m.a+m.size] 对齐到 page 的 [m.b..m.b+m.size]；
        # 即以此为锚将 needle 整体覆盖到 page 上，截出等长窗口做 full ratio。
        win_start = max(0, m.b - m.a)
        win_end = min(len(pv.normalized), win_start + n)
        window = pv.normalized[win_start:win_end]
        if not window:
            continue
        sim = SequenceMatcher(None, norm_sel, window).ratio()
        if sim < FUZZY_THRESHOLD:
            continue
        new_before, new_after = _slice_context(pv.normalized, win_start, len(window))
        ctx_sim = _context_similarity(anchor, new_before, new_after)
        yield _Candidate(
            page_index=pv.index,
            quads=[],
            matched_text=window,
            text_similarity=sim,
            context_similarity=ctx_sim,
            page_proximity=_proximity(pv.index - anchor.page_index, page_window),
        )


def _all_find(haystack: str, needle: str) -> list[int]:
    """返回 needle 在 haystack 中所有出现位置，按顺序。"""

    positions: list[int] = []
    if not needle:
        return positions
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            return positions
        positions.append(idx)
        start = idx + 1  # 允许重叠以应对极短 token


def _slice_context(text: str, hit_idx: int, hit_len: int) -> tuple[str, str]:
    """从 normalized text 切 hit_idx 前后各 CONTEXT_CHARS 字符。hit_idx<0 退化为空。"""

    if hit_idx < 0:
        return "", ""
    before = text[max(0, hit_idx - CONTEXT_CHARS) : hit_idx]
    end = hit_idx + hit_len
    after = text[end : end + CONTEXT_CHARS]
    return before, after


def _context_similarity(anchor: Anchor, new_before: str, new_after: str) -> float:
    """旧 anchor 的 ±context 与新位置 ±context 的 SequenceMatcher ratio 平均。

    若 anchor 两侧 context 都为空 —— 例如选中文本正好在页首或页尾 —— 返回 0.0
    （context 无信号，不参与加分也不减分，因为 W_CONTEXT 对双方都是零贡献）。
    """

    parts: list[float] = []
    if anchor.context_before:
        parts.append(SequenceMatcher(None, anchor.context_before, new_before).ratio())
    if anchor.context_after:
        parts.append(SequenceMatcher(None, anchor.context_after, new_after).ratio())
    if not parts:
        return 0.0
    return sum(parts) / len(parts)


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
    """按 anchor + 已分配的 candidate 判 preserved / relocated / changed。"""

    page_delta = cand.page_index - anchor.page_index
    reason = MatchReason(
        selected_text_similarity=round(cand.text_similarity, 3),
        context_similarity=round(cand.context_similarity, 3),
        page_delta=page_delta,
        candidate_rank=1,
    )
    new_anchor = NewAnchor(
        page_index=cand.page_index,
        quads=cand.quads,
        matched_text=cand.matched_text,
    )

    # Fuzzy text match —— 区分两种情形：
    #   text 低 + context 强 → `changed`（原位置的文本被编辑）
    #   text 低 + context 弱 → `relocated`（同 token 的不同实例 / 附近移动）
    if cand.text_similarity < 1.0:
        if (
            cand.text_similarity >= CHANGED_TEXT_MIN
            and cand.context_similarity >= CHANGED_CONTEXT_THRESHOLD
        ):
            return DiffResult(
                annotation_id=anchor.annotation_id,
                status="changed",
                confidence=round(cand.score, 3),
                old_anchor=anchor,
                new_anchor=new_anchor,
                match_reason=reason,
                review_required=True,
                message=(
                    f"Text edited in place on page {cand.page_index} "
                    f"(text_sim={cand.text_similarity:.2f}, ctx_sim={cand.context_similarity:.2f})."
                ),
            )
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="relocated",
            confidence=round(cand.score, 3),
            old_anchor=anchor,
            new_anchor=new_anchor,
            match_reason=reason,
            review_required=cand.text_similarity < 0.90,
            message=(
                f"Fuzzy match on page {cand.page_index} (text_sim={cand.text_similarity:.2f})."
            ),
        )

    # 以下都是 exact text 命中（quads 非空）。
    if page_delta != 0:
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="relocated",
            confidence=round(cand.score, 3),
            old_anchor=anchor,
            new_anchor=new_anchor,
            match_reason=reason,
            review_required=False,
            message=f"Exact match on page {cand.page_index} (was {anchor.page_index}).",
        )

    # 同页 exact：quad 近邻 → preserved；偏离 → 同页 relocated。
    if _quads_nearby(anchor.quads, cand.quads, threshold=QUAD_PROXIMITY_THRESHOLD):
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="preserved",
            confidence=round(max(cand.score, 1.0), 3),
            old_anchor=anchor,
            new_anchor=new_anchor,
            match_reason=reason,
            review_required=False,
            message=f"Exact match at same location on page {cand.page_index}.",
        )

    distance = _quad_distance(anchor.quads, cand.quads)
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="relocated",
        confidence=round(cand.score, 3),
        old_anchor=anchor,
        new_anchor=new_anchor,
        match_reason=reason,
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
