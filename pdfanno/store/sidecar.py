"""SQLite-backed sidecar 存储 —— plan.md §9 保守同步策略。

职责：
- draft 注释：不触碰 PDF，记录到 sidecar。
- 状态跟踪：draft / written / external-modified。
- doc 绑定：doc_id → last_known_path，支持 rebind。
- import：把 PDF 内既有注释复制为 sidecar 记录（只读）。
- 冲突处理：v1 保守 —— 外部修改仅报告，不自动 merge。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pdfanno.models import AnnotationRecord

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).parent / "schema.sql"

STATE_DRAFT = "draft"
STATE_WRITTEN = "written"
STATE_EXTERNAL_MODIFIED = "external-modified"

SOURCE_SIDECAR = "sidecar"
SOURCE_PDF = "pdf"
SOURCE_PLAN = "plan"


def default_sidecar_path() -> Path:
    """sidecar DB 默认位置。`PDFANNO_SIDECAR_PATH` 覆盖。"""

    env = os.environ.get("PDFANNO_SIDECAR_PATH")
    if env:
        return Path(os.path.expanduser(env))
    return Path(os.path.expanduser("~/.pdfanno/sidecar.sqlite"))


class Sidecar:
    """sidecar 连接的最小封装。实例不线程安全 —— CLI 每次调用构造新实例即可。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_sidecar_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._bootstrap()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Sidecar:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---------- schema ----------

    def _bootstrap(self) -> None:
        ddl = SCHEMA_FILE.read_text(encoding="utf-8")
        self._conn.executescript(ddl)
        self._conn.commit()

    # ---------- doc_bindings ----------

    def touch_doc(self, doc_id: str, path: str | os.PathLike) -> None:
        """记录 / 更新 doc_id → path 的绑定。"""

        now = _utc_now()
        self._conn.execute(
            """
            INSERT INTO doc_bindings (doc_id, last_known_path, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                last_known_path = excluded.last_known_path,
                last_seen_at = excluded.last_seen_at
            """,
            (doc_id, str(path), now, now),
        )
        self._conn.commit()

    def get_binding(self, doc_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT doc_id, last_known_path, first_seen_at, last_seen_at FROM doc_bindings WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None

    def rebind(self, old_doc_id: str, new_doc_id: str, new_path: str | os.PathLike) -> int:
        """把 old_doc_id 下所有条目与绑定迁到 new_doc_id。返回被迁移 entry 数。"""

        if old_doc_id == new_doc_id:
            self.touch_doc(new_doc_id, new_path)
            return 0

        count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM entries WHERE doc_id = ?", (old_doc_id,)
        ).fetchone()["c"]

        self._conn.execute(
            "UPDATE entries SET doc_id = ?, modified_at = ? WHERE doc_id = ?",
            (new_doc_id, _utc_now(), old_doc_id),
        )
        self._conn.execute("DELETE FROM doc_bindings WHERE doc_id = ?", (old_doc_id,))
        self.touch_doc(new_doc_id, new_path)
        self._conn.commit()
        return int(count)

    # ---------- entries ----------

    def upsert_entry(self, rec: AnnotationRecord, *, state: str) -> None:
        """新增或更新一条记录 —— 幂等依据 (doc_id, annotation_id)。"""

        existing = self._conn.execute(
            "SELECT id FROM entries WHERE doc_id = ? AND annotation_id = ?",
            (rec.doc_id, rec.annotation_id),
        ).fetchone()
        now = _utc_now()
        payload = (
            rec.id or str(uuid.uuid4()),
            rec.annotation_id,
            rec.doc_id,
            rec.page,
            rec.kind,
            json.dumps(rec.quads),
            json.dumps(rec.color),
            rec.contents,
            rec.matched_text,
            rec.rule_hash,
            rec.query,
            rec.source,
            rec.pdf_xref,
            state,
            rec.created_at.isoformat() if rec.created_at else now,
            now,
        )
        if existing:
            self._conn.execute(
                """
                UPDATE entries SET
                    page = ?, kind = ?, quads = ?, color = ?, contents = ?,
                    matched_text = ?, rule_hash = ?, query = ?, source = ?,
                    pdf_xref = ?, state = ?, modified_at = ?
                WHERE doc_id = ? AND annotation_id = ?
                """,
                (
                    rec.page,
                    rec.kind,
                    json.dumps(rec.quads),
                    json.dumps(rec.color),
                    rec.contents,
                    rec.matched_text,
                    rec.rule_hash,
                    rec.query,
                    rec.source,
                    rec.pdf_xref,
                    state,
                    now,
                    rec.doc_id,
                    rec.annotation_id,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO entries
                (id, annotation_id, doc_id, page, kind, quads, color, contents,
                 matched_text, rule_hash, query, source, pdf_xref, state,
                 created_at, modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        self._conn.commit()

    def mark_written(self, doc_id: str, annotation_id: str, pdf_xref: int) -> None:
        self._conn.execute(
            """
            UPDATE entries SET state = ?, pdf_xref = ?, modified_at = ?
            WHERE doc_id = ? AND annotation_id = ?
            """,
            (STATE_WRITTEN, pdf_xref, _utc_now(), doc_id, annotation_id),
        )
        self._conn.commit()

    def list_entries(self, doc_id: str, *, state: str | None = None) -> list[dict]:
        if state is None:
            rows = self._conn.execute(
                "SELECT * FROM entries WHERE doc_id = ? ORDER BY page, created_at",
                (doc_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM entries WHERE doc_id = ? AND state = ? ORDER BY page, created_at",
                (doc_id, state),
            ).fetchall()
        return [_deserialize_entry(dict(r)) for r in rows]

    def existing_annotation_ids(self, doc_id: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT annotation_id FROM entries WHERE doc_id = ?", (doc_id,)
        ).fetchall()
        return {r["annotation_id"] for r in rows}


def _deserialize_entry(row: dict) -> dict:
    row["quads"] = json.loads(row["quads"])
    row["color"] = json.loads(row["color"])
    return row


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
