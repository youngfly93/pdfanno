"""Fixtures 构造脚本 —— 可读、可审计的 PyMuPDF 代码。

规则：
- 所有 fixture 文本都用常见 ASCII 论文词汇，保证 PyMuPDF 文本提取稳定。
- 不提交二进制 PDF；每次测试 session 在 tmp 目录即时生成。
- 旋转 fixture 必须能独立验证 annotation_id 的 roundtrip 稳定性。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

# A4 点坐标
PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def build_simple(path: Path) -> Path:
    """单页 PDF，含多个可搜索词。"""

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72, 100), "hello world from pdfanno")
    page.insert_text((72, 140), "a transformer learns attention over sequences")
    page.insert_text((72, 180), "we find transformer models useful for annotation")
    page.insert_text((72, 220), "end of the simple fixture page")
    doc.save(str(path))
    doc.close()
    return path


def build_existing_annotations(path: Path) -> Path:
    """预先包含一个外部高亮（非 pdfanno 创建）的 PDF。"""

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72, 100), "existing highlight here")
    page.insert_text((72, 140), "another line without highlight")
    quads = page.search_for("existing highlight", quads=True)
    if quads:
        annot = page.add_highlight_annot(quads)
        annot.set_info(title="external-reader", content="preset note", subject="Highlight")
        annot.update()
    doc.save(str(path))
    doc.close()
    return path


def build_rotated(path: Path, rotation: int) -> Path:
    """旋转页面 fixture。搜索/高亮/annotation_id 必须在旋转下稳定。"""

    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"rotation 只支持 0/90/180/270，收到 {rotation}")
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72, 100), "rotated transformer sample text")
    page.insert_text((72, 140), "second line for rotation testing")
    if rotation:
        page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def build_two_columns(path: Path) -> Path:
    """双栏排版 fixture。左栏与右栏各含 "transformer" 关键词一次。

    坐标测试要求：搜索命中不应跨栏合并。
    """

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    gap = 20
    col_width = (PAGE_WIDTH - 2 * 50 - gap) / 2
    left = pymupdf.Rect(50, 80, 50 + col_width, PAGE_HEIGHT - 80)
    right = pymupdf.Rect(50 + col_width + gap, 80, PAGE_WIDTH - 50, PAGE_HEIGHT - 80)
    page.insert_textbox(
        left,
        "Left column talks about transformer models.\nMore left column content here.",
        fontsize=11,
    )
    page.insert_textbox(
        right,
        "Right column mentions transformer attention.\nRight column ends here.",
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()
    return path


def build_scanned_no_text(path: Path) -> Path:
    """无 text layer 的 PDF —— 模拟扫描件。

    仅画一个矩形 + 一张小图的底色占位，不 insert_text，保证 get_text 返回空串。
    """

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.draw_rect(
        pymupdf.Rect(100, 100, 300, 300), color=(0.2, 0.2, 0.2), fill=(0.9, 0.9, 0.9)
    )
    doc.save(str(path))
    doc.close()
    return path


def build_encrypted(path: Path, user_pw: str = "user", owner_pw: str = "owner") -> Path:
    """AES-256 加密 PDF，保留明文即可 open 元数据（不 authenticate 即可读页数但不可改）。"""

    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72, 100), "encrypted content line")
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw=owner_pw,
        user_pw=user_pw,
    )
    doc.close()
    return path


def build_all(fixture_dir: Path) -> dict[str, Path]:
    """批量构造 Stage A+B 的全部 fixture，返回 name -> path 的映射。"""

    fixture_dir.mkdir(parents=True, exist_ok=True)
    return {
        "simple": build_simple(fixture_dir / "simple.pdf"),
        "existing_annotations": build_existing_annotations(
            fixture_dir / "existing_annotations.pdf"
        ),
        "rotated_90": build_rotated(fixture_dir / "rotated_90.pdf", 90),
        "rotated_270": build_rotated(fixture_dir / "rotated_270.pdf", 270),
        "two_columns": build_two_columns(fixture_dir / "two_columns.pdf"),
        "scanned_no_text": build_scanned_no_text(fixture_dir / "scanned_no_text.pdf"),
        "encrypted": build_encrypted(fixture_dir / "encrypted.pdf"),
    }
