"""Hungarian 分配的单元测试 —— 纯算法层，不依赖 PDF。"""

from __future__ import annotations

from pdfanno.diff._hungarian import assign_max_score, assign_min_cost

INF = float("inf")


def test_empty_matrix_returns_empty():
    assert assign_max_score([]) == {}
    assert assign_min_cost([]) == {}


def test_single_cell():
    assert assign_max_score([[0.9]]) == {0: 0}


def test_all_forbidden_returns_empty_row():
    assert assign_max_score([[-INF, -INF]]) == {}


def test_square_obvious_matching():
    scores = [
        [1.0, 0.1],
        [0.1, 1.0],
    ]
    assert assign_max_score(scores) == {0: 0, 1: 1}


def test_greedy_suboptimal_hungarian_optimal():
    """这是 Week 2 B 的核心动机场景：greedy 会错，Hungarian 要对。

    Anchor A: cand X=0.95, cand Y=0.90
    Anchor B: cand X=0.85, cand Y=0.30

    Greedy (按 score desc): A 抢 X (0.95) → B 只能拿 Y (0.30) → 总分 1.25
    Hungarian 最优:        A 拿 Y (0.90) + B 拿 X (0.85)     → 总分 1.75
    """

    scores = [
        [0.95, 0.90],  # anchor A
        [0.85, 0.30],  # anchor B
    ]
    result = assign_max_score(scores)
    total = sum(scores[r][c] for r, c in result.items())
    assert total > 1.25, f"expected Hungarian > 1.25, got {total}"
    assert result == {0: 1, 1: 0}


def test_rectangular_more_cols_than_rows():
    """m > n：行全部分配，多余列剩下。"""

    scores = [
        [0.9, 0.1, 0.5],
        [0.2, 0.8, 0.6],
    ]
    result = assign_max_score(scores)
    assert len(result) == 2
    assigned_cols = set(result.values())
    assert len(assigned_cols) == 2  # 两个不同的列
    total = sum(scores[r][c] for r, c in result.items())
    # 最优解: (0→0=0.9) + (1→1=0.8) = 1.7；允许 float 误差
    assert abs(total - 1.7) < 1e-9


def test_rectangular_more_rows_than_cols():
    """n > m：部分 row 分不到 col，结果字典中就不包含它们。"""

    scores = [
        [0.9, 0.1],
        [0.2, 0.8],
        [0.5, 0.5],  # 第三个 row 没有槽
    ]
    result = assign_max_score(scores)
    assert len(result) == 2
    assert set(result.values()) == {0, 1}


def test_forbidden_cells_are_excluded():
    """-INF cell 永远不会被选上。"""

    scores = [
        [-INF, 0.5],
        [0.6, -INF],
    ]
    result = assign_max_score(scores)
    assert result == {0: 1, 1: 0}


def test_deterministic():
    """相同输入应得相同输出（同分时的 tiebreaker 稳定）。"""

    scores = [
        [0.5, 0.5, 0.5],
        [0.5, 0.5, 0.5],
        [0.5, 0.5, 0.5],
    ]
    r1 = assign_max_score(scores)
    r2 = assign_max_score(scores)
    assert r1 == r2


def test_min_cost_equivalent():
    """assign_min_cost 与 assign_max_score 的负号转换应等价。"""

    scores = [
        [3.0, 1.0],
        [2.0, 4.0],
    ]
    max_result = assign_max_score(scores)
    # 等价的 min cost：取负
    cost = [[-s for s in row] for row in scores]
    min_result = assign_min_cost(cost)
    assert max_result == min_result
