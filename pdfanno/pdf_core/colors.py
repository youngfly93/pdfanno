"""命名色板 —— plan.md §11。

v1 固定 7 种命名色，RGB 值与 Zotero/Acrobat/Preview 的常见高亮色习惯对齐。
RGB float 输入作为高级 fallback，保留给脚本和 agent。
"""

from __future__ import annotations

# 色值选取原则：明度够高保证半透明高亮可读，色相分散避免混淆。
NAMED_COLORS: dict[str, tuple[float, float, float]] = {
    "yellow": (1.00, 1.00, 0.00),
    "green": (0.57, 0.89, 0.56),
    "blue": (0.55, 0.80, 1.00),
    "pink": (1.00, 0.72, 0.80),
    "orange": (1.00, 0.65, 0.00),
    "red": (1.00, 0.45, 0.45),
    "purple": (0.78, 0.60, 1.00),
}

DEFAULT_COLOR_NAME = "yellow"


def parse_color(value: str | None) -> list[float]:
    """接受命名色（yellow/green/...）或 "r,g,b" float 三元组。

    返回 [r,g,b] 的 list，分量归一化到 [0,1]。非法输入抛 ValueError。
    """

    if value is None or not value.strip():
        return list(NAMED_COLORS[DEFAULT_COLOR_NAME])

    key = value.strip().lower()
    if key in NAMED_COLORS:
        return list(NAMED_COLORS[key])

    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 3:
        raise ValueError(
            f"color 必须是命名色 {sorted(NAMED_COLORS)} 或 'r,g,b' 三元组，收到 {value!r}"
        )
    try:
        rgb = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"color RGB 分量必须是 float，收到 {value!r}") from exc
    for c in rgb:
        if not 0.0 <= c <= 1.0:
            raise ValueError(f"color 分量必须在 [0,1]，收到 {value!r}")
    return rgb
