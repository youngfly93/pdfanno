"""Kuhn-Munkres（Hungarian）线性分配 —— 纯 Python，用于 `_assign_one_to_one`。

为什么不用 scipy：
- KINGSTON 盘空间紧张，引入 scipy+numpy 会占 ~100 MB，不值得。
- 我们的矩阵非常小（n ≤ 50, m ≤ 200），纯 Python O(n²·m) 毫秒级完成。

实现参考 Jonker-Volgenant 风格的 dense 版本（CP-Algorithms 描述），扩展到矩形矩阵。
接口：输入 cost matrix（最小化），输出 row→col 匹配字典。
本模块只处理分配算法；语义（score→cost、dedup key、过滤阈值）都在 match.py 里。
"""

from __future__ import annotations

from collections.abc import Sequence

INF = float("inf")


def assign_min_cost(
    cost: Sequence[Sequence[float]], *, forbidden_marker: float = INF
) -> dict[int, int]:
    """对给定 cost 矩阵求最小总成本的一对一分配。

    `cost[i][j]` 表示把 row i 分给 col j 的代价。`forbidden_marker`（默认 +inf）
    表示不允许分配（算法不会选这些 cell）。返回 `{row_idx: col_idx}` 字典，仅
    包含实际被分配的 (row, col)；`cost` 值等于 `forbidden_marker` 的分配会被剔除。

    矩阵可以是矩形（n ≠ m）。内部通过 pad 到 size=max(n,m) 再跑算法，最后过滤 pad。

    算法：O((max(n,m))^3)，纯 Python，适合 n ≤ ~100。更大请换 scipy。
    """

    n_rows = len(cost)
    if n_rows == 0:
        return {}
    n_cols = len(cost[0])
    for row in cost:
        if len(row) != n_cols:
            raise ValueError("cost matrix rows must have equal length")

    # Pad 到方阵；pad 的 cell 用一个比 forbidden 还大的 marker 避免被选
    # —— 但 forbidden 本身就是 INF，pad 也用 INF。
    size = max(n_rows, n_cols)
    padded = [[INF] * size for _ in range(size)]
    for i in range(n_rows):
        for j in range(n_cols):
            padded[i][j] = cost[i][j]

    # 所有值非负、INF 保持不变
    row_match = _kuhn_munkres(padded, size)

    # 过滤 pad row 和 pad col，以及 cost == forbidden_marker 的 cell
    result: dict[int, int] = {}
    for row, col in row_match.items():
        if row >= n_rows or col >= n_cols:
            continue
        c = cost[row][col]
        if c in (forbidden_marker, INF):
            continue
        result[row] = col
    return result


def assign_max_score(
    score_matrix: Sequence[Sequence[float]],
    *,
    forbidden_marker: float = -INF,
) -> dict[int, int]:
    """最大化总分的一对一分配。`score_matrix[i][j]` = -INF 表示禁止分配。

    内部通过取负转为最小化。返回同 `assign_min_cost`。
    """

    n = len(score_matrix)
    if n == 0:
        return {}
    cost = [[-s if s != forbidden_marker else INF for s in row] for row in score_matrix]
    return assign_min_cost(cost)


# ---------- 内部：标准 Kuhn-Munkres（方阵）----------


def _kuhn_munkres(cost: list[list[float]], n: int) -> dict[int, int]:
    """标准 Jonker-Volgenant 实现（方阵版），返回 {row: col} 字典。

    使用 1-indexed 数组以贴近经典伪代码（p[0] 当作哨兵）。
    """

    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)  # p[j] = row assigned to col j（1-indexed）
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if delta == INF:
                # 无可增广路（全 INF 的行）；分配失败，标记为未分配并跳出
                break
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        # 回溯 augmenting path
        while j0 != 0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    result: dict[int, int] = {}
    for j in range(1, n + 1):
        if p[j] != 0:
            result[p[j] - 1] = j - 1  # 转回 0-indexed
    return result
