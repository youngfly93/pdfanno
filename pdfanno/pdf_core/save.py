"""保存策略 —— plan.md §11。

默认策略：
- `-o OUTPUT`：另存副本（推荐）。
- `--in-place`：显式原地写回，必须通过 `inspect_safety` 检查才能生效。
- `--sidecar`：Stage C 引入，不触碰 PDF。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pymupdf

from pdfanno.pdf_core.document import inspect_safety


class InPlaceSaveRefused(Exception):
    """`--in-place` 因安全检查不通过而拒绝。reasons 是一串可读拒因。"""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("in-place save refused: " + ", ".join(reasons))
        self.reasons = reasons


def save_to_new_file(
    doc: pymupdf.Document, output_path: str | os.PathLike, *, deflate: bool = True
) -> Path:
    """把 doc 另存到 output_path。保留原 encryption 设置。

    原始输入文件在此函数调用前不会被触碰 —— doc 内存中的修改只通过 save 刷写到新路径。
    """

    out = Path(os.path.expanduser(str(output_path)))
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(
        str(out),
        deflate=deflate,
        encryption=pymupdf.PDF_ENCRYPT_KEEP,
    )
    return out


def save_in_place(doc: pymupdf.Document, path: str | os.PathLike) -> Path:
    """原地增量写回。任何安全检查失败时抛 InPlaceSaveRefused。"""

    flags = inspect_safety(doc)
    reasons: list[str] = []
    if not flags.can_save_incrementally:
        reasons.append("cannot save incrementally")
    if flags.is_encrypted:
        reasons.append("encrypted")
    if flags.is_signed:
        reasons.append("contains digital signature")
    if flags.is_permission_restricted:
        reasons.append("permission-restricted")
    if flags.has_xfa:
        reasons.append("XFA form")
    if flags.has_javascript:
        reasons.append("embedded JavaScript")
    if reasons:
        raise InPlaceSaveRefused(reasons)

    target = Path(os.path.expanduser(str(path)))
    doc.save(
        str(target),
        incremental=True,
        encryption=pymupdf.PDF_ENCRYPT_KEEP,
    )
    return target


def copy_pdf(src: str | os.PathLike, dst: str | os.PathLike) -> Path:
    """物理拷贝一个 PDF 文件（bytes → bytes）。不打开 PDF，不重新序列化。"""

    dst_path = Path(os.path.expanduser(str(dst)))
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(src), str(dst_path))
    return dst_path
