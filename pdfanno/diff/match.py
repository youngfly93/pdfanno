"""把旧 Anchor 映射到新 PDF 的位置 —— PRD §8.3。

Week 1 算法（最小闭环）：
  1. 同页 exact normalized substring 命中 -> preserved（page_delta=0, confidence=1.0）
  2. old_page ± PAGE_WINDOW 页内 exact 命中 -> relocated（page_delta!=0）
  3. 全局 exact 命中 -> relocated
  4. 都失败走 difflib.SequenceMatcher 做 fuzzy，阈值 >= FUZZY_THRESHOLD 视为 relocated
  5. 仍未命中 -> broken（confidence=0）

不实现 context_similarity / layout_score 的打分，Week 2 补齐。
"""

from __future__ import annotations

from dataclasses import dataclass
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

PAGE_WINDOW = 3
FUZZY_THRESHOLD = 0.85


@dataclass(frozen=True)
class _PageView:
    """新 PDF 单页的工作视图 —— 原始文本 + 归一化文本。"""

    index: int
    text: str
    normalized: str

    @classmethod
    def from_page(cls, page: pymupdf.Page) -> _PageView:
        raw = page.get_text("text") or ""
        return cls(index=page.number, text=raw, normalized=normalize_text(raw))


def diff_against(
    old_anchors: list[Anchor], new_doc: pymupdf.Document, new_doc_id: str
) -> DiffReport:
    """对一组旧 anchor 在 new_doc 中逐条 diff。"""

    pages = [_PageView.from_page(new_doc[i]) for i in range(new_doc.page_count)]

    results: list[DiffResult] = []
    for anchor in old_anchors:
        results.append(_diff_one(anchor, pages))

    summary = _summarize(results)
    return DiffReport(
        old_doc_id=old_anchors[0].doc_id if old_anchors else "",
        new_doc_id=new_doc_id,
        summary=summary,
        results=results,
    )


def _diff_one(anchor: Anchor, pages: list[_PageView]) -> DiffResult:
    norm_sel = normalize_text(anchor.selected_text)
    if not norm_sel:
        return _broken(
            anchor,
            reason="annotation_text_missing",
            message="No extractable selected_text on old anchor (non-text annotation?).",
        )

    # 1. 同页 exact
    same_page = _find_page(pages, anchor.page_index)
    if same_page is not None and norm_sel in same_page.normalized:
        return _preserved(anchor, same_page, norm_sel)

    # 2. old_page ± WINDOW exact
    for candidate in _pages_within_window(pages, anchor.page_index, PAGE_WINDOW):
        if candidate.index == anchor.page_index:
            continue
        if norm_sel in candidate.normalized:
            return _relocated_exact(anchor, candidate, norm_sel, rank=1)

    # 3. 全局 exact
    for candidate in pages:
        if candidate.index in _window_indices(pages, anchor.page_index, PAGE_WINDOW):
            continue
        if norm_sel in candidate.normalized:
            return _relocated_exact(anchor, candidate, norm_sel, rank=1)

    # 4. fuzzy
    fuzzy_candidate, fuzzy_score = _best_fuzzy(pages, norm_sel)
    if fuzzy_candidate is not None and fuzzy_score >= FUZZY_THRESHOLD:
        return _relocated_fuzzy(anchor, fuzzy_candidate, norm_sel, fuzzy_score)

    # 5. broken
    return _broken(
        anchor,
        reason="no_candidate_over_threshold",
        message=f"No match found; best fuzzy score={fuzzy_score:.2f}.",
    )


# ---------- 状态构造 ----------


def _preserved(anchor: Anchor, page: _PageView, norm_sel: str) -> DiffResult:
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="preserved",
        confidence=1.0,
        old_anchor=anchor,
        new_anchor=NewAnchor(page_index=page.index, matched_text=norm_sel),
        match_reason=MatchReason(
            selected_text_similarity=1.0,
            page_delta=0,
            candidate_rank=1,
        ),
        review_required=False,
        message=f"Exact match on same page {page.index}.",
    )


def _relocated_exact(anchor: Anchor, page: _PageView, norm_sel: str, rank: int) -> DiffResult:
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="relocated",
        confidence=0.95,
        old_anchor=anchor,
        new_anchor=NewAnchor(page_index=page.index, matched_text=norm_sel),
        match_reason=MatchReason(
            selected_text_similarity=1.0,
            page_delta=page.index - anchor.page_index,
            candidate_rank=rank,
        ),
        review_required=False,
        message=f"Exact match on page {page.index} (was {anchor.page_index}).",
    )


def _relocated_fuzzy(anchor: Anchor, page: _PageView, norm_sel: str, score: float) -> DiffResult:
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="relocated",
        confidence=round(score, 3),
        old_anchor=anchor,
        new_anchor=NewAnchor(page_index=page.index, matched_text=norm_sel),
        match_reason=MatchReason(
            selected_text_similarity=round(score, 3),
            page_delta=page.index - anchor.page_index,
            candidate_rank=1,
        ),
        review_required=score < 0.90,
        message=f"Fuzzy match on page {page.index} (score={score:.2f}).",
    )


def _broken(anchor: Anchor, *, reason: str, message: str) -> DiffResult:
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="broken",
        confidence=0.0,
        old_anchor=anchor,
        new_anchor=None,
        match_reason=None,
        review_required=True,
        message=f"{reason}: {message}",
    )


# ---------- 内部辅助 ----------


def _find_page(pages: list[_PageView], idx: int) -> _PageView | None:
    return next((p for p in pages if p.index == idx), None)


def _pages_within_window(pages: list[_PageView], center: int, window: int) -> list[_PageView]:
    lo, hi = center - window, center + window
    return [p for p in pages if lo <= p.index <= hi]


def _window_indices(pages: list[_PageView], center: int, window: int) -> set[int]:
    return {p.index for p in _pages_within_window(pages, center, window)}


def _best_fuzzy(pages: list[_PageView], needle: str) -> tuple[_PageView | None, float]:
    """在所有页面里找最高 SequenceMatcher.ratio 的候选。Week 1 简单实现：整页匹配。"""

    best: _PageView | None = None
    best_score = 0.0
    for page in pages:
        score = SequenceMatcher(None, needle, page.normalized).ratio()
        # 短 needle 在长 page 中用整页比对会偏低；改为 find_longest_match 取片段分数。
        match = SequenceMatcher(None, needle, page.normalized).find_longest_match(
            0, len(needle), 0, len(page.normalized)
        )
        if match.size > 0 and len(needle) > 0:
            segment_score = match.size / len(needle)
            score = max(score, segment_score)
        if score > best_score:
            best = page
            best_score = score
    return best, best_score


def _summarize(results: list[DiffResult]) -> DiffSummary:
    summary = DiffSummary(total_annotations=len(results))
    for r in results:
        setattr(summary, r.status, getattr(summary, r.status, 0) + 1)
    return summary
