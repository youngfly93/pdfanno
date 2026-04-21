"""diff/migrate 数据契约 —— 与 PRD §7.1 / §7.2 / §5.1 对齐。

Week 1 PoC 仅实装三档状态（preserved / relocated / broken）。PRD 另定义
`changed` / `ambiguous` / `unsupported`，由 Week 2+ 扩展。`status` 当前用
`Literal[...]` 联合类型声明（见下方 `DiffStatus`）；新增状态时在联合里追加，
pydantic `extra="allow"` 保证老 parser 能忽略未知字段但不会静默吞未知 status。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# PRD §7.2 sidecar schema_version 从 "0.2" 起步；这里 diff/migrate 的 JSON 契约
# 用整数 schema 版本，从 2 开始（v0.1 模型是 schema_version=1）。
DIFF_SCHEMA_VERSION = 2

_FORWARD_COMPAT = ConfigDict(extra="allow")


class Anchor(BaseModel):
    """旧版本注释的多层锚点。PRD §5.1。"""

    model_config = _FORWARD_COMPAT

    annotation_id: str
    doc_id: str
    kind: str
    page_index: int
    quads: list[list[float]] = Field(default_factory=list)
    selected_text: str
    context_before: str = ""
    context_after: str = ""
    text_hash: str = ""
    context_hash: str = ""
    color: list[float] | None = None
    note: str = ""


class NewAnchor(BaseModel):
    """新 PDF 中命中位置的视图。broken 时字段为空。"""

    model_config = _FORWARD_COMPAT

    page_index: int | None = None
    quads: list[list[float]] = Field(default_factory=list)
    matched_text: str = ""


class MatchReason(BaseModel):
    """匹配得分分解。Week 1 只填 selected_text_similarity + page_delta + candidate_rank，
    其余字段留给 Week 2+ 填充（PRD §8.3 五项打分）。"""

    model_config = _FORWARD_COMPAT

    selected_text_similarity: float = 0.0
    context_similarity: float = 0.0
    page_delta: int = 0
    layout_score: float = 0.0
    candidate_rank: int = 0


DiffStatus = Literal[
    "preserved",
    "relocated",
    "changed",
    "ambiguous",
    "broken",
    "unsupported",
]


class DiffResult(BaseModel):
    """单条旧注释在新 PDF 中的 diff 记录。PRD §7.1。"""

    model_config = _FORWARD_COMPAT

    annotation_id: str
    status: DiffStatus
    confidence: float
    old_anchor: Anchor
    new_anchor: NewAnchor | None = None
    match_reason: MatchReason | None = None
    review_required: bool = False
    message: str = ""


class DiffSummary(BaseModel):
    """各状态计数汇总。新增状态也会自动出现在这里（extra=allow）。"""

    model_config = _FORWARD_COMPAT

    total_annotations: int = 0
    preserved: int = 0
    relocated: int = 0
    changed: int = 0
    ambiguous: int = 0
    broken: int = 0
    unsupported: int = 0


class DiffReport(BaseModel):
    """`pdfanno diff` 的顶层输出。"""

    model_config = _FORWARD_COMPAT

    schema_version: int = DIFF_SCHEMA_VERSION
    old_doc_id: str
    new_doc_id: str
    summary: DiffSummary
    results: list[DiffResult] = Field(default_factory=list)
