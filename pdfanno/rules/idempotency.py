"""稳定 annotation_id —— plan.md §6.3。

公式：
    annotation_id = sha256(
        doc_id + kind + page + normalized_quads + normalized_text + rule_hash
    )

`normalized_quads`：按 PDF point 坐标、PyMuPDF quad 点顺序、每个 float 四舍五入到 2 位小数。
`normalized_text`：`\\s+` → 单空格后两端 strip，只取命中原文（不含用户后加的 note）。

fixtures 要保证 highlight → save → reopen → re-highlight 的 roundtrip 下 annotation_id 不变。
"""

from __future__ import annotations

import hashlib
import json

from pdfanno.pdf_core.text import normalize_text

QUAD_PRECISION = 2


def normalize_quads(quads: list[list[float]]) -> list[list[float]]:
    """对 quads 做确定性归一化：每个 float 四舍五入到 2 位小数，保留原顺序。"""

    return [[round(v, QUAD_PRECISION) for v in quad] for quad in quads]


def compute_rule_hash(
    *, kind: str, query: str, mode: str, color: list[float], page_range: str | None = None
) -> str:
    """规则指纹。规则字段发生任何语义变化（包括颜色）都会改变 rule_hash。"""

    payload = {
        "kind": kind,
        "query": query,
        "mode": mode,
        "color": [round(c, 4) for c in color],
        "page_range": page_range,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def compute_annotation_id(
    *,
    doc_id: str,
    kind: str,
    page: int,
    quads: list[list[float]],
    matched_text: str,
    rule_hash: str,
) -> str:
    """按 §6.3 公式产出稳定 annotation_id。

    任何参与哈希的输入都要先归一化，否则库升级或保存-重开会让 id 漂移。
    """

    payload = {
        "doc_id": doc_id,
        "kind": kind,
        "page": page,
        "quads": normalize_quads(quads),
        "text": normalize_text(matched_text),
        "rule_hash": rule_hash,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
