"""PDF 注释的读写。

plan.md §6.3：写入 PDF 时，`annotation_id` 存入 /NM 字段；其他阅读器若丢弃该字段，
Stage C 引入 sidecar 后以 sidecar 为权威。
plan.md §11：尊重已有注释，绝不覆盖外部阅读器创建的 annot。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import pymupdf

from pdfanno.pdf_core.text import floats_to_quad

# /NM 之外的标记，用于识别 pdfanno 自己创建的注释。
PDFANNO_SUBJECT = "pdfanno"


@dataclass(frozen=True)
class ExistingAnnotation:
    """list 命令读回的注释视图。外部创建的没有 stable id，只回 xref。"""

    page: int
    xref: int
    kind: str
    rect: tuple[float, float, float, float]
    color: list[float] | None
    contents: str
    title: str
    subject: str
    name: str | None  # /NM；pdfanno 创建的 = annotation_id


def add_highlight(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    *,
    quads_floats: list[list[float]],
    color: list[float],
    annotation_id: str,
    contents: str = "",
) -> pymupdf.Annot:
    """写入 highlight annotation 并把 `annotation_id` 存到 /NM。

    `color` 为三元 RGB，分量 [0,1]。`contents` 是用户可见 note，默认空。
    """

    quads = [floats_to_quad(q) for q in quads_floats]
    annot = page.add_highlight_annot(quads)
    stroke = (float(color[0]), float(color[1]), float(color[2]))
    annot.set_colors(stroke=stroke)
    annot.set_info(
        title="pdfanno",
        content=contents,
        subject=PDFANNO_SUBJECT,
    )
    _write_annot_name(doc, annot, annotation_id)
    annot.update()
    return annot


def add_note(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    *,
    point: tuple[float, float],
    contents: str,
    annotation_id: str,
) -> pymupdf.Annot:
    """写入 sticky text annotation（note）并把 `annotation_id` 存到 /NM。"""

    pt = pymupdf.Point(float(point[0]), float(point[1]))
    annot = page.add_text_annot(pt, contents)
    annot.set_info(title="pdfanno", content=contents, subject=PDFANNO_SUBJECT)
    _write_annot_name(doc, annot, annotation_id)
    annot.update()
    return annot


def read_annotations(doc: pymupdf.Document) -> list[ExistingAnnotation]:
    """读取所有页面的现有注释。不区分 pdfanno 创建 vs 外部创建。"""

    out: list[ExistingAnnotation] = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        for annot in page.annots() or []:
            info = annot.info
            color = None
            colors = annot.colors or {}
            stroke = colors.get("stroke")
            if stroke:
                color = [float(c) for c in stroke]
            rect = annot.rect
            out.append(
                ExistingAnnotation(
                    page=page_idx,
                    xref=annot.xref,
                    kind=_annot_kind_name(annot),
                    rect=(rect.x0, rect.y0, rect.x1, rect.y1),
                    color=color,
                    contents=info.get("content", "") or "",
                    title=info.get("title", "") or "",
                    subject=info.get("subject", "") or "",
                    name=_read_annot_name(doc, annot),
                )
            )
    return out


def read_annotation_quads(doc: pymupdf.Document) -> list[dict]:
    """读取现有注释并还原其 quads（或退化为 rect 的点四元组）。

    供 `extract --format plan` 使用。返回的 dict 包含 annotation_id（从 /NM）、
    page、kind、quads（8-float list-of-list）、color、contents、rect、subject。
    """

    out: list[dict] = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        for annot in page.annots() or []:
            info = annot.info
            colors = annot.colors or {}
            stroke = colors.get("stroke") or [1.0, 1.0, 0.0]
            color = [float(c) for c in stroke]
            quads = _extract_quads(annot)
            out.append(
                {
                    "annotation_id": _read_annot_name(doc, annot),
                    "page": page_idx,
                    "kind": _annot_kind_name(annot),
                    "quads": quads,
                    "color": color,
                    "contents": info.get("content", "") or "",
                    "subject": info.get("subject", "") or "",
                    "xref": annot.xref,
                }
            )
    return out


def _extract_quads(annot: pymupdf.Annot) -> list[list[float]]:
    """从 annot 读取 quads（8 floats 一组）。

    对 highlight / underline / strikeout / squiggly，PyMuPDF 暴露 vertices 为点序列。
    对 text/freetext 等无 quad 的注释，用 rect 的四个角退化成一个 quad。
    """

    vertices = getattr(annot, "vertices", None)
    if vertices and len(vertices) >= 4 and len(vertices) % 4 == 0:
        quads: list[list[float]] = []
        for i in range(0, len(vertices), 4):
            pts = vertices[i : i + 4]
            quads.append([float(pts[0][0]), float(pts[0][1]),
                          float(pts[1][0]), float(pts[1][1]),
                          float(pts[2][0]), float(pts[2][1]),
                          float(pts[3][0]), float(pts[3][1])])  # fmt: skip
        return quads

    rect = annot.rect
    return [
        [
            rect.x0,
            rect.y0,
            rect.x1,
            rect.y0,
            rect.x0,
            rect.y1,
            rect.x1,
            rect.y1,
        ]  # fmt: skip
    ]


def existing_pdfanno_ids(doc: pymupdf.Document) -> set[str]:
    """返回 PDF 里已有、由 pdfanno 创建的注释 id 集合，用于幂等去重。"""

    ids: set[str] = set()
    for ann in read_annotations(doc):
        if ann.subject == PDFANNO_SUBJECT and ann.name:
            ids.add(ann.name)
    return ids


# ---------- 内部：/NM 读写 ----------


def _write_annot_name(doc: pymupdf.Document, annot: pymupdf.Annot, name: str) -> None:
    """把 `name` 写入注释的 /NM。失败时不抛 —— /NM 是 best-effort，sidecar 才是权威。"""

    with contextlib.suppress(Exception):
        doc.xref_set_key(annot.xref, "NM", _pdf_text_string(name))


def _read_annot_name(doc: pymupdf.Document, annot: pymupdf.Annot) -> str | None:
    """读 /NM，返回解码后的字符串；不存在返回 None。"""

    try:
        kind, value = doc.xref_get_key(annot.xref, "NM")
    except Exception:
        return None
    if kind == "string" and isinstance(value, str):
        return value
    return None


def _pdf_text_string(s: str) -> str:
    """序列化为 PDF 字面字符串格式。ASCII 安全走 `(...)`，否则用 utf-16-be hex。"""

    if s.isascii() and not any(c in "()\\" for c in s):
        return f"({s})"
    return "<" + s.encode("utf-16-be").hex() + ">"


def _annot_kind_name(annot: pymupdf.Annot) -> str:
    """统一注释 kind 命名。PyMuPDF 的 annot.type 返回 (num, name_str)。"""

    kind = annot.type
    if isinstance(kind, tuple) and len(kind) >= 2:
        return str(kind[1]).lower()
    return "unknown"
