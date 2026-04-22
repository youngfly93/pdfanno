"""Semantic-aware ground truth —— Week 8。

旧 `ground_truth.py` 用 `v1 rank k → v2 rank k` 的硬规则。Week 7 诊断出这个规则
在 v2 增删同 token 时会被 rank 位移误导（典型案例：v5 的 Figure 2 caption 引入
新 "Multi-Head Attention" occurrences，把 v1 rank 3 的 anchor 映射到了 Figure
caption 而不是其语义对应的段落）。

本 oracle 的规则：

    对 v1 的每条 anchor：
    1. 提取 anchor 的局部 ctx（±CONTEXT_CHARS，从 anchor 自身 quad 位置抽）
    2. 对 v2 里所有同 token 的 occurrences，各自抽局部 ctx
    3. 选 ctx_similarity（SequenceMatcher on before || after）最高者作为 gt
    4. 若所有候选的 ctx_sim 都 < MIN_CTX_SIM，标 gt_status="broken"

这是一个 **更诚实的 oracle** —— 不受 token 增删的位移干扰，但前提假设是
anchor 的局部 ctx 在 v2 里仍保留可识别的片段（大多数 revision 成立）。

输出 schema 与 `ground_truth.py` 兼容，evaluate.py 可直接消费。新增字段：
`gt_ctx_similarity`（best match 的 sim 值）+ `gt_method: "semantic"` 标识。

用法：
    python -m benchmarks.tools.ground_truth_semantic V1.pdf V2.pdf --out gt_semantic.json
"""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

from benchmarks.tools.ground_truth import (
    QUAD_PROXIMITY_THRESHOLD,
    _occurrences_for_queries,
    _quad_center,
    _rank_in_v1,
)
from pdfanno.diff.anchors import CONTEXT_CHARS, extract_anchors
from pdfanno.pdf_core.document import compute_doc_id, open_pdf
from pdfanno.pdf_core.text import normalize_text

# ctx_sim 低于此值的"最佳匹配"认为 token 的 semantic 定位在 v2 已丢失，标 broken。
# 0.15 比较宽松 —— 短 token 本身 ctx 信息有限（BLEU 周围可能是表格数字），太严会
# 把合法 relocation 误判成 broken。
MIN_CTX_SIM = 0.15


def build_ground_truth_semantic(v1_path: Path, v2_path: Path) -> dict:
    """对 (v1, v2) 生成 semantic-aware GT。"""

    with open_pdf(v1_path) as d1:
        v1_anchors = extract_anchors(d1, compute_doc_id(d1, v1_path))
    queries = {normalize_text(a.selected_text) for a in v1_anchors if a.selected_text}

    # v1 / v2 的 occurrence index（复用旧 oracle 的函数保持一致）。
    with open_pdf(v1_path) as d1:
        v1_idx = _occurrences_for_queries(d1, queries)
    with open_pdf(v2_path) as d2:
        v2_idx = _occurrences_for_queries(d2, queries)
        # 同时预抽 v2 每页的 normalized text，用于算局部 ctx。
        v2_page_text = [normalize_text(d2[i].get_text("text") or "") for i in range(d2.page_count)]

    labels: list[dict] = []
    pending: list[dict] = []  # anchor 要 global 1-to-1 分配的状态
    for anchor in v1_anchors:
        query = normalize_text(anchor.selected_text)
        if not query:
            labels.append(_label_empty(anchor, "selected_text is empty"))
            continue

        v1_rank = _rank_in_v1(anchor, v1_idx.get(query, []))
        if v1_rank is None:
            labels.append(
                _label_empty(anchor, f"anchor not found in v1 search_for (text={query!r})")
            )
            continue

        v2_occs = v2_idx.get(query, [])
        if not v2_occs:
            labels.append(_label_broken(anchor, v1_rank, "v2 has 0 occurrences of this token"))
            continue

        # 为每个 v2 occurrence 算局部 ctx。真正的 1-to-1 挑选在下面的 global 分配里做。
        anchor_ctx = anchor.context_before + " || " + anchor.context_after
        sims: list[float] = []
        for i, (v2_page, v2_quad) in enumerate(v2_occs):
            v2_ctx = _local_ctx(v2_page_text, v2_page, v2_quad, query, v2_occs, i)
            sims.append(SequenceMatcher(None, anchor_ctx, v2_ctx).ratio() if v2_ctx else 0.0)
        pending.append(
            {
                "anchor": anchor,
                "v1_rank": v1_rank,
                "query": query,
                "v2_occs": v2_occs,
                "sims": sims,
                "label_index": len(labels),
            }
        )
        labels.append(None)  # placeholder，下面 global 分配后填

    # 1-to-1 global 分配 —— 按 (sim desc, v1_rank asc) 排所有 (pending anchor, v2_occ)
    # 三元组，greedy 挑。每个 anchor 只取一次，每个 v2 occurrence 只被认领一次。
    claimed: set[tuple] = set()
    assigned: dict[str, tuple[int, list[float], float, int]] = {}
    triples: list[tuple[float, int, int]] = []
    for pi, entry in enumerate(pending):
        for oi, sim in enumerate(entry["sims"]):
            triples.append((sim, pi, oi))
    triples.sort(key=lambda t: (-t[0], t[1], t[2]))

    for sim, pi, oi in triples:
        if sim < MIN_CTX_SIM:
            break
        entry = pending[pi]
        aid = entry["anchor"].annotation_id
        if aid in assigned:
            continue
        v2_page, v2_quad = entry["v2_occs"][oi]
        # 用 (page, rounded quad center) 作为 v2 位置 key —— 即便同位置由 search_for
        # 返回了 2 个重叠的 quad（edge case），也只算一个 slot。
        v2_key = (
            v2_page,
            round((v2_quad[0] + v2_quad[6]) / 2, 1),
            round((v2_quad[1] + v2_quad[7]) / 2, 1),
        )
        if v2_key in claimed:
            continue
        assigned[aid] = (v2_page, v2_quad, sim, oi)
        claimed.add(v2_key)

    # 回填 labels 里的 placeholder
    for entry in pending:
        anchor = entry["anchor"]
        aid = anchor.annotation_id
        pick = assigned.get(aid)
        if pick is None:
            # 所有 sim 都 < MIN_CTX_SIM，或被其他 anchor 抢光。
            best_sim = max(entry["sims"]) if entry["sims"] else 0.0
            if best_sim < MIN_CTX_SIM:
                reason = (
                    f"best v2 ctx_sim {best_sim:.3f} < {MIN_CTX_SIM} "
                    f"(token likely removed semantically)"
                )
            else:
                reason = (
                    f"all higher-ctx v2 occurrences claimed by other anchors; "
                    f"own best ctx was {best_sim:.3f}"
                )
            labels[entry["label_index"]] = _label_broken(anchor, entry["v1_rank"], reason)
            continue

        v2_page, v2_quad, best_sim, v2_rank = pick
        gt_status, gt_reason = _decide_status(anchor, v2_page, v2_quad)
        labels[entry["label_index"]] = {
            "annotation_id": aid,
            "selected_text": anchor.selected_text,
            "v1_page": anchor.page_index,
            "v1_quad": anchor.quads[0] if anchor.quads else None,
            "v1_occurrence_rank": entry["v1_rank"],
            "gt_status": gt_status,
            "gt_page": v2_page,
            "gt_quad": v2_quad,
            "gt_reason": gt_reason,
            "gt_method": "semantic-1to1",
            "gt_ctx_similarity": round(best_sim, 4),
            "gt_v2_occurrence_rank": v2_rank,
        }

    return {
        "schema_version": 2,
        "oracle": "semantic-1to1",
        "old_pdf": str(v1_path),
        "new_pdf": str(v2_path),
        "total_labels": len(labels),
        "summary": _summarize(labels),
        "labels": labels,
    }


def _local_ctx(
    v2_page_text: list[str],
    page: int,
    quad: list[float],
    query: str,
    all_occs: list[tuple[int, list[float]]],
    this_idx: int,
) -> str:
    """取 v2 第 this_idx 次 occurrence 附近的 ctx（before || after 拼接）。"""

    if page < 0 or page >= len(v2_page_text):
        return ""
    page_text = v2_page_text[page]
    # 统计本 page 里 query 是第几次 occurrence（v2 全局 rank → 本页局部 rank）。
    local_k = sum(1 for i, (p, _q) in enumerate(all_occs[:this_idx]) if p == page)
    # 在 page_text 上找第 local_k 次出现。
    idx = -1
    start = 0
    for _ in range(local_k + 1):
        idx = page_text.find(query, start)
        if idx < 0:
            return ""
        start = idx + len(query)
    before = page_text[max(0, idx - CONTEXT_CHARS) : idx]
    after = page_text[idx + len(query) : idx + len(query) + CONTEXT_CHARS]
    return before + " || " + after


def _decide_status(anchor, v2_page: int, v2_quad: list[float]) -> tuple[str, str]:
    if v2_page != anchor.page_index:
        return "relocated", f"page moved from {anchor.page_index} to {v2_page}"
    old_c = _quad_center(anchor.quads[0])
    new_c = _quad_center(v2_quad)
    dist = ((old_c[0] - new_c[0]) ** 2 + (old_c[1] - new_c[1]) ** 2) ** 0.5
    if dist < QUAD_PROXIMITY_THRESHOLD:
        return "preserved", f"same page, quad centers within {dist:.1f} pt"
    return "relocated", f"same page but shifted {dist:.1f} pt (> {QUAD_PROXIMITY_THRESHOLD} pt)"


def _label_empty(anchor, reason: str) -> dict:
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
        "gt_method": "semantic",
    }


def _label_broken(anchor, v1_rank: int, reason: str) -> dict:
    return {
        "annotation_id": anchor.annotation_id,
        "selected_text": anchor.selected_text,
        "v1_page": anchor.page_index,
        "v1_quad": anchor.quads[0] if anchor.quads else None,
        "v1_occurrence_rank": v1_rank,
        "gt_status": "broken",
        "gt_page": None,
        "gt_quad": None,
        "gt_reason": reason,
        "gt_method": "semantic",
    }


def _summarize(labels: list[dict]) -> dict:
    out = {"preserved": 0, "relocated": 0, "broken": 0, "needs_review": 0}
    for lbl in labels:
        s = lbl["gt_status"]
        out[s] = out.get(s, 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("v1", type=Path)
    ap.add_argument("v2", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    report = build_ground_truth_semantic(args.v1, args.v2)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out} ({len(report['labels'])} labels)")
    print(f"summary: {report['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
