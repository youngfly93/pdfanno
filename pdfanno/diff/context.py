"""共享的 context-similarity 计算 —— 供 matcher (`match._context_similarity`) 和
oracle (`benchmarks.tools.ground_truth_semantic`) 复用。

Week 10 为什么抽出来：之前 matcher 用 `(before_ratio + after_ratio)/2`（mean），
oracle 用 `SequenceMatcher(before||after, before||after).ratio()`（concat）。两种
算法给出的数值分布不同，导致 Week 9 的 broken floor 在 arXiv 上 fix/break ≈ 1:1
—— 不是机制错，是两个 ctx 指标不在同一尺度上。抽成共享 helper 后可以用环境变量
开关让 matcher 换到 concat，和 oracle 对齐后再评估 floor 的可行性。

`mode`：
- `"mean"`（默认）：保留 v0.2.1 的行为。before/after 各算一次 SequenceMatcher
  ratio，取均值；空侧跳过。对短 context 有平均化作用。
- `"concat"`：把 before 和 after 拼成单串（用 `CONTEXT_SEP` 分隔）再一次
  SequenceMatcher。与 oracle 的算法一致，数值更两极化（要么高要么低）。

切换方式（matcher 端）：

    PDFANNO_CTX_SIM_MODE=concat .venv/bin/pdfanno diff ...

oracle 端永远用 concat —— 那是 oracle 的定义，不通过环境变量改。
"""

from __future__ import annotations

from difflib import SequenceMatcher

CONTEXT_SEP = " || "
DEFAULT_MODE = "mean"
ALL_MODES = ("mean", "concat")


def context_similarity(
    before_a: str,
    after_a: str,
    before_b: str,
    after_b: str,
    *,
    mode: str = DEFAULT_MODE,
) -> float:
    """比较两个 (before, after) 上下文对的相似度。返回 [0, 1]。"""

    if mode == "concat":
        a = before_a + CONTEXT_SEP + after_a
        b = before_b + CONTEXT_SEP + after_b
        if not a.strip() or not b.strip():
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    # 默认 mean
    parts: list[float] = []
    if before_a:
        parts.append(SequenceMatcher(None, before_a, before_b).ratio())
    if after_a:
        parts.append(SequenceMatcher(None, after_a, after_b).ratio())
    return sum(parts) / len(parts) if parts else 0.0
