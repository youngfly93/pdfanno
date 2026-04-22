"""把旧 Anchor 映射到新 PDF 的位置 —— PRD §8.3。

Week 2 H1 (candidate pool + 1:1 + quad reconstruction):
- 生成**所有候选**，不只取第一个。
- **全局贪心 1:1 分配**：按 `score` 降序配对，每个候选只被认领一次。
- `new_anchor.quads` 用 `search_for(..., quads=True)` 回填。
- `preserved` 要求新 quad 中心距旧 quad 中心 < QUAD_PROXIMITY_THRESHOLD。

Week 2 H2 (context_similarity + changed 状态):
- `context_similarity` 用 SequenceMatcher 比对旧 anchor 的 ±300 字 context 与
  新位置的 ±300 字 context。
- 新状态 `changed`：高 text (≥0.90) 或 fuzzy text + 强 context (≥0.70)。

Week 2 H3 (layout_score + length_similarity):
- PRD §8.3 完整五项打分：text(0.40) / context(0.30) / layout(0.15) /
  proximity(0.10) / length(0.05)。`_ACTIVE_SUM` 现在是 1.0。
- `layout_score` 为 exact 候选计算：y_ratio 相似度（占 0.70）+ x_ratio 相似度
  （占 0.30）。同页多实例下，与旧 anchor y-位置最接近的候选胜出 —— 这是解决
  arXiv spike 里 "第 k 次 BLEU 应对应第 k 次 BLEU" 的关键信号。
- 若 anchor 或页面缺少尺寸信息（老 sidecar），layout 退化到 0.5 neutral，不偏不倚。
- fuzzy 候选无 quad，同样取 0.5 neutral。
- `length_similarity = 1 - abs(len_old - len_new) / max(len_old, len_new)`。
  exact 下恒为 1.0；fuzzy 下给弱信号加成。

Week 3+ 继续补齐：ambiguous / unsupported / reading-order 精修。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import pymupdf

from pdfanno.diff import context as ctx
from pdfanno.diff.anchors import CONTEXT_CHARS, TEXT_COVERAGE_KINDS
from pdfanno.diff.sections import SectionSpan, build_section_index, section_for
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
# `changed` 状态阈值，两档：
#   A) text 非常高 (≥ HIGH_TEXT_CHANGED_MIN) → 认为是 in-place edit，即使 context 一般
#   B) text 中等 (≥ CHANGED_TEXT_MIN) 且 context 强 (≥ CHANGED_CONTEXT_THRESHOLD) → 同上
# B 档覆盖 "句子改动较大但位置没变"；A 档覆盖 "仅改个别数字/单词" 这种典型论文修订。
CHANGED_CONTEXT_THRESHOLD = 0.70
CHANGED_TEXT_MIN = 0.60
HIGH_TEXT_CHANGED_MIN = 0.90
# fuzzy 候选下限：低于此值不产生候选。保持在 0.85 避免短 token 跨句误匹配
# （如 "keyword only appears in ..." 这种公用子串）。被 `changed` 认领需要
# fuzzy 通过阈值后 text_sim 仍在 [CHANGED_TEXT_MIN, 1.0) 区间 —— 即 0.85 以上。
FUZZY_THRESHOLD = 0.85

# 打分权重 —— 对齐 PRD §8.3。Week 2 H3 起五项齐全，_ACTIVE_SUM = 1.0。
W_TEXT = 0.40
W_CONTEXT = 0.30
W_LAYOUT = 0.15
W_PROXIMITY = 0.10
W_LENGTH = 0.05
_ACTIVE_SUM = W_TEXT + W_CONTEXT + W_LAYOUT + W_PROXIMITY + W_LENGTH  # 1.00

# layout_score 内部权重（Week 3 C 起四子分数）：
# - section_sim: 同 section 才视作候选，是最强判别信号。
# - rank_sim: 文档级 reading-order k-th 映射，弥补短 token 跨页歧义。
# - y_sim: 同页多实例 y-位置定位。
# - x_sim: 双栏版式的 column 提示；单栏几乎没信号。
W_LAYOUT_SECTION = 0.20
W_LAYOUT_RANK = 0.50
W_LAYOUT_Y = 0.25
W_LAYOUT_X = 0.05
# 缺尺寸 / rank / section 信息时子分数的 neutral 值。
NEUTRAL_LAYOUT = 0.5

# 环境变量开关：`PDFANNO_DISABLE_SECTION_SIM=1` 把 section 子分数强制退成 NEUTRAL，
# 用于 counterfactual 测试 —— 证明 section_sim **确实在翻案**，而不是只被代码路径覆盖。
_SECTION_SIM_ENV_OFF = "PDFANNO_DISABLE_SECTION_SIM"

# Week 9 "broken floor"：当 assigned candidate 的 context_similarity 小于 floor 时，
# 若该候选不是 "same-location preserved"，判为 broken 而非 relocated。动机见
# week8_semantic_oracle.md —— 1-to-1 semantic oracle 下 8/9 真失败都是 "pred=relocated,
# gt=broken"，算法对 ctx 很弱的位置过度乐观。
#
# **默认 0.0（关闭）** —— 保证 v0.2.1 tag 以来的所有 baseline / test 行为不变。
# 运行时通过 `PDFANNO_BROKEN_CTX_FLOOR=0.20` 启用，用于 benchmark sweep 和 A/B。
# `PDFANNO_DISABLE_BROKEN_FLOOR=1` 显式强制关闭（优先级高于数值）。
# Same-location preserved 的 case 永不被惩罚 —— 位置对但 ctx 抽取弱是常见情形。
BROKEN_CTX_FLOOR = 0.0
_BROKEN_FLOOR_ENV_OFF = "PDFANNO_DISABLE_BROKEN_FLOOR"
_BROKEN_FLOOR_ENV_VAL = "PDFANNO_BROKEN_CTX_FLOOR"

# Week 11 "ctx-aware assignment preemption"：在 1:1 greedy 分配时，若候选 slot 已被
# 同 normalized selected_text 的另一 anchor 占用，score 差在 epsilon 内，且当前
# anchor 的 context_similarity 显著更高（≥ MIN_CTX_ADVANTAGE），允许抢占；被抢占
# 的 anchor 退到自己的下一个未占用候选。
#
# 动机（week10_ctx_mode.md）：arXiv 失败里 `anc_bc48e155 Scaled-Dot-Product` own_ctx
# = 0.576 本身不差，但 greedy 阶段被另一同 token anchor 抢走 slot，只能退到 ctx=
# 0.07 次优。broken floor 单靠度量解不了这个"被抢"问题，必须进到分配层。
#
# **默认关闭** —— `PDFANNO_CTX_AWARE_ASSIGN=1` 显式启用。`PDFANNO_CTX_ASSIGN_EPSILON`
# / `PDFANNO_CTX_ASSIGN_MIN_ADVANTAGE` 运行时覆盖阈值。只在 same-token 组内生效，
# 不跨 token、不做 Hungarian、不比较不同 text 的 score 总和 —— 那条路 Week 2 B
# 和 Week 7 已证伪两次。
_CTX_ASSIGN_ENV_ON = "PDFANNO_CTX_AWARE_ASSIGN"
_CTX_ASSIGN_EPSILON_ENV = "PDFANNO_CTX_ASSIGN_EPSILON"
_CTX_ASSIGN_MIN_ADVANTAGE_ENV = "PDFANNO_CTX_ASSIGN_MIN_ADVANTAGE"
CTX_ASSIGN_EPSILON = 0.05
CTX_ASSIGN_MIN_ADVANTAGE = 0.10


def _ctx_aware_assign_params() -> tuple[float, float] | None:
    """返回 (epsilon, min_advantage)；None 表示关闭（默认）。"""

    if os.environ.get(_CTX_ASSIGN_ENV_ON) != "1":
        return None
    try:
        eps = float(os.environ.get(_CTX_ASSIGN_EPSILON_ENV, CTX_ASSIGN_EPSILON))
    except ValueError:
        eps = CTX_ASSIGN_EPSILON
    try:
        adv = float(os.environ.get(_CTX_ASSIGN_MIN_ADVANTAGE_ENV, CTX_ASSIGN_MIN_ADVANTAGE))
    except ValueError:
        adv = CTX_ASSIGN_MIN_ADVANTAGE
    return eps, adv


def _active_broken_floor() -> float | None:
    """返回当前生效的 ctx floor；None 表示关闭。"""

    if os.environ.get(_BROKEN_FLOOR_ENV_OFF) == "1":
        return None
    try:
        return float(os.environ.get(_BROKEN_FLOOR_ENV_VAL, BROKEN_CTX_FLOOR))
    except ValueError:
        return BROKEN_CTX_FLOOR


def _section_sim_disabled() -> bool:
    """每次调用检查 —— 支持测试中 monkeypatch.setenv。"""

    return os.environ.get(_SECTION_SIM_ENV_OFF) == "1"


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
    """anchor → 新 PDF 上的一个候选位置。

    `window_start` 是候选在 normalized page text 中的起始字符位置，用于跨 exact /
    fuzzy 共享 text slot —— 否则 fuzzy 候选可以复用已经被 exact anchor 认领的新
    PDF 文本位置，把删除的近重复句子误判为 changed。
    exact 候选仍保留 quad 中心作为几何 slot；window_start 缺失时默认 -1。
    """

    page_index: int
    quads: list[list[float]] = field(default_factory=list)
    matched_text: str = ""
    text_similarity: float = 0.0
    context_similarity: float = 0.0
    layout_score: float = NEUTRAL_LAYOUT
    page_proximity: float = 0.0
    length_similarity: float = 1.0
    window_start: int = -1

    @property
    def score(self) -> float:
        raw = (
            W_TEXT * self.text_similarity
            + W_CONTEXT * self.context_similarity
            + W_LAYOUT * self.layout_score
            + W_PROXIMITY * self.page_proximity
            + W_LENGTH * self.length_similarity
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

    # 预计算 v2 文档级 reading-order occurrence 列表，
    # 为 candidate 的 rank 子分数（跨页 k-th 映射）提供全局索引。
    queries = {normalize_text(a.selected_text) for a in old_anchors if a.selected_text}
    v2_occurrences = _build_doc_occurrences(new_doc, queries)
    # v2 section 索引，供 candidate 计算 section_sim。
    v2_sections = build_section_index(new_doc)

    # Phase 1: 每条 anchor 生成候选列表。
    anchor_candidates: list[tuple[Anchor, list[_Candidate]]] = [
        (
            anchor,
            _candidates_for(
                anchor,
                pages,
                page_window=page_window,
                v2_occurrences=v2_occurrences,
                v2_sections=v2_sections,
            ),
        )
        for anchor in old_anchors
    ]

    # Phase 2: 全局贪心 1:1 分配（score desc）。
    assignments = _assign_one_to_one(anchor_candidates)

    # Phase 3: 按分配结果分类并构造 DiffResult。
    results: list[DiffResult] = []
    for anchor, _cands in anchor_candidates:
        if _is_unsupported(anchor):
            results.append(_unsupported(anchor))
            continue
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
    anchor: Anchor,
    pages: list[_PageView],
    *,
    page_window: int,
    v2_occurrences: dict[str, list[tuple[int, float, float]]] | None = None,
    v2_sections: list[SectionSpan] | None = None,
) -> list[_Candidate]:
    """枚举 anchor 在新 PDF 上的全部候选。

    策略：优先用 PyMuPDF `search_for` 拿到精确命中 + quads；
    若全文无 exact 命中，退而用 difflib 对每页整串 fuzzy 匹配（无 quads）。
    `v2_occurrences` 提供 doc-level reading-order rank，`v2_sections` 提供
    section 标签；两者都参与 layout 子分数。
    """

    norm_sel = normalize_text(anchor.selected_text)
    if not norm_sel:
        return []

    v2_occs_for_query = (v2_occurrences or {}).get(norm_sel, [])
    candidates = list(
        _exact_candidates(
            anchor,
            pages,
            norm_sel,
            page_window=page_window,
            v2_occurrences=v2_occs_for_query,
            v2_sections=v2_sections or [],
        )
    )
    if candidates:
        return candidates
    return list(_fuzzy_candidates(anchor, pages, norm_sel, page_window=page_window))


def _exact_candidates(
    anchor: Anchor,
    pages: list[_PageView],
    norm_sel: str,
    *,
    page_window: int,
    v2_occurrences: list[tuple[int, float, float]] | None = None,
    v2_sections: list[SectionSpan] | None = None,
):
    """每个 page.search_for 返回的 quad 与 normalized text 中 `norm_sel` 的出现按顺序配对，
    从而得到每个候选位置的 new context。单栏论文下顺序一致；多栏可能错位 —— Week 3 再处理。

    `v2_occurrences` 是 normalized query 在 v2 整个文档里按 reading-order 排好的
    (page, cy, cx) 列表，供 layout rank 子分数使用。
    `v2_sections` 为 v2 的 section 索引，供 section_sim 子分数使用。
    """

    v2_occs = v2_occurrences or []
    v2_total = len(v2_occs)
    v2_sec_list = v2_sections or []
    for pv in pages:
        quads = list(pv.page.search_for(norm_sel, quads=True) or [])
        if not quads:
            continue
        text_positions = _all_find(pv.normalized, norm_sel)
        casefold_positions = _all_find(pv.normalized.casefold(), norm_sel.casefold())
        for q_idx, q in enumerate(quads):
            text_idx = text_positions[q_idx] if q_idx < len(text_positions) else -1
            if text_idx < 0 and q_idx < len(casefold_positions):
                text_idx = casefold_positions[q_idx]
            quad_floats = _quad_to_floats(q)
            matched_text, text_sim = _exact_candidate_text(pv.page, q, norm_sel)
            if matched_text is None:
                # PyMuPDF search_for is ASCII case-insensitive. If the page text
                # does not contain a case-sensitive occurrence and the quad text
                # is not a case-only variant, do not treat it as an exact hit.
                continue
            new_before, new_after = _slice_context(pv.normalized, text_idx, len(norm_sel))
            ctx_sim = _context_similarity(anchor, new_before, new_after)
            v2_rank = _match_v2_rank(v2_occs, pv.index, quad_floats)
            # 候选的 section —— 用 quad 中心 y 定位到 v2 的 section。
            cy = (quad_floats[1] + quad_floats[7]) / 2
            cand_section = section_for(v2_sec_list, pv.index, cy)
            layout = _layout_score(
                anchor,
                quad_floats,
                pv.page.rect,
                v2_rank=v2_rank,
                v2_total=v2_total,
                candidate_section_path=cand_section.path if cand_section else None,
            )
            yield _Candidate(
                page_index=pv.index,
                quads=[quad_floats],
                matched_text=matched_text,
                text_similarity=text_sim,
                context_similarity=ctx_sim,
                layout_score=layout,
                page_proximity=_proximity(pv.index - anchor.page_index, page_window),
                length_similarity=_length_similarity(len(norm_sel), len(matched_text)),
                window_start=text_idx,
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
        # autojunk=False：对 >200 字符的 page text，SequenceMatcher 默认把高频字符标为
        # "junk" 导致 find_longest_match 只返回 size=1。论文正文恰好触发这个陷阱，
        # 会让一半以上的 fuzzy 候选丢失。关掉 autojunk 恢复正常行为。
        m = SequenceMatcher(None, norm_sel, pv.normalized, autojunk=False).find_longest_match(
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
        sim = SequenceMatcher(None, norm_sel, window, autojunk=False).ratio()
        if sim < FUZZY_THRESHOLD:
            continue
        new_before, new_after = _slice_context(pv.normalized, win_start, len(window))
        ctx_sim = _context_similarity(anchor, new_before, new_after)
        # fuzzy 没 quad，layout 退化到 neutral；长度相似度拿 needle vs window 的长度比。
        length_sim = _length_similarity(len(norm_sel), len(window))
        yield _Candidate(
            page_index=pv.index,
            quads=[],
            matched_text=window,
            text_similarity=sim,
            context_similarity=ctx_sim,
            layout_score=NEUTRAL_LAYOUT,
            page_proximity=_proximity(pv.index - anchor.page_index, page_window),
            length_similarity=length_sim,
            window_start=win_start,
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


def _exact_candidate_text(
    page: pymupdf.Page,
    quad: pymupdf.Quad,
    norm_sel: str,
) -> tuple[str | None, float]:
    """Return case-sensitive candidate text and similarity for a MuPDF hit.

    PyMuPDF `search_for` is case-insensitive for ASCII. For diff this matters:
    a case-only edit at the same location should be `changed`, not `preserved`.
    We therefore inspect the hit rectangle. True same-case hits get text_sim=1;
    case-only variants keep their quad but receive a SequenceMatcher score.
    """

    actual = normalize_text(page.get_textbox(quad.rect) or "")
    if not actual:
        return norm_sel, 1.0
    if actual == norm_sel or norm_sel in actual or actual in norm_sel:
        return norm_sel, 1.0
    if actual.casefold() == norm_sel.casefold():
        return actual, SequenceMatcher(None, norm_sel, actual, autojunk=False).ratio()
    return None, 0.0


def _slice_context(text: str, hit_idx: int, hit_len: int) -> tuple[str, str]:
    """从 normalized text 切 hit_idx 前后各 CONTEXT_CHARS 字符。hit_idx<0 退化为空。"""

    if hit_idx < 0:
        return "", ""
    before = text[max(0, hit_idx - CONTEXT_CHARS) : hit_idx]
    end = hit_idx + hit_len
    after = text[end : end + CONTEXT_CHARS]
    return before, after


def _layout_score(
    anchor: Anchor,
    new_quad: list[float],
    new_rect,
    *,
    v2_rank: int | None = None,
    v2_total: int = 0,
    candidate_section_path: str | None = None,
) -> float:
    """四个子分数加权和：section + reading-order rank + y-ratio + x-ratio。

    section: 最强判别 —— 同 section 1.0，不同 0.0，两侧都未知 1.0（无信号，不扣分），
             只有一侧已知 neutral。
    rank: 跨页 `k-th` 映射，对短 token 重复命中特别关键。
    y: 同页多实例按 y 锁定。
    x: 双栏版式 column 提示；单栏几乎没信号。

    任一子分数缺信息 → 退化到 NEUTRAL_LAYOUT，不扰动其他子分数。
    """

    # section 子分数：两侧都未知视为等价 —— "无信号" 是对称噪声，均匀作用于所有候选；
    # 退 0.5 会静默惩罚每个无 TOC / 无 heading 结构的 PDF。
    # `PDFANNO_DISABLE_SECTION_SIM=1` 时强制 NEUTRAL —— 用于 counterfactual 测试。
    if _section_sim_disabled():
        section_sim = NEUTRAL_LAYOUT
    elif anchor.section_path and candidate_section_path:
        section_sim = 1.0 if anchor.section_path == candidate_section_path else 0.0
    elif anchor.section_path is None and candidate_section_path is None:
        section_sim = 1.0
    else:
        section_sim = NEUTRAL_LAYOUT

    # rank 子分数
    if (
        v2_rank is not None
        and v2_total > 0
        and anchor.occurrence_rank is not None
        and anchor.total_occurrences is not None
        and anchor.total_occurrences > 0
    ):
        v1_norm = anchor.occurrence_rank / max(anchor.total_occurrences, 1)
        v2_norm = v2_rank / max(v2_total, 1)
        rank_sim = max(0.0, 1.0 - abs(v1_norm - v2_norm))
    else:
        rank_sim = NEUTRAL_LAYOUT

    # y / x 子分数
    if (
        not anchor.quads
        or anchor.page_height is None
        or anchor.page_height <= 0
        or anchor.page_width is None
        or anchor.page_width <= 0
        or not new_quad
        or not new_rect
        or new_rect.height <= 0
        or new_rect.width <= 0
    ):
        y_sim = NEUTRAL_LAYOUT
        x_sim = NEUTRAL_LAYOUT
    else:
        old_cx, old_cy = _quad_center(anchor.quads[0])
        new_cx, new_cy = _quad_center(new_quad)
        y_sim = max(0.0, 1.0 - abs(old_cy / anchor.page_height - new_cy / new_rect.height))
        x_sim = max(0.0, 1.0 - abs(old_cx / anchor.page_width - new_cx / new_rect.width))

    return (
        W_LAYOUT_SECTION * section_sim
        + W_LAYOUT_RANK * rank_sim
        + W_LAYOUT_Y * y_sim
        + W_LAYOUT_X * x_sim
    )


def _build_doc_occurrences(
    doc: pymupdf.Document, queries: set[str]
) -> dict[str, list[tuple[int, float, float]]]:
    """对每个 query 返回 (page, cy, cx) 列表，按 reading order (page, cy, cx) 排好。"""

    out: dict[str, list[tuple[int, float, float]]] = {q: [] for q in queries}
    for p_idx in range(doc.page_count):
        page = doc[p_idx]
        for q in queries:
            for quad in page.search_for(q, quads=True) or []:
                cx = (quad.ul.x + quad.lr.x) / 2
                cy = (quad.ul.y + quad.lr.y) / 2
                out[q].append((p_idx, cy, cx))
    for q in out:
        out[q].sort(key=lambda t: (t[0], t[1], t[2]))
    return out


def _match_v2_rank(
    occs: list[tuple[int, float, float]], page_index: int, quad_floats: list[float]
) -> int | None:
    """给定 v2 某 page 某 quad，找它在 doc-level occurrence 列表里的 rank。"""

    if not occs or not quad_floats:
        return None
    cx, cy = _quad_center(quad_floats)
    best_idx, best_d = None, float("inf")
    for idx, (p, ocy, ocx) in enumerate(occs):
        if p != page_index:
            continue
        d = (ocx - cx) ** 2 + (ocy - cy) ** 2
        if d < best_d:
            best_d = d
            best_idx = idx
    return best_idx


def _length_similarity(len_a: int, len_b: int) -> float:
    if len_a <= 0 and len_b <= 0:
        return 1.0
    denom = max(len_a, len_b)
    if denom == 0:
        return 1.0
    return max(0.0, 1.0 - abs(len_a - len_b) / denom)


def _context_similarity(anchor: Anchor, new_before: str, new_after: str) -> float:
    """旧 anchor 的 ±context 与新位置 ±context 的相似度。

    实现委托给 `pdfanno.diff.context.context_similarity`，默认 `mean` 模式 ——
    与 v0.2.1 行为一致。`PDFANNO_CTX_SIM_MODE=concat` 可切到与 semantic oracle
    对齐的 concat 算法（Week 10 实验；见 `week10_ctx_mode.md`）。
    """

    mode = os.environ.get("PDFANNO_CTX_SIM_MODE", ctx.DEFAULT_MODE)
    if mode not in ctx.ALL_MODES:
        mode = ctx.DEFAULT_MODE
    return ctx.context_similarity(
        anchor.context_before, anchor.context_after, new_before, new_after, mode=mode
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

    历史上尝试过换非 greedy（全部 revert 回 greedy）：
    - Week 2 B：**全局** Kuhn-Munkres → arXiv 92.3→89.7 / 56.4→53.8。
    - Week 7：**同 token 组内** Hungarian → arXiv 同样 92.3→89.7 / 56.4→53.8，
      Word2Vec 压力集数字没动。`anc_68ab8fb0 Multi-Head Attention` 稳定证伪任何
      "加强 same-token 组内 sum-max" 思路。详见 `week7_group_assign.md`。

    **结论**：greedy 的 "先选最高分对" 先验比 Hungarian sum-max 健壮。

    Week 11 加了一个 **可选的 same-token ctx-aware preemption 层**（默认关闭）：
    candidate slot 已被同 token 另一 anchor 占用，且 score 差 ≤ epsilon，
    且 contender 的 ctx 比 holder 高 ≥ MIN_CTX_ADVANTAGE 时，允许抢占。
    被抢的 anchor 退到自己的下一个未占用候选。解决 arXiv `anc_bc48e155` 这类
    "own ctx 不差但被 greedy 早期抢走 slot，只能退到 ctx 近 0 的次优" 失败。

    启用：`PDFANNO_CTX_AWARE_ASSIGN=1`（与 `PDFANNO_CTX_SIM_MODE=concat` 配合
    使用效果最好，因为 concat mode 下 ctx 数值和 oracle 对齐）。

    同分时以 (a_idx, c_idx) 破平 —— 保证确定性。
    """

    indexed: list[tuple[float, int, int, str, _Candidate]] = []
    for a_idx, (anchor, cands) in enumerate(anchor_candidates):
        for c_idx, cand in enumerate(cands):
            if cand.score < MIN_CANDIDATE_SCORE:
                continue
            indexed.append((cand.score, a_idx, c_idx, anchor.annotation_id, cand))
    indexed.sort(key=lambda t: (-t[0], t[1], t[2]))

    ctx_params = _ctx_aware_assign_params()  # None = disabled

    assignments: dict[str, _Candidate] = {}
    used: dict[tuple, tuple[str, float, _Candidate]] = {}
    displaced: list[str] = []  # anchors whose slot got preempted; retry at end

    # 预计算 aid → anchor 以便查 normalize_text；另外 aid → sorted cands 以便退位。
    aid_to_anchor: dict[str, Anchor] = {a.annotation_id: a for a, _ in anchor_candidates}
    aid_to_cands: dict[str, list[_Candidate]] = {
        a.annotation_id: cands for a, cands in anchor_candidates
    }

    for score, _ai, _ci, aid, cand in indexed:
        if aid in assignments:
            continue
        conflict = _first_used_slot(used, cand)
        if conflict is None:
            assignments[aid] = cand
            _claim_candidate(used, aid, score, cand)
            continue

        # slot 已占。默认 greedy：skip。Week 11：仅在开关打开 + 同 token + score 近
        # + ctx 显著更高时抢占。
        _slot, holder = conflict
        holder_aid, holder_score, holder_cand = holder

        # Fuzzy 候选不能复用已被 exact 命中的新文本位置；反过来，如果 exact 候选
        # 后到，也应该抢回这个 slot。否则 "anchor 44" 这种已删除近重复句会把
        # "anchor 34" 的真实位置当成高 text_sim 的 changed。
        if _is_exact_text_candidate(cand) and not _is_exact_text_candidate(holder_cand):
            assignments.pop(holder_aid, None)
            _release_candidate(used, holder_aid, holder_cand)
            displaced.append(holder_aid)
            assignments[aid] = cand
            _claim_candidate(used, aid, score, cand)
            continue

        if ctx_params is None:
            continue
        epsilon, min_advantage = ctx_params
        if holder_aid == aid:
            continue  # 防御性 —— 不应发生
        holder_anchor = aid_to_anchor.get(holder_aid)
        contender_anchor = aid_to_anchor.get(aid)
        if holder_anchor is None or contender_anchor is None:
            continue
        if normalize_text(holder_anchor.selected_text) != normalize_text(
            contender_anchor.selected_text
        ):
            continue  # 跨 token 禁止抢占
        if holder_score - score > epsilon:
            continue  # holder 的 score 明显更高，不翻案
        if cand.context_similarity - holder_cand.context_similarity < min_advantage:
            continue  # contender 的 ctx 优势不够
        # 抢占！
        assignments.pop(holder_aid, None)
        _release_candidate(used, holder_aid, holder_cand)
        displaced.append(holder_aid)
        assignments[aid] = cand
        _claim_candidate(used, aid, score, cand)

    # 为被抢占的 anchor 找次优未占用候选。按本 anchor 的 score desc 扫一遍。
    for aid in displaced:
        if aid in assignments:
            continue  # 被后续抢占链条重新赋值，不用再找
        for c in sorted(aid_to_cands.get(aid, []), key=lambda x: -x.score):
            if c.score < MIN_CANDIDATE_SCORE:
                break
            if _first_used_slot(used, c) is not None:
                continue
            assignments[aid] = c
            _claim_candidate(used, aid, c.score, c)
            break

    return assignments


def _candidate_keys(cand: _Candidate) -> tuple[tuple, ...]:
    """候选去重 key：
    - text slot：exact / fuzzy 共享 `(page, window_start)`，禁止重复认领同一文本位置。
    - geometry slot：exact 额外按 quad 中心去重（四舍五入到 0.1）。

    fuzzy 的 window_start 是对齐后的字符偏移；exact 的 window_start 来自 normalized
    text 中的命中位置。缺失 window_start 时，fuzzy 回退到 legacy fuzzy key。
    """

    keys: list[tuple] = []
    if cand.window_start >= 0:
        keys.append(("text", cand.page_index, cand.window_start))
    if cand.quads:
        cx, cy = _quad_center(cand.quads[0])
        keys.append(("exact", cand.page_index, round(cx, 1), round(cy, 1)))
    if not keys:
        keys.append(("fuzzy", cand.page_index, cand.window_start))
    return tuple(keys)


def _first_used_slot(
    used: dict[tuple, tuple[str, float, _Candidate]],
    cand: _Candidate,
) -> tuple[tuple, tuple[str, float, _Candidate]] | None:
    for key in _candidate_keys(cand):
        holder = used.get(key)
        if holder is not None:
            if (
                key[0] == "text"
                and _is_exact_text_candidate(cand)
                and _is_exact_text_candidate(holder[2])
            ):
                continue
            return key, holder
    return None


def _claim_candidate(
    used: dict[tuple, tuple[str, float, _Candidate]],
    aid: str,
    score: float,
    cand: _Candidate,
) -> None:
    for key in _candidate_keys(cand):
        used[key] = (aid, score, cand)


def _release_candidate(
    used: dict[tuple, tuple[str, float, _Candidate]],
    aid: str,
    cand: _Candidate,
) -> None:
    for key in _candidate_keys(cand):
        holder = used.get(key)
        if holder is not None and holder[0] == aid:
            used.pop(key, None)


def _is_exact_text_candidate(cand: _Candidate) -> bool:
    return bool(cand.quads) and cand.text_similarity == 1.0


# ---------- Phase 3: status classification ----------


def _classify(anchor: Anchor, cand: _Candidate) -> DiffResult:
    """按 anchor + 已分配的 candidate 判 preserved / relocated / changed / broken。"""

    page_delta = cand.page_index - anchor.page_index
    reason = MatchReason(
        selected_text_similarity=round(cand.text_similarity, 3),
        context_similarity=round(cand.context_similarity, 3),
        layout_score=round(cand.layout_score, 3),
        length_similarity=round(cand.length_similarity, 3),
        page_delta=page_delta,
        candidate_rank=1,
    )
    new_anchor = NewAnchor(
        page_index=cand.page_index,
        quads=cand.quads,
        matched_text=cand.matched_text,
    )

    floor = _active_broken_floor()
    # `preserved_case` 保护罩：same-page exact 且 quad 近邻时绝不被 broken floor 碰。
    # 其他所有会返回 relocated 的分支都要先过 ctx floor —— ctx 过低即语义丢失。
    preserved_case = (
        cand.text_similarity == 1.0
        and page_delta == 0
        and _quads_nearby(anchor.quads, cand.quads, threshold=QUAD_PROXIMITY_THRESHOLD)
    )

    def _maybe_broken(reason_tag: str) -> DiffResult | None:
        if floor is None or preserved_case:
            return None
        if cand.context_similarity >= floor:
            return None
        return DiffResult(
            annotation_id=anchor.annotation_id,
            status="broken",
            confidence=round(cand.score, 3),
            old_anchor=anchor,
            new_anchor=None,
            match_reason=reason,
            review_required=True,
            message=(
                f"Context below floor ({cand.context_similarity:.2f} < {floor:.2f}) "
                f"on {reason_tag}; semantic match lost."
            ),
        )

    # Fuzzy text match —— 区分两种情形：
    #   A) text 高 (≥0.90)：in-place 小编辑 → `changed`，不卡 context 阈值
    #   B) text 中等 + context 强 → `changed`
    #   其他 → `relocated` / broken（看 ctx floor）
    if cand.text_similarity < 1.0:
        high_text_edit = cand.text_similarity >= HIGH_TEXT_CHANGED_MIN
        mid_text_with_ctx = (
            cand.text_similarity >= CHANGED_TEXT_MIN
            and cand.context_similarity >= CHANGED_CONTEXT_THRESHOLD
        )
        same_location_edit = (
            cand.text_similarity >= FUZZY_THRESHOLD
            and page_delta == 0
            and _quads_nearby(anchor.quads, cand.quads, threshold=QUAD_PROXIMITY_THRESHOLD)
        )
        has_exact_geometry = bool(cand.quads)
        if same_location_edit or (
            not has_exact_geometry and (high_text_edit or mid_text_with_ctx)
        ):
            # `changed` 保留，不受 floor 影响 —— 高 text 或 ctx 本身已不低。
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
        broken = _maybe_broken(f"fuzzy-relocated p{cand.page_index}")
        if broken is not None:
            return broken
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
        broken = _maybe_broken(f"exact cross-page {anchor.page_index}→{cand.page_index}")
        if broken is not None:
            return broken
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
    if preserved_case:
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
    broken = _maybe_broken(f"same-page shifted {distance:.1f}pt")
    if broken is not None:
        return broken
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


def _unsupported(anchor: Anchor) -> DiffResult:
    return DiffResult(
        annotation_id=anchor.annotation_id,
        status="unsupported",
        confidence=0.0,
        old_anchor=anchor,
        new_anchor=None,
        match_reason=None,
        review_required=True,
        message=f"Annotation kind {anchor.kind!r} is not text-coverage and cannot be migrated.",
    )


def _is_unsupported(anchor: Anchor) -> bool:
    return anchor.kind not in TEXT_COVERAGE_KINDS


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
