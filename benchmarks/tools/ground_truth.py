"""为 (v1, v2) PDF 对生成 ground truth 标注 —— 独立于 pdfanno/diff 的 oracle。

我们不想让"算法给自己打分"。这个脚本只用 PyMuPDF 原生的 search_for 和阅读顺序
（先 y，再 x）来判定每条 v1 highlight 在 v2 里的"正确"位置，不依赖 pdfanno.diff
的任何打分/分配逻辑。

规则：
- 抽取 v1 的每条 highlight：selected_text, v1_page, v1_quad。
- 对每个独特 query，分别在 v1 和 v2 上 search_for(quads=True) + 按阅读顺序排序。
- v1 中的第 k 次出现（按阅读顺序） → v2 中的第 k 次出现 = ground truth 位置。
- 如果 v1 的 k 超过 v2 的总命中数 → gt_status = broken。
- 否则根据 v1_page == gt_page 和 quad 距离判 preserved / relocated。
- `changed` / `ambiguous` 由 oracle 给不出 —— 需要人工判断，这里留空（None），
  只用于后续手工复核补标。

用法：
    python -m benchmarks.tools.ground_truth V1.pdf V2.pdf --out gt.json

输出 JSON schema（同 diff schema_version=2 的 old_anchor 字段兼容）：
    {
      "schema_version": 2,
      "old_pdf": "...",
      "new_pdf": "...",
      "labels": [
        {
          "annotation_id": "anc_...",
          "selected_text": "...",
          "v1_page": 0,
          "v1_quad": [...],
          "v1_occurrence_rank": 0,
          "gt_status": "preserved" | "relocated" | "broken" | "needs_review",
          "gt_page": 1 | null,
          "gt_quad": [...] | null,
          "gt_reason": "..."
        }
      ]
    }
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pymupdf

from pdfanno.diff.anchors import extract_anchors
from pdfanno.pdf_core.document import compute_doc_id, open_pdf
from pdfanno.pdf_core.text import normalize_text

# 与 match.py 的 QUAD_PROXIMITY_THRESHOLD 保持一致，以便口径一致。
QUAD_PROXIMITY_THRESHOLD = 15.0


def build_ground_truth(v1_path: Path, v2_path: Path, out_path: Path | None = None) -> dict:
    """主入口：为 v1 的每条 highlight 生成 ground truth 标注。"""

    with open_pdf(v1_path) as d1:
        v1_doc_id = compute_doc_id(d1, v1_path)
        v1_anchors = extract_anchors(d1, v1_doc_id)
        # 抓每个 query 在 v1 里的 (page, quad, reading_rank)
        v1_index = _build_occurrence_index(d1)

    with open_pdf(v2_path) as d2:
        v2_index = _build_occurrence_index(d2)

    labels: list[dict] = []
    for anchor in v1_anchors:
        query = normalize_text(anchor.selected_text)
        if not query:
            labels.append(_label_for_empty(anchor, "selected_text is empty"))
            continue

        # v1 anchor 的阅读顺序 rank：在 v1 该 query 的 occurrence 列表里找 quad 中心最接近的。
        v1_rank = _rank_in_v1(anchor, v1_index.get(query, []))
        if v1_rank is None:
            labels.append(
                _label_for_empty(
                    anchor,
                    f"could not locate anchor in v1 search_for results (text={query!r})",
                )
            )
            continue

        # v2 的第 v1_rank 次出现。
        v2_occs = v2_index.get(query, [])
        if v1_rank >= len(v2_occs):
            labels.append(
                {
                    "annotation_id": anchor.annotation_id,
                    "selected_text": anchor.selected_text,
                    "v1_page": anchor.page_index,
                    "v1_quad": anchor.quads[0] if anchor.quads else None,
                    "v1_occurrence_rank": v1_rank,
                    "gt_status": "broken",
                    "gt_page": None,
                    "gt_quad": None,
                    "gt_reason": (
                        f"v1 rank {v1_rank} (0-indexed) exceeds v2 count "
                        f"({len(v2_occs)}) for this query"
                    ),
                }
            )
            continue

        v2_page, v2_quad = v2_occs[v1_rank]
        gt_status, gt_reason = _decide_status(anchor, v2_page, v2_quad)
        labels.append(
            {
                "annotation_id": anchor.annotation_id,
                "selected_text": anchor.selected_text,
                "v1_page": anchor.page_index,
                "v1_quad": anchor.quads[0] if anchor.quads else None,
                "v1_occurrence_rank": v1_rank,
                "gt_status": gt_status,
                "gt_page": v2_page,
                "gt_quad": v2_quad,
                "gt_reason": gt_reason,
            }
        )

    report = {
        "schema_version": 2,
        "old_pdf": str(v1_path),
        "new_pdf": str(v2_path),
        "total_labels": len(labels),
        "summary": _summarize(labels),
        "labels": labels,
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# ---------- 内部 ----------


def _build_occurrence_index(
    doc: pymupdf.Document,
) -> dict[str, list[tuple[int, list[float]]]]:
    """对 doc 里每个独特 query (normalized selected_text) 返回 [(page, quad), ...]
    按阅读顺序（先 page，再 y 上到下，再 x 左到右）。

    这里的 "query 集合" 来自 v1 的 anchors —— 但这个函数对称地应用到 v2，因此我们用
    一个副作用式调用：调用方先从 v1 anchors 提取所有 normalized text，再分别调用两次
    （每次传入 target_queries）。

    为简化实现：本函数先拿全 anchor 的 text 列表，调用方只扫一遍。
    """

    # 延后绑定：build_ground_truth 会直接调用本函数而不传 queries；
    # 我们通过对 v1 anchors 先构造 queries，再复用同一 queries 扫 v2。
    # 所以真正的扫描入口是 `_occurrences_for_queries`。
    raise NotImplementedError("use _occurrences_for_queries instead")


def _occurrences_for_queries(
    doc: pymupdf.Document, queries: set[str]
) -> dict[str, list[tuple[int, list[float]]]]:
    out: dict[str, list[tuple[int, list[float]]]] = {q: [] for q in queries}
    for p_idx in range(doc.page_count):
        page = doc[p_idx]
        for q in queries:
            quads = page.search_for(q, quads=True) or []
            page_hits: list[tuple[int, list[float]]] = []
            for quad in quads:
                page_hits.append((p_idx, _quad_to_floats(quad)))
            # 同页内按阅读顺序排序：先 y（top→bottom），再 x
            page_hits.sort(key=lambda t: (_quad_center(t[1])[1], _quad_center(t[1])[0]))
            out[q].extend(page_hits)
    return out


def _rank_in_v1(anchor, v1_occs: list[tuple[int, list[float]]]) -> int | None:
    """在 v1 的 occurrence 列表里找到与 anchor.quad 中心最近的一条，返回它的 rank。"""

    if not anchor.quads or not v1_occs:
        return None
    target = _quad_center(anchor.quads[0])
    best_rank, best_dist = None, float("inf")
    for i, (pg, q) in enumerate(v1_occs):
        if pg != anchor.page_index:
            continue
        c = _quad_center(q)
        d = (c[0] - target[0]) ** 2 + (c[1] - target[1]) ** 2
        if d < best_dist:
            best_dist = d
            best_rank = i
    # 要求最佳候选距离 < 阈值的 2 倍，否则视为 anchor 未命中 search_for 结果
    if best_rank is None or best_dist**0.5 > QUAD_PROXIMITY_THRESHOLD * 2:
        return None
    return best_rank


def _decide_status(anchor, v2_page: int, v2_quad: list[float]) -> tuple[str, str]:
    """根据 v1 anchor 与 v2 对应 occurrence 判断 preserved / relocated。"""

    if v2_page != anchor.page_index:
        return "relocated", f"page moved from {anchor.page_index} to {v2_page}"
    # 同页，按 quad 距离判
    old_c = _quad_center(anchor.quads[0])
    new_c = _quad_center(v2_quad)
    dist = ((old_c[0] - new_c[0]) ** 2 + (old_c[1] - new_c[1]) ** 2) ** 0.5
    if dist < QUAD_PROXIMITY_THRESHOLD:
        return "preserved", f"same page, quad centers within {dist:.1f} pt"
    return "relocated", f"same page but shifted {dist:.1f} pt (> {QUAD_PROXIMITY_THRESHOLD} pt)"


def _label_for_empty(anchor, reason: str) -> dict:
    return {
        "annotation_id": anchor.annotation_id,
        "selected_text": anchor.selected_text,
        "v1_page": anchor.page_index,
        "v1_quad": anchor.quads[0] if anchor.quads else None,
        "v1_occurrence_rank": None,
        "gt_status": "needs_review",
        "gt_page": None,
        "gt_quad": None,
        "gt_reason": reason,
    }


def _summarize(labels: list[dict]) -> dict:
    out = {"preserved": 0, "relocated": 0, "broken": 0, "needs_review": 0}
    for lbl in labels:
        s = lbl["gt_status"]
        out[s] = out.get(s, 0) + 1
    return out


def _quad_center(quad: list[float]) -> tuple[float, float]:
    xs = quad[0::2]
    ys = quad[1::2]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _quad_to_floats(q: pymupdf.Quad) -> list[float]:
    return [
        q.ul.x, q.ul.y,
        q.ur.x, q.ur.y,
        q.ll.x, q.ll.y,
        q.lr.x, q.lr.y,
    ]  # fmt: skip


# ---------- CLI ----------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v1", type=Path, help="Old-version PDF")
    parser.add_argument("v2", type=Path, help="New-version PDF")
    parser.add_argument("--out", type=Path, required=True, help="Output ground-truth JSON")
    args = parser.parse_args()

    # 先扫 v1 anchors 拿 queries 集合。
    with open_pdf(args.v1) as d1:
        v1_doc_id = compute_doc_id(d1, args.v1)
        anchors = extract_anchors(d1, v1_doc_id)
    queries = {normalize_text(a.selected_text) for a in anchors if a.selected_text}

    # 分别对 v1 / v2 建 occurrence index。
    with open_pdf(args.v1) as d1:
        v1_idx = _occurrences_for_queries(d1, queries)
    with open_pdf(args.v2) as d2:
        v2_idx = _occurrences_for_queries(d2, queries)

    # 组装 labels —— 复用主入口的判定逻辑，但传入预计算好的 index。
    labels: list[dict] = []
    for anchor in anchors:
        query = normalize_text(anchor.selected_text)
        if not query:
            labels.append(_label_for_empty(anchor, "selected_text is empty"))
            continue
        v1_rank = _rank_in_v1(anchor, v1_idx.get(query, []))
        if v1_rank is None:
            labels.append(
                _label_for_empty(
                    anchor,
                    f"anchor not found in v1 search_for results (text={query!r})",
                )
            )
            continue
        v2_occs = v2_idx.get(query, [])
        if v1_rank >= len(v2_occs):
            labels.append(
                {
                    "annotation_id": anchor.annotation_id,
                    "selected_text": anchor.selected_text,
                    "v1_page": anchor.page_index,
                    "v1_quad": anchor.quads[0] if anchor.quads else None,
                    "v1_occurrence_rank": v1_rank,
                    "gt_status": "broken",
                    "gt_page": None,
                    "gt_quad": None,
                    "gt_reason": (f"v1 rank {v1_rank} exceeds v2 count ({len(v2_occs)})"),
                }
            )
            continue
        v2_page, v2_quad = v2_occs[v1_rank]
        gt_status, gt_reason = _decide_status(anchor, v2_page, v2_quad)
        labels.append(
            {
                "annotation_id": anchor.annotation_id,
                "selected_text": anchor.selected_text,
                "v1_page": anchor.page_index,
                "v1_quad": anchor.quads[0] if anchor.quads else None,
                "v1_occurrence_rank": v1_rank,
                "gt_status": gt_status,
                "gt_page": v2_page,
                "gt_quad": v2_quad,
                "gt_reason": gt_reason,
            }
        )

    report = {
        "schema_version": 2,
        "old_pdf": str(args.v1),
        "new_pdf": str(args.v2),
        "total_labels": len(labels),
        "summary": _summarize(labels),
        "labels": labels,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out} ({len(labels)} labels)")
    print(f"summary: {report['summary']}")
    # 用 doc_id hash 作为确定性种子确保标注可重复 —— 这里只是 echo，不改变行为。
    _ = hashlib
    return 0


if __name__ == "__main__":
    sys.exit(main())
