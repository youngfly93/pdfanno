"""PDF section 检测 —— Week 3 C 结构化信号。

动机：short-token 跨页重复（arXiv 的 BLEU × 11, WMT 2014 × 7）下，text + context +
layout_rank 三合一仍会把 anchor 错配到 "同文本但不同 section" 的候选。Section 标签
是强判别信号：同 section 才真正是同一段话。

策略：
1. 优先用 `doc.get_toc()` —— PDF 自带 bookmark 树，最可靠（arXiv / 绝大多数学术 PDF 都有）。
2. 没有 TOC 时用 font-size 启发：字号显著大于正文 body 且文本短、不以 "." 结尾、
   符合 "N[.M]*? Title" 等模式的行视为 heading。
3. 两者都产出同一个 `SectionSpan` 列表，按 (page, y_top) 排序。

API:
- `build_section_index(doc) -> list[SectionSpan]`
- `section_for(index, page_idx, y_center) -> SectionSpan | None`
- `SectionSpan.path` —— 形如 `"3 / 3.2 / 3.2.1 Scaled Dot-Product Attention"`，
  用于 anchor 与 candidate 的相等比较（完整路径比单 title 更精确 —— 两个 "Introduction"
  在不同章节下时不会误匹配）。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

import pymupdf

# body 字号与 heading 的差值下限（启发式）
HEADING_FONT_EXTRA = 1.5
# 启发式下的 heading 最长字符数（避免把段首长行误判）
HEADING_MAX_CHARS = 120
# 标题编号的常见模式：`1`, `1.2`, `1.2.3`, `3.2.1`
_NUM_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+)$")
# 非编号 heading：短文本、首字母大写、不以句号结尾
_KEYWORD_HEADINGS = {
    "abstract",
    "introduction",
    "background",
    "methods",
    "method",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgments",
    "acknowledgements",
    "appendix",
}


@dataclass(frozen=True)
class SectionSpan:
    """section 的起点。section 覆盖从自身 (page, y_top) 到下一个 SectionSpan 起点之前的所有内容。"""

    page_index: int
    y_top: float
    title: str
    level: int  # 1 = top-level，2/3 = subsection
    # 完整路径：祖先 title 用 ` / ` 连接，例如 "3 Model Architecture / 3.2 Attention / 3.2.1 Scaled Dot-Product Attention"
    path: str = ""


@dataclass
class _HeadingCandidate:
    page_index: int
    y_top: float
    text: str
    size: float


def build_section_index(doc: pymupdf.Document) -> list[SectionSpan]:
    """返回按 (page, y_top) 排序的 section 列表。无可识别 section 则返回空列表。"""

    toc_sections = _from_toc(doc)
    if toc_sections:
        return _build_paths(toc_sections)

    heuristic_sections = _from_font_heuristic(doc)
    if heuristic_sections:
        return _build_paths(heuristic_sections)

    return []


def section_for(index: list[SectionSpan], page_index: int, y_center: float) -> SectionSpan | None:
    """在排好序的 section 列表里找包含 (page_index, y_center) 的 section。

    "包含" 定义：最后一个满足 (section.page, section.y_top) ≤ (page_index, y_center) 的 section。
    空 index 返回 None。y=-inf 或第一个 section 之前的位置也返回 None。
    """

    if not index:
        return None
    best: SectionSpan | None = None
    for sec in index:
        if (sec.page_index, sec.y_top) <= (page_index, y_center):
            best = sec
        else:
            break
    return best


# ---------- 内部 ----------


def _from_toc(doc: pymupdf.Document) -> list[SectionSpan]:
    """从 PDF 自带的 table of contents 构造 SectionSpan 列表。

    `doc.get_toc()` 返回 `[[level, title, page_1_indexed], ...]`。TOC 不给 y 坐标，
    全部用 y_top=0（意味着整页顶部开始）。
    """

    raw = doc.get_toc() or []
    out: list[SectionSpan] = []
    for entry in raw:
        if len(entry) < 3:
            continue
        level, title, page_1 = entry[0], entry[1], entry[2]
        if not isinstance(page_1, int) or page_1 < 1:
            continue
        out.append(
            SectionSpan(
                page_index=page_1 - 1,
                y_top=0.0,
                title=str(title).strip(),
                level=int(level),
            )
        )
    out.sort(key=lambda s: (s.page_index, s.y_top))
    return out


def _from_font_heuristic(doc: pymupdf.Document) -> list[SectionSpan]:
    """无 TOC 时的回退：在 body 字号之上找 heading 候选。"""

    candidates: list[_HeadingCandidate] = []
    sizes: list[float] = []
    for p_idx in range(doc.page_count):
        page = doc[p_idx]
        try:
            raw = page.get_text("dict")
        except Exception:
            continue
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = str(span.get("text", "")).strip()
                    size = float(span.get("size", 0.0))
                    if text:
                        sizes.append(size)
                    if not text or len(text) > HEADING_MAX_CHARS:
                        continue
                    y_top = float(span.get("bbox", [0, 0, 0, 0])[1])
                    candidates.append(
                        _HeadingCandidate(page_index=p_idx, y_top=y_top, text=text, size=size)
                    )
    if not sizes:
        return []

    body_size = statistics.median(sizes)
    threshold = body_size + HEADING_FONT_EXTRA

    out: list[SectionSpan] = []
    for c in candidates:
        if c.size < threshold:
            continue
        if not _looks_like_heading(c.text):
            continue
        # 编号越多（"3.2.1 …"）层级越深
        level = _heading_level(c.text)
        out.append(
            SectionSpan(
                page_index=c.page_index,
                y_top=c.y_top,
                title=_clean_title(c.text),
                level=level,
            )
        )
    # 同一 y 附近的多段合并（有些 PDF 把 "3.2" 和 "Attention" 拆成两个 span）
    out = _merge_nearby(out)
    out.sort(key=lambda s: (s.page_index, s.y_top))
    return out


def _looks_like_heading(text: str) -> bool:
    if _NUM_HEADING_RE.match(text):
        return True
    tlow = text.lower().rstrip(":").strip()
    if tlow in _KEYWORD_HEADINGS:
        return True
    # 短、非句子、首字母大写
    if len(text) <= 60 and not text.endswith(".") and text[:1].isupper():
        # 至少含一个空格 —— 避免把单 word / page number 误判
        return " " in text
    return False


def _heading_level(text: str) -> int:
    m = _NUM_HEADING_RE.match(text)
    if m:
        return m.group(1).count(".") + 1
    return 1


def _clean_title(text: str) -> str:
    """去掉末尾冒号、多余空白。"""

    return text.strip().rstrip(":").strip()


def _merge_nearby(sections: list[SectionSpan]) -> list[SectionSpan]:
    """有些 PDF 把 heading 拆多 span；同页 y 差 < 3 pt 的相邻 heading 合并。"""

    if not sections:
        return sections
    sections.sort(key=lambda s: (s.page_index, s.y_top))
    out: list[SectionSpan] = [sections[0]]
    for sec in sections[1:]:
        last = out[-1]
        if sec.page_index == last.page_index and abs(sec.y_top - last.y_top) < 3.0:
            merged_title = f"{last.title} {sec.title}".strip()
            out[-1] = SectionSpan(
                page_index=last.page_index,
                y_top=min(last.y_top, sec.y_top),
                title=merged_title,
                level=min(last.level, sec.level),
            )
        else:
            out.append(sec)
    return out


def _build_paths(sections: list[SectionSpan]) -> list[SectionSpan]:
    """给每个 section 计算 `path` —— 祖先 title 链。遍历时维护 level → title 栈。"""

    ancestors: dict[int, str] = {}
    out: list[SectionSpan] = []
    for sec in sections:
        # 清理所有 >= 当前 level 的祖先
        for lvl in [lv for lv in ancestors if lv >= sec.level]:
            del ancestors[lvl]
        ancestors[sec.level] = sec.title
        path = " / ".join(ancestors[k] for k in sorted(ancestors))
        out.append(
            SectionSpan(
                page_index=sec.page_index,
                y_top=sec.y_top,
                title=sec.title,
                level=sec.level,
                path=path,
            )
        )
    return out
