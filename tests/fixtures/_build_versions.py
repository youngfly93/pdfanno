"""为 v0.2 diff/migrate 功能构造 (v1, v2) PDF 版本对。

每对 fixture 故意把 v2 的变化限制在一个维度，便于 Week 1 PoC 对准期望：
- `identical`：完全相同，所有 v1 highlight 应 preserved。
- `reordered`：v2 在 v1 之前插入内容，让部分文本跨页漂移，期望 relocated。
- `partial`：v2 删除了 v1 中一条特定文本，期望对应 highlight broken。

fixture 里插入 highlight 使用外部（非 pdfanno）流程生成 —— 直接 `add_highlight_annot`，
不写 /NM，这样 `diff` 功能测试跑的路径和 "用户用别的工具标注过 v1" 一致。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def _new_page(doc: pymupdf.Document, lines: list[str], start_y: float = 80.0) -> pymupdf.Page:
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    y = start_y
    for line in lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 24
    return page


def _highlight(page: pymupdf.Page, text: str, color: tuple[float, float, float]) -> None:
    quads = page.search_for(text, quads=True)
    if not quads:
        raise RuntimeError(f"fixture build: 找不到 highlight 文本 {text!r}")
    annot = page.add_highlight_annot(quads)
    annot.set_colors(stroke=color)
    annot.set_info(title="external-reader", subject="Highlight")
    annot.update()


def build_pair_identical(out_dir: Path) -> tuple[Path, Path]:
    """v1 和 v2 完全相同。v1 标 4 条 highlight，全部期望 preserved。"""

    pages_v1 = [
        [
            "Alpha the first sentence about protein A.",
            "Beta the second sentence about pathway B.",
            "Gamma the third sentence about enzyme C.",
            "Delta the fourth sentence about reaction D.",
        ],
        [
            "Epsilon the fifth sentence about kinase E.",
            "Zeta the sixth sentence about receptor F.",
            "Eta the seventh sentence about channel G.",
            "Theta the eighth sentence about tumor H.",
        ],
    ]

    def _build(lines_per_page: list[list[str]], path: Path) -> Path:
        doc = pymupdf.open()
        for lines in lines_per_page:
            _new_page(doc, lines)
        if path.name == "identical_v1.pdf":
            _highlight(doc[0], "Beta the second sentence about pathway B.", (1, 1, 0))
            _highlight(doc[0], "Delta the fourth sentence about reaction D.", (1, 1, 0))
            _highlight(doc[1], "Epsilon the fifth sentence about kinase E.", (0.5, 1, 0.5))
            _highlight(doc[1], "Theta the eighth sentence about tumor H.", (0.5, 1, 0.5))
        doc.save(str(path))
        doc.close()
        return path

    v1 = _build(pages_v1, out_dir / "identical_v1.pdf")
    v2 = _build(pages_v1, out_dir / "identical_v2.pdf")
    return v1, v2


def build_pair_reordered(out_dir: Path) -> tuple[Path, Path]:
    """v2 在页首插入新段落，让部分 v1 内容跨页漂移。

    期望：同页仍能找到的仍 preserved / relocated(同页 page_delta=0)；被挤到下一页
    的走 relocated(page_delta!=0)。
    """

    pages_v1 = [
        [
            "LineA1 sentence one on page one.",
            "LineA2 sentence two on page one.",
            "LineA3 sentence three on page one.",
            "LineA4 sentence four on page one.",
            "LineA5 sentence five on page one.",
        ],
        [
            "LineB1 sentence one on page two.",
            "LineB2 sentence two on page two.",
        ],
    ]
    # v2 在 page 1 开头插入一段很长的新内容，把 LineA5 顶到 page 2。
    pages_v2 = [
        [
            "NewIntro alpha inserted content one two three four.",
            "NewIntro beta inserted content one two three four.",
            "NewIntro gamma inserted content one two three four.",
            "NewIntro delta inserted content one two three four.",
            "LineA1 sentence one on page one.",
            "LineA2 sentence two on page one.",
            "LineA3 sentence three on page one.",
            "LineA4 sentence four on page one.",
        ],
        [
            "LineA5 sentence five on page one.",
            "LineB1 sentence one on page two.",
            "LineB2 sentence two on page two.",
        ],
    ]

    def _build(lines_per_page: list[list[str]], path: Path) -> Path:
        doc = pymupdf.open()
        for lines in lines_per_page:
            _new_page(doc, lines)
        if path.name == "reordered_v1.pdf":
            _highlight(doc[0], "LineA2 sentence two on page one.", (1, 1, 0))
            _highlight(doc[0], "LineA5 sentence five on page one.", (1, 1, 0))
            _highlight(doc[1], "LineB1 sentence one on page two.", (0.5, 0.8, 1))
        doc.save(str(path))
        doc.close()
        return path

    v1 = _build(pages_v1, out_dir / "reordered_v1.pdf")
    v2 = _build(pages_v2, out_dir / "reordered_v2.pdf")
    return v1, v2


def build_pair_partial(out_dir: Path) -> tuple[Path, Path]:
    """v2 删除了 v1 中一条独特句子。对应 v1 highlight 期望 broken。"""

    pages_v1 = [
        [
            "AlphaUnique keyword only appears in alpha line.",
            "BetaUnique keyword only appears in beta line.",
            "GammaUnique keyword only appears in gamma line.",
        ],
    ]
    # v2 删除 BetaUnique 那一行。
    pages_v2 = [
        [
            "AlphaUnique keyword only appears in alpha line.",
            "GammaUnique keyword only appears in gamma line.",
        ],
    ]

    def _build(lines_per_page: list[list[str]], path: Path) -> Path:
        doc = pymupdf.open()
        for lines in lines_per_page:
            _new_page(doc, lines)
        if path.name == "partial_v1.pdf":
            _highlight(doc[0], "AlphaUnique keyword only appears in alpha line.", (1, 1, 0))
            _highlight(doc[0], "BetaUnique keyword only appears in beta line.", (1, 1, 0))
            _highlight(doc[0], "GammaUnique keyword only appears in gamma line.", (1, 1, 0))
        doc.save(str(path))
        doc.close()
        return path

    v1 = _build(pages_v1, out_dir / "partial_v1.pdf")
    v2 = _build(pages_v2, out_dir / "partial_v2.pdf")
    return v1, v2


def build_all_pairs(fixture_dir: Path) -> dict[str, tuple[Path, Path]]:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    return {
        "identical": build_pair_identical(fixture_dir),
        "reordered": build_pair_reordered(fixture_dir),
        "partial": build_pair_partial(fixture_dir),
    }
