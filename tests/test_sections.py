"""sections.py 单测 —— Week 3 C-1 / C-3。

覆盖：
- TOC 路径（PyMuPDF `set_toc`）：层级 / path 构造 / 排序
- font-size 启发路径：heading 检测、合并、路径构造
- 都无时返回空列表
- `section_for` 查找：命中首个、命中中间、命中之前、跨页
"""

from __future__ import annotations

import pymupdf

from pdfanno.diff.sections import SectionSpan, build_section_index, section_for


def _make_doc_with_toc(tmp_path):
    """构造一个 3 页 PDF，用 set_toc 写入 3 级目录。"""

    path = tmp_path / "with_toc.pdf"
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"page {i + 1} body content line a")
        p.insert_text((72, 140), f"page {i + 1} body content line b")
    doc.set_toc(
        [
            [1, "1 Introduction", 1],
            [1, "2 Methods", 2],
            [2, "2.1 Data", 2],
            [2, "2.2 Model", 2],
            [1, "3 Results", 3],
        ]
    )
    doc.save(str(path))
    doc.close()
    return path


def _make_doc_with_headings(tmp_path):
    """无 TOC 的 PDF，靠字号启发：heading 字号显著大于正文。"""

    path = tmp_path / "no_toc.pdf"
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    # body 11pt，heading 16pt；留出空位在同一 y 附近。
    p1.insert_text((72, 100), "1 Introduction", fontsize=16)
    p1.insert_text((72, 140), "body line inside introduction section", fontsize=11)
    p1.insert_text((72, 180), "more body content for context", fontsize=11)
    p1.insert_text((72, 220), "2 Methods", fontsize=16)
    p1.insert_text((72, 260), "description of methodology here", fontsize=11)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 100), "3 Results", fontsize=16)
    p2.insert_text((72, 140), "result discussion body text", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _make_doc_no_structure(tmp_path):
    """无 TOC、无明显字号差的 PDF —— build_section_index 应返回空列表。"""

    path = tmp_path / "flat.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    for i, y in enumerate(range(100, 400, 30)):
        p.insert_text((72, y), f"line {i} of uniform body text", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


# ---------- TOC 路径 ----------


def test_build_section_index_from_toc_has_expected_count(tmp_path) -> None:
    doc = pymupdf.open(str(_make_doc_with_toc(tmp_path)))
    try:
        sections = build_section_index(doc)
    finally:
        doc.close()
    assert len(sections) == 5


def test_build_section_index_from_toc_builds_hierarchical_paths(tmp_path) -> None:
    doc = pymupdf.open(str(_make_doc_with_toc(tmp_path)))
    try:
        sections = build_section_index(doc)
    finally:
        doc.close()
    titles = [s.title for s in sections]
    paths = [s.path for s in sections]
    assert titles == [
        "1 Introduction",
        "2 Methods",
        "2.1 Data",
        "2.2 Model",
        "3 Results",
    ]
    # 关键：2.1 的 path 应包含祖先 "2 Methods"
    assert paths[2] == "2 Methods / 2.1 Data"
    assert paths[3] == "2 Methods / 2.2 Model"
    # 3 Results 是 level-1 —— 不应继承 2 Methods 的路径。
    assert paths[4] == "3 Results"
    # 1 Introduction 也只有自身。
    assert paths[0] == "1 Introduction"


def test_build_section_index_from_toc_sorts_by_page(tmp_path) -> None:
    doc = pymupdf.open(str(_make_doc_with_toc(tmp_path)))
    try:
        sections = build_section_index(doc)
    finally:
        doc.close()
    pages = [s.page_index for s in sections]
    assert pages == sorted(pages)


# ---------- font-heuristic 路径 ----------


def test_build_section_index_from_font_heuristic(tmp_path) -> None:
    doc = pymupdf.open(str(_make_doc_with_headings(tmp_path)))
    try:
        sections = build_section_index(doc)
    finally:
        doc.close()
    titles = [s.title for s in sections]
    # heuristic 应该至少捕到三个编号 heading；合并后数量 ≥ 3。
    assert any("Introduction" in t for t in titles)
    assert any("Methods" in t for t in titles)
    assert any("Results" in t for t in titles)


def test_build_section_index_empty_on_flat_doc(tmp_path) -> None:
    """所有行字号一致 → heuristic 拿不到 heading，返回空列表。"""

    doc = pymupdf.open(str(_make_doc_no_structure(tmp_path)))
    try:
        sections = build_section_index(doc)
    finally:
        doc.close()
    assert sections == []


# ---------- section_for 查找 ----------


def test_section_for_returns_none_on_empty_index() -> None:
    assert section_for([], 0, 100.0) is None


def test_section_for_returns_none_before_first_section() -> None:
    idx = [
        SectionSpan(page_index=1, y_top=100.0, title="Only", level=1, path="Only"),
    ]
    # 位置在 page 0 —— 在任何 section 之前。
    assert section_for(idx, 0, 50.0) is None


def test_section_for_matches_last_preceding_span() -> None:
    idx = [
        SectionSpan(page_index=0, y_top=0.0, title="A", level=1, path="A"),
        SectionSpan(page_index=0, y_top=300.0, title="B", level=1, path="B"),
        SectionSpan(page_index=1, y_top=0.0, title="C", level=1, path="C"),
    ]
    # 第 0 页 y=50 → A；y=350 → B；第 1 页任意 y → C
    assert section_for(idx, 0, 50.0).title == "A"
    assert section_for(idx, 0, 350.0).title == "B"
    assert section_for(idx, 1, 100.0).title == "C"
    assert section_for(idx, 1, 0.0).title == "C"


def test_section_for_handles_boundary_exactly() -> None:
    """y_top 恰好等于 section 起点 —— 定义为落入该 section。"""

    idx = [
        SectionSpan(page_index=0, y_top=0.0, title="A", level=1, path="A"),
        SectionSpan(page_index=0, y_top=200.0, title="B", level=1, path="B"),
    ]
    assert section_for(idx, 0, 200.0).title == "B"


def test_path_resets_on_same_level_sibling(tmp_path) -> None:
    """level 2 之后再见 level 2 应替换而非累加（2.1 -> 2.2 不能变成 2.1 / 2.2）。"""

    path = tmp_path / "siblings.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.set_toc(
        [
            [1, "1 A", 1],
            [2, "1.1 AA", 1],
            [2, "1.2 AB", 1],
        ]
    )
    doc.save(str(path))
    doc.close()

    doc = pymupdf.open(str(path))
    try:
        idx = build_section_index(doc)
    finally:
        doc.close()
    # 两个 1.x 都应该挂在 "1 A" 下，互不叠加。
    assert idx[1].path == "1 A / 1.1 AA"
    assert idx[2].path == "1 A / 1.2 AB"
