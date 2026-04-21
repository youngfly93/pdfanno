"""从旧 PDF 抽取注释的多层锚点 —— PRD §5.1 + §8.2。

Week 1 scope：只处理 highlight / underline / squiggly / strikeout 四种覆盖型注释；
其余 kind（text note、freetext 等）也会产出 Anchor，但 selected_text 会退化为空，
由后续 match 层归到 `unsupported`（Week 1 PoC 中暂时按 broken 处理）。
"""

from __future__ import annotations

import hashlib

import pymupdf

from pdfanno.diff.sections import build_section_index, section_for
from pdfanno.diff.types import Anchor
from pdfanno.pdf_core.text import normalize_text

CONTEXT_CHARS = 300
TEXT_COVERAGE_KINDS = {"highlight", "underline", "strikeout", "squiggly"}


def extract_anchors(doc: pymupdf.Document, doc_id: str) -> list[Anchor]:
    """遍历 doc，对每条注释生成 Anchor。"""

    section_index = build_section_index(doc)
    out: list[Anchor] = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        page_text = page.get_text("text") or ""
        page_rect = page.rect
        for annot in page.annots() or []:
            kind = _annot_kind(annot)
            quads = _annot_quads(annot)
            selected_text = _selected_text(page, annot, kind)
            # v0.2.1 曾尝试按 anchor 的 quad 中心定位它是 page 里第几次出现，再抽对应
            # 位置的 ctx 窗口（"语义正确" 的做法）。实测 arXiv status 92.3→89.7%，
            # location 56.4→48.7% —— 版本重构后 anchor 的 "真实位置" ctx 和目标位置的
            # ctx 不再对齐。保持 v0.2.0 的 "取第一次出现 ctx" 行为反而稳。详见
            # benchmarks/reports/week4_ctx_experiment.md。
            context_before, context_after = _context_window(page_text, selected_text)
            color = _color(annot)
            anchor_id = _local_anchor_id(doc_id, kind, page_idx, selected_text, quads)
            # anchor 的 y 中心用于 section 定位
            y_center = sum(quads[0][1::2]) / 4 if quads and len(quads[0]) >= 8 else 0.0
            section = section_for(section_index, page_idx, y_center)
            out.append(
                Anchor(
                    annotation_id=anchor_id,
                    doc_id=doc_id,
                    kind=kind,
                    page_index=page_idx,
                    quads=quads,
                    selected_text=selected_text,
                    context_before=context_before,
                    context_after=context_after,
                    text_hash=_sha256(normalize_text(selected_text)),
                    context_hash=_sha256(
                        normalize_text(context_before) + "||" + normalize_text(context_after)
                    ),
                    color=color,
                    note=annot.info.get("content", "") or "",
                    page_width=float(page_rect.width),
                    page_height=float(page_rect.height),
                    section_path=section.path if section else None,
                )
            )
    _assign_occurrence_ranks(doc, out)
    return out


def _assign_occurrence_ranks(doc: pymupdf.Document, anchors: list[Anchor]) -> None:
    """对每个独特 `selected_text`，按文档阅读顺序 (page, y, x) 给每次出现排 rank，
    再把 anchor 的 quad 中心映射到最近的出现位置取 occurrence_rank。

    结果 in-place 写入 anchor.occurrence_rank / total_occurrences。
    """

    queries = {normalize_text(a.selected_text) for a in anchors if a.selected_text}
    # 每个 query 的 reading-order 位置列表
    occ_by_query: dict[str, list[tuple[int, float, float]]] = {q: [] for q in queries}
    for p_idx in range(doc.page_count):
        page = doc[p_idx]
        for q in queries:
            for quad in page.search_for(q, quads=True) or []:
                cx = (quad.ul.x + quad.lr.x) / 2
                cy = (quad.ul.y + quad.lr.y) / 2
                occ_by_query[q].append((p_idx, cy, cx))
    for q in occ_by_query:
        occ_by_query[q].sort(key=lambda t: (t[0], t[1], t[2]))

    for i, anchor in enumerate(anchors):
        q = normalize_text(anchor.selected_text)
        occs = occ_by_query.get(q, [])
        if not occs or not anchor.quads:
            continue
        # 找与 anchor 中心最近的 occurrence
        target_cx = (anchor.quads[0][0] + anchor.quads[0][6]) / 2
        target_cy = (anchor.quads[0][1] + anchor.quads[0][7]) / 2
        best_idx, best_d = None, float("inf")
        for k, (p, cy, cx) in enumerate(occs):
            if p != anchor.page_index:
                continue
            d = (cx - target_cx) ** 2 + (cy - target_cy) ** 2
            if d < best_d:
                best_d = d
                best_idx = k
        if best_idx is None:
            continue
        # 写回（Anchor 是 pydantic frozen=False，可直接修改）
        anchors[i] = anchor.model_copy(
            update={
                "occurrence_rank": best_idx,
                "total_occurrences": len(occs),
            }
        )


def _annot_kind(annot: pymupdf.Annot) -> str:
    t = annot.type
    if isinstance(t, tuple) and len(t) >= 2:
        return str(t[1]).lower()
    return "unknown"


def _annot_quads(annot: pymupdf.Annot) -> list[list[float]]:
    """复用 pdf_core.annotations 的 _extract_quads 逻辑（局部重实现避免循环依赖）。"""

    vertices = getattr(annot, "vertices", None)
    if vertices and len(vertices) >= 4 and len(vertices) % 4 == 0:
        quads: list[list[float]] = []
        for i in range(0, len(vertices), 4):
            pts = vertices[i : i + 4]
            quads.append(
                [
                    float(pts[0][0]),
                    float(pts[0][1]),
                    float(pts[1][0]),
                    float(pts[1][1]),
                    float(pts[2][0]),
                    float(pts[2][1]),
                    float(pts[3][0]),
                    float(pts[3][1]),
                ]  # fmt: skip
            )
        return quads
    r = annot.rect
    return [[r.x0, r.y0, r.x1, r.y0, r.x0, r.y1, r.x1, r.y1]]


def _selected_text(page: pymupdf.Page, annot: pymupdf.Annot, kind: str) -> str:
    """对文本覆盖型注释，用每个 quad 反查文本再拼接；其他 kind 返回空串。

    v0.2.1：混合策略。先用 `page.get_textbox(rect)` 拿 char-level 精确文本（保留旧
    行为在宽松版式下的精细 x-clip），若结果含 `\\n`（紧排版相邻行 leak 的特征），
    才切换到 word-level y-中心过滤：

        'pared to the pre\\nneural network\\nmputational cos'
            ↑ 触发 \\n → 切 word-level → 'neural network'

    动机（详见 week5_widen_bench.md）：
    - 单纯切 word-level 会让 BLEU → BLEU,（带标点）、residual connection →
      residual connections，`search_for` 回流命中数会变，破坏 arXiv 1706 基线。
    - 保留 `get_textbox` 的精细 x-clip 行为可维持已通过基准；仅在 leak 触发时退到
      word-level。Word2Vec / Seq2Seq 的紧排版正好属于 leak 触发场景。
    """

    if kind not in TEXT_COVERAGE_KINDS:
        return ""
    vertices = getattr(annot, "vertices", None)
    if vertices and len(vertices) >= 4 and len(vertices) % 4 == 0:
        chunks: list[str] = []
        for i in range(0, len(vertices), 4):
            pts = vertices[i : i + 4]
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            rect = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
            txt = _clip_text_to_rect(page, rect)
            if txt:
                chunks.append(txt)
        if chunks:
            return " ".join(chunks)
    # fallback: 用 annot.rect 整体取
    return _clip_text_to_rect(page, annot.rect)


def _clip_text_to_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> str:
    """混合策略：先 char-level `get_textbox`；若含 `\\n` 表示跨行 leak，降级 word-level。"""

    raw = (page.get_textbox(rect) or "").strip()
    if "\n" not in raw:
        return raw
    filtered = _words_in_quad_rect(page, rect)
    return filtered or raw.replace("\n", " ")


def _words_in_quad_rect(page: pymupdf.Page, rect: pymupdf.Rect, *, y_eps: float = 1.0) -> str:
    """只保留 word 的 y-中心落在 rect.y0 - y_eps .. rect.y1 + y_eps 之间，
    并且 word 的 x-bbox 与 rect x-轴有重叠的词；按 reading order 拼接。

    y_eps 容忍上下标 / font descenders 导致的小偏移；1pt 足够宽松版式论文，
    同时足够严格去掉紧排版的相邻行 leak（相邻行 y-中心相距 ≥ 10pt）。
    """

    words = page.get_text("words") or []
    y_lo = rect.y0 - y_eps
    y_hi = rect.y1 + y_eps
    kept: list[tuple[int, int, int, str]] = []
    for w in words:
        if len(w) < 8:
            continue
        x0, y0, x1, y1, text, block_no, line_no, word_no = w[:8]
        cy = (y0 + y1) / 2
        if cy < y_lo or cy > y_hi:
            continue
        overlap = min(x1, rect.x1) - max(x0, rect.x0)
        if overlap <= 0:
            continue
        kept.append((block_no, line_no, word_no, text))
    # PyMuPDF 的 words 已经按 (block, line, word) 排好；这里显式再排一次确保稳定。
    kept.sort(key=lambda t: (t[0], t[1], t[2]))
    return " ".join(t[3] for t in kept)


def _context_window(page_text: str, selected: str) -> tuple[str, str]:
    """在 page_text 中定位 selected 的第一次出现，取前后 CONTEXT_CHARS 字符作上下文。

    设计取舍（v0.2.1 实验确认）：
    - "语义正确" 的做法是按 anchor 的 quad 中心定位到第 k 次出现再抽 ctx。
    - 实测 arXiv 反而回退（详见 week4_ctx_experiment.md）：版本重构后 anchor 真实位置
      的 ctx 和候选位置的 ctx 不再对齐，而取 "第一次出现" 的 ctx 相对更稳定。
    - 暂时保留这个 "首次出现" 行为，直到有信号/eval 能说明 per-anchor ctx 胜出。
    """

    if not selected or not page_text:
        return "", ""
    norm_page = normalize_text(page_text)
    norm_sel = normalize_text(selected)
    if not norm_sel:
        return "", ""
    idx = norm_page.find(norm_sel)
    if idx < 0:
        return "", ""
    before = norm_page[max(0, idx - CONTEXT_CHARS) : idx]
    after = norm_page[idx + len(norm_sel) : idx + len(norm_sel) + CONTEXT_CHARS]
    return before, after


def _color(annot: pymupdf.Annot) -> list[float] | None:
    c = (annot.colors or {}).get("stroke")
    if not c:
        return None
    return [float(v) for v in c]


def _local_anchor_id(
    doc_id: str, kind: str, page: int, selected_text: str, quads: list[list[float]]
) -> str:
    """给旧注释一个稳定的 local id，仅在 diff 上下文里唯一。

    不和 v0.1.x 的 `annotation_id`（/NM）混用 —— 那套是 v0.1 pdfanno 自己创建的注释才有的。
    外部阅读器创建的 highlight 通常没有 /NM，所以这里用 (doc_id, kind, page, text, quads) 合成。
    """

    payload = f"{doc_id}|{kind}|{page}|{normalize_text(selected_text)}|{quads}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"anc_{digest}"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
