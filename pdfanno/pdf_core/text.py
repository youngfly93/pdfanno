"""文本搜索与归一化。

plan.md §6.3：`normalized_text` 定义为命中原文的 `\\s+` → 空格 + strip。
plan.md §6.4：v1 只做 literal / ignore-case，不做 regex（v1.5）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class TextMatch:
    """页面内单条命中。quads 已按 PyMuPDF 默认顺序（每个 quad 8 个 float）。"""

    page: int
    matched_text: str
    quads: list[list[float]]


def normalize_text(text: str) -> str:
    """按 plan.md §6.3 归一化用于 annotation_id hash 的文本。"""

    return _WHITESPACE_RE.sub(" ", text).strip()


def search_page(page: pymupdf.Page, needle: str, *, ignore_case: bool = False) -> list[TextMatch]:
    """在单页中搜索 literal 串。

    PyMuPDF 对跨行命中返回多个 quads，且不暴露 "一条命中边界"。Stage A 的策略是：
    每个 quad 当作一条独立命中，生成独立 annotation_id。跨行查询会产出多条高亮，
    视觉上等价于每行分别高亮，也满足幂等（re-run 各自 dedup）。

    注：PyMuPDF 默认对 ASCII 字母忽略大小写（历史行为），参数 `ignore_case` 当前不改变行为，
    留作 Stage B 的 page.get_text("words") 路径的扩展位。
    """

    _ = ignore_case  # Stage A 不再额外处理；保留参数以便 Stage B 扩展。
    quads = page.search_for(needle, quads=True)
    if not quads:
        return []
    return [
        TextMatch(
            page=page.number,
            matched_text=needle,
            quads=[_quad_to_floats(q)],
        )
        for q in quads
    ]


def _quad_to_floats(quad: pymupdf.Quad) -> list[float]:
    """PyMuPDF Quad → 8 个 float 的列表（ul, ur, ll, lr 顺序）。

    plan.md §8.2 的 quads 字段格式 [x1,y1,x2,y2,x3,y3,x4,y4]。
    """

    return [
        quad.ul.x,
        quad.ul.y,
        quad.ur.x,
        quad.ur.y,
        quad.ll.x,
        quad.ll.y,
        quad.lr.x,
        quad.lr.y,
    ]


def floats_to_quad(values: list[float]) -> pymupdf.Quad:
    """8 个 float → PyMuPDF Quad。"""

    if len(values) != 8:
        raise ValueError(f"quad 必须有 8 个 float，实际 {len(values)}")
    return pymupdf.Quad(
        pymupdf.Point(values[0], values[1]),
        pymupdf.Point(values[2], values[3]),
        pymupdf.Point(values[4], values[5]),
        pymupdf.Point(values[6], values[7]),
    )
