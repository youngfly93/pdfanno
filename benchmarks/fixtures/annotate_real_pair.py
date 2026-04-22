"""给真实 arXiv PDF 的 v1 加 N 条 highlight，产出 v1_hl.pdf 供 diff + ground_truth 消费。

用法：
    python -m benchmarks.fixtures.annotate_real_pair V1.pdf V1_HL.pdf -p "phrase 1" -p "phrase 2" ...

**支持重复短 token stress test**：同一 phrase 在列表中出现 N 次，就依次高亮它的第 1、
第 2、...、第 N 次命中（0-indexed 按全局 reading-order）。例如：

    -p "LSTMs" -p "LSTMs" -p "LSTMs"

会高亮 v1 里前 3 次 "LSTMs" —— 这是类比 arXiv 1706 的 BLEU × 2 / WMT × 4 失败模式
的关键。如果只高亮第一次命中，diff 只需做 "1-to-1 全局唯一" 映射，轻松 100%；第
k 次命中（k > 0）要求 `_assign_occurrence_ranks` 正确匹配 + `layout.rank_sim` 与
v2 的第 k 次命中对齐，是 Week 2-4 调参的核心挑战场景。

超过 PDF 实际命中数的请求会跳过并记入 missed（不报错，便于脚本化）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf


def _locate_nth_occurrence(
    doc: pymupdf.Document, phrase: str, n: int
) -> tuple[int, list[float]] | None:
    """按全局 reading-order 找第 n 次命中（0-indexed），返回 (page_idx, quad 8-floats)。

    返回 list[float] 而不是 Quad 对象，避免跨函数作用域传递 PyMuPDF 内部对象引用
    导致的 "annotation not bound to any page" 错误。
    """

    count = 0
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        quads = page.search_for(phrase, quads=True) or []
        for q in quads:
            if count == n:
                return page_idx, [
                    q.ul.x, q.ul.y, q.ur.x, q.ur.y,
                    q.ll.x, q.ll.y, q.lr.x, q.lr.y,
                ]  # fmt: skip
            count += 1
    return None


def annotate(src: Path, dst: Path, phrases: list[str]) -> dict:
    doc = pymupdf.open(str(src))
    seen: dict[str, int] = {}  # phrase -> next occurrence index to highlight
    hit = 0
    missed: list[str] = []
    for phrase in phrases:
        target_idx = seen.get(phrase, 0)
        found = _locate_nth_occurrence(doc, phrase, target_idx)
        if found is None:
            missed.append(f"{phrase}@occ={target_idx}")
            continue
        page_idx, quad_floats = found
        page = doc[page_idx]
        quad = pymupdf.Quad(
            pymupdf.Point(quad_floats[0], quad_floats[1]),
            pymupdf.Point(quad_floats[2], quad_floats[3]),
            pymupdf.Point(quad_floats[4], quad_floats[5]),
            pymupdf.Point(quad_floats[6], quad_floats[7]),
        )
        annot = page.add_highlight_annot(quad)
        annot.set_info(title="real-pair-fixture", content="", subject="Highlight")
        annot.update()
        hit += 1
        seen[phrase] = target_idx + 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dst))
    doc.close()
    return {"hit": hit, "missed": missed, "total": len(phrases)}


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("-p", "--phrase", action="append", default=[], help="可重复；每次一个 phrase")
    args = ap.parse_args()
    if not args.phrase:
        print("error: 至少需要一个 --phrase", file=sys.stderr)
        return 2
    result = annotate(args.src, args.dst, args.phrase)
    print(f"annotated {result['hit']}/{result['total']} phrases into {args.dst}")
    if result["missed"]:
        print(f"  missed ({len(result['missed'])}): {result['missed']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
