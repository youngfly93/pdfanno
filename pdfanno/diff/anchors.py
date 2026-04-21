"""从旧 PDF 抽取注释的多层锚点 —— PRD §5.1 + §8.2。

Week 1 scope：只处理 highlight / underline / squiggly / strikeout 四种覆盖型注释；
其余 kind（text note、freetext 等）也会产出 Anchor，但 selected_text 会退化为空，
由后续 match 层归到 `unsupported`（Week 1 PoC 中暂时按 broken 处理）。
"""

from __future__ import annotations

import hashlib

import pymupdf

from pdfanno.diff.types import Anchor
from pdfanno.pdf_core.text import normalize_text

CONTEXT_CHARS = 300
TEXT_COVERAGE_KINDS = {"highlight", "underline", "strikeout", "squiggly"}


def extract_anchors(doc: pymupdf.Document, doc_id: str) -> list[Anchor]:
    """遍历 doc，对每条注释生成 Anchor。"""

    out: list[Anchor] = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        page_text = page.get_text("text") or ""
        for annot in page.annots() or []:
            kind = _annot_kind(annot)
            quads = _annot_quads(annot)
            selected_text = _selected_text(page, annot, kind)
            context_before, context_after = _context_window(page_text, selected_text)
            color = _color(annot)
            anchor_id = _local_anchor_id(doc_id, kind, page_idx, selected_text, quads)
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
                )
            )
    return out


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
    """对文本覆盖型注释，用每个 quad 反查文本再拼接；其他 kind 返回空串。"""

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
            txt = (page.get_textbox(rect) or "").strip()
            if txt:
                chunks.append(txt)
        if chunks:
            return " ".join(chunks)
    # fallback: 用 annot.rect 整体取
    return (page.get_textbox(annot.rect) or "").strip()


def _context_window(page_text: str, selected: str) -> tuple[str, str]:
    """在 page_text 中定位 selected，取前后 CONTEXT_CHARS 字符作上下文。"""

    if not selected or not page_text:
        return "", ""
    # 用归一化串匹配定位，但切片从原始 page_text 上取以保留可读性。
    norm_page = normalize_text(page_text)
    norm_sel = normalize_text(selected)
    if not norm_sel:
        return "", ""
    idx = norm_page.find(norm_sel)
    if idx < 0:
        return "", ""
    # 归一化后的索引与原文索引不完全对应，但 300 字符窗口里的偏差可忽略。
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
