"""pdfanno 的 pydantic 数据契约。

所有 agent 可消费的结构都在这里定义。字段一旦纳入 schema_version 1，必须向后兼容；
新增字段走 `extra="allow"` 不破坏老消费者，破坏性变更需要 schema_version 升级。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# JSON 输出的当前 schema 版本。plan.md §8.3。
SCHEMA_VERSION = 1

# 允许额外字段 —— 跨阶段演进时避免老消费者炸。
_FORWARD_COMPAT = ConfigDict(extra="allow", frozen=False)


# ---------- 规则与 plan ----------


class Rule(BaseModel):
    """匹配规则 —— plan.md §8.3。"""

    model_config = _FORWARD_COMPAT

    rule_id: str
    kind: Literal["highlight", "note", "underline", "strikeout", "squiggly"] = "highlight"
    query: str
    mode: Literal["literal", "ignore-case"] = "literal"
    color: list[float] = Field(default_factory=lambda: [1.0, 1.0, 0.0])
    page_range: str | None = None


class PlannedAnnotation(BaseModel):
    """AnnotationPlan 中的单条预计注释 —— plan.md §8.3。

    dry-run 和真实执行共用此结构，避免预览与执行路径分叉。
    """

    model_config = _FORWARD_COMPAT

    annotation_id: str
    rule_id: str
    kind: str
    page: int
    matched_text: str
    quads: list[list[float]]
    color: list[float]
    contents: str = ""
    source: Literal["plan", "sidecar", "pdf"] = "plan"


class AnnotationPlan(BaseModel):
    """dry-run 和 apply 共用的注释 plan —— plan.md §8.3。"""

    model_config = _FORWARD_COMPAT

    schema_version: int = SCHEMA_VERSION
    doc_id: str
    rules: list[Rule]
    annotations: list[PlannedAnnotation] = Field(default_factory=list)


# ---------- sidecar / 内部记录 ----------


class AnnotationRecord(BaseModel):
    """sidecar 与内部流转的注释记录 —— plan.md §8.2。

    `id` 是 sidecar 行本地 uuid，`annotation_id` 是跨实例稳定 sha256。
    `contents` 不参与 `annotation_id` hash（plan.md §6.3），可自由修改。
    """

    model_config = _FORWARD_COMPAT

    id: str
    annotation_id: str
    doc_id: str
    page: int
    kind: str
    quads: list[list[float]]
    color: list[float]
    contents: str = ""
    matched_text: str = ""
    rule_hash: str = ""
    query: str = ""
    source: Literal["plan", "sidecar", "pdf"] = "sidecar"
    pdf_xref: int | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None


# ---------- CLI 输出契约 ----------


class CliResult(BaseModel):
    """所有核心命令的 JSON 输出基线 —— plan.md §6.1。

    `input` / `output` / `command` 必填；`matches` / `annotations_*` 仅 highlight/apply 使用，
    其他命令填 0；`warnings` 始终是数组，即便为空。
    """

    model_config = _FORWARD_COMPAT

    schema_version: int = SCHEMA_VERSION
    command: str
    input: str
    output: str | None = None
    dry_run: bool = False
    matches: int = 0
    annotations_planned: int = 0
    annotations_created: int = 0
    warnings: list[str] = Field(default_factory=list)
    # 子命令可在下面附加结构化 payload，消费者通过 command 字段分流。
    data: dict | None = None
