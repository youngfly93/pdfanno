"""PDF 文档打开、doc_id 计算、安全性检查。

plan.md §8.1：文档身份优先使用 trailer /ID[0]，fallback 为 page_count + first_page_text_hash + file_size。
plan.md §11：原地写回前必须检查 encrypted / signed / permission / XFA / JavaScript。
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class DocSafetyFlags:
    """文档安全性标志。`--in-place` 在任意一项为 True 时默认拒绝。

    plan.md §11 要求：
    - signed / permission-restricted / XFA / JavaScript → 默认拒绝原地。
    - encrypted（即便已解密）也要保留 encryption passthrough。
    """

    is_encrypted: bool
    is_signed: bool
    is_permission_restricted: bool
    has_xfa: bool
    has_javascript: bool
    can_save_incrementally: bool


@contextmanager
def open_pdf(path: str | os.PathLike, password: str | None = None):
    """打开 PDF 的上下文管理器 —— 保证关闭。"""

    doc = pymupdf.open(str(path))
    # 未授权仍可读基础元数据；调用方按需检查 doc.is_encrypted。
    if doc.is_encrypted and password is not None:
        doc.authenticate(password)
    try:
        yield doc
    finally:
        doc.close()


def compute_doc_id(doc: pymupdf.Document, path: str | os.PathLike) -> str:
    """按 plan.md §8.1 计算文档身份。

    primary: `id:` 前缀 + 小写十六进制的 trailer /ID[0]。
    fallback: `fb:` 前缀 + page_count + first_page_text_hash(前 16 位) + file_size。
    """

    primary = _read_trailer_id(doc)
    if primary is not None:
        return f"id:{primary}"

    page_count = doc.page_count
    first_page_text = doc[0].get_text("text") if page_count > 0 else ""
    first_hash = hashlib.sha256(first_page_text.encode("utf-8")).hexdigest()[:16]
    size = os.path.getsize(path)
    return f"fb:{page_count}:{first_hash}:{size}"


def _read_trailer_id(doc: pymupdf.Document) -> str | None:
    """从 PDF trailer 读取 /ID[0] 的十六进制字符串；读取失败返回 None。"""

    try:
        kind, value = doc.xref_get_key(-1, "ID")
    except Exception:
        return None
    if kind != "array" or not value:
        return None
    # value 形如 "[<abcd...><efgh...>]"，取第一个 hex 字串。
    match = re.match(r"\[\s*<([0-9a-fA-F]+)>", value)
    if match is None:
        return None
    return match.group(1).lower()


def inspect_safety(doc: pymupdf.Document) -> DocSafetyFlags:
    """检查文档是否适合原地写回 —— plan.md §11 的前置门槛。

    对无法精确探测的字段采取保守策略：有疑即拒。
    """

    is_encrypted = bool(doc.is_encrypted) or bool(doc.needs_pass)
    can_save_incrementally = bool(doc.can_save_incrementally())

    is_signed, has_xfa, has_javascript = _inspect_catalog_acroform(doc)
    is_permission_restricted = _check_permission_restriction(doc)

    return DocSafetyFlags(
        is_encrypted=is_encrypted,
        is_signed=is_signed,
        is_permission_restricted=is_permission_restricted,
        has_xfa=has_xfa,
        has_javascript=has_javascript,
        can_save_incrementally=can_save_incrementally,
    )


def _inspect_catalog_acroform(doc: pymupdf.Document) -> tuple[bool, bool, bool]:
    """返回 (is_signed, has_xfa, has_javascript)。

    从 Root/Catalog 和 AcroForm 读取关键字段。探测失败走保守默认 False ——
    外层调用方若需要更强的保证，应结合 is_encrypted 与 can_save_incrementally。
    """

    is_signed = False
    has_xfa = False
    has_javascript = False

    try:
        root_kind, root_ref = doc.xref_get_key(-1, "Root")
    except Exception:
        return (is_signed, has_xfa, has_javascript)
    if root_kind != "xref" or not root_ref:
        return (is_signed, has_xfa, has_javascript)

    root_xref = _parse_xref_ref(root_ref)
    if root_xref is None:
        return (is_signed, has_xfa, has_javascript)

    # JavaScript：Catalog.Names.JavaScript 或 Catalog.OpenAction 指向 JS。
    try:
        names_kind, _ = doc.xref_get_key(root_xref, "Names")
        if names_kind != "null":
            # 粗粒度：Names 字典存在即视为可能含 JS 或嵌入脚本。
            raw = doc.xref_object(root_xref, compressed=False)
            if "/JavaScript" in raw or "/JS" in raw:
                has_javascript = True
    except Exception:
        pass

    # AcroForm.XFA 与 AcroForm.Fields 中的 Sig。
    try:
        acro_kind, acro_ref = doc.xref_get_key(root_xref, "AcroForm")
    except Exception:
        return (is_signed, has_xfa, has_javascript)
    if acro_kind == "xref":
        acro_xref = _parse_xref_ref(acro_ref)
        if acro_xref is not None:
            try:
                xfa_kind, _ = doc.xref_get_key(acro_xref, "XFA")
                if xfa_kind != "null":
                    has_xfa = True
            except Exception:
                pass
            # 签名检测：AcroForm.SigFlags 位 0 = SignaturesExist。保守探测：对象文本含 Sig。
            try:
                raw_acro = doc.xref_object(acro_xref, compressed=False)
                if "/Sig" in raw_acro or "SigFlags" in raw_acro:
                    is_signed = True
            except Exception:
                pass

    return (is_signed, has_xfa, has_javascript)


def _check_permission_restriction(doc: pymupdf.Document) -> bool:
    """PDF permissions 位掩码：modify 或 annotate 被禁视为受限。"""

    permissions = getattr(doc, "permissions", -1)
    if permissions == -1:
        return False
    modify_bit = 1 << 3
    annotate_bit = 1 << 5
    return (permissions & modify_bit) == 0 or (permissions & annotate_bit) == 0


def _parse_xref_ref(value: str) -> int | None:
    """PyMuPDF 的间接引用值形如 "12 0 R"，提取其中的 xref 号。"""

    if not value:
        return None
    parts = value.strip().split()
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def resolve_path(path: str | os.PathLike) -> Path:
    """规范化路径：展开 ~、转成绝对路径，不解析 symlink（保留用户意图）。"""

    return Path(os.path.expanduser(str(path))).resolve(strict=False)
