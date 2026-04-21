"""给真实 arXiv PDF 的 v1 加 N 条 highlight，产出 v1_hl.pdf 供 diff + ground_truth 消费。

用法：
    python -m benchmarks.fixtures.annotate_real_pair V1.pdf V1_HL.pdf -p "phrase 1" -p "phrase 2" ...

对每个 phrase 只高亮 **第一次** 命中，避免把同样的 short token 一次性打爆。
如果 phrase 在 PDF 里找不到，跳过并打印警告（不报错，便于脚本跑多篇）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf


def annotate(src: Path, dst: Path, phrases: list[str]) -> dict:
    doc = pymupdf.open(str(src))
    hit = 0
    missed: list[str] = []
    for phrase in phrases:
        placed = False
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            quads = page.search_for(phrase, quads=True) or []
            if not quads:
                continue
            annot = page.add_highlight_annot(quads[0])
            annot.set_info(title="real-pair-fixture", content="", subject="Highlight")
            annot.update()
            hit += 1
            placed = True
            break
        if not placed:
            missed.append(phrase)
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
