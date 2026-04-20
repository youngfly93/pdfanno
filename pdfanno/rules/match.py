"""把 Rule 作用到一个已打开的 PDF，产出 AnnotationPlan —— plan.md §8.3。

Stage A 实现 literal/ignore-case 匹配；Stage B 补 page range。regex 留到 v1.5。
"""

from __future__ import annotations

import pymupdf

from pdfanno.models import AnnotationPlan, PlannedAnnotation, Rule
from pdfanno.pdf_core.text import search_page
from pdfanno.rules.idempotency import compute_annotation_id, compute_rule_hash


def plan_from_rules(doc: pymupdf.Document, doc_id: str, rules: list[Rule]) -> AnnotationPlan:
    """在 `doc` 上执行所有 `rules`，返回一个合并的 AnnotationPlan。"""

    planned: list[PlannedAnnotation] = []
    for rule in rules:
        planned.extend(_plan_single_rule(doc, doc_id, rule))
    return AnnotationPlan(doc_id=doc_id, rules=list(rules), annotations=planned)


def _plan_single_rule(doc: pymupdf.Document, doc_id: str, rule: Rule) -> list[PlannedAnnotation]:
    rule_hash = compute_rule_hash(
        kind=rule.kind,
        query=rule.query,
        mode=rule.mode,
        color=list(rule.color),
        page_range=rule.page_range,
    )

    page_filter = parse_page_range(rule.page_range, doc.page_count)

    out: list[PlannedAnnotation] = []
    for page_idx in range(doc.page_count):
        if page_filter is not None and page_idx not in page_filter:
            continue
        page = doc[page_idx]
        for match in search_page(page, rule.query, ignore_case=(rule.mode == "ignore-case")):
            annotation_id = compute_annotation_id(
                doc_id=doc_id,
                kind=rule.kind,
                page=match.page,
                quads=match.quads,
                matched_text=match.matched_text,
                rule_hash=rule_hash,
            )
            out.append(
                PlannedAnnotation(
                    annotation_id=annotation_id,
                    rule_id=rule.rule_id,
                    kind=rule.kind,
                    page=match.page,
                    matched_text=match.matched_text,
                    quads=match.quads,
                    color=list(rule.color),
                )
            )
    return out


def parse_page_range(spec: str | None, page_count: int) -> set[int] | None:
    """解析 1-indexed 的 "1-3,5,7-9" → 0-indexed 页号集合。None 表示不过滤。

    越界值截断，不抛异常；整个 spec 语法错才抛 ValueError。
    """

    if spec is None or not spec.strip():
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            try:
                lo_i = int(lo_s)
                hi_i = int(hi_s)
            except ValueError as exc:
                raise ValueError(f"bad page range segment: {part!r}") from exc
            if lo_i > hi_i:
                lo_i, hi_i = hi_i, lo_i
            lo_i = max(1, lo_i)
            hi_i = min(page_count, hi_i)
            pages.update(range(lo_i - 1, hi_i))
        else:
            try:
                p = int(part)
            except ValueError as exc:
                raise ValueError(f"bad page number: {part!r}") from exc
            if 1 <= p <= page_count:
                pages.add(p - 1)
    return pages


def plan_for_query(
    doc: pymupdf.Document,
    doc_id: str,
    *,
    query: str,
    kind: str = "highlight",
    mode: str = "literal",
    color: list[float] | None = None,
    page_range: str | None = None,
    rule_id: str = "rule-001",
) -> AnnotationPlan:
    """便捷入口：从单个 query 构造 Rule 并生成 plan。"""

    rule = Rule(
        rule_id=rule_id,
        kind=kind,
        query=query,
        mode=mode,
        color=color if color is not None else [1.0, 1.0, 0.0],
        page_range=page_range,
    )
    return plan_from_rules(doc, doc_id, [rule])
