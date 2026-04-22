# Week 9: broken floor —— W2V 赢了，arXiv 没过门槛

## 机制

当 `_classify` 准备返回 `relocated`（包括 fuzzy、exact 跨页、同页偏移）且
candidate 的 `context_similarity < floor` 时，强制返回 `broken`。受保护的 case：
**same-location preserved**（exact + 同页 + 位置近邻）无论 ctx 多低都不被惩罚。

开关：
- `PDFANNO_BROKEN_CTX_FLOOR=<float>` 运行时指定 floor（默认 `0.0` = 关闭）
- `PDFANNO_DISABLE_BROKEN_FLOOR=1` 显式强关

默认关闭保证 v0.2.1 以来所有 baseline / 91 pytests 行为不变。

## 阈值 sweep 结果（semantic 1-to-1 oracle）

| floor | arXiv 1706 | BERT | W2V stressed | Seq2Seq | revised |
|---:|---|---|---|---|---|
| 0.00 | 84.6% / 6 | 92.9% / 1 | 80.0% / 3 | 100% / 0 | 88.5% / 3 |
| 0.02 | 84.6% / 6 | 92.9% / 1 | **93.3% / 1** | 100% / 0 | 84.6% / 4 |
| 0.05 | 84.6% / 6 | 92.9% / 1 | **93.3% / 1** | 100% / 0 | 76.9% / 6 |
| 0.08 | 79.5% / 8 | 92.9% / 1 | 93.3% / 1 | 100% / 0 | 76.9% / 6 |
| 0.10 | 79.5% / 8 | **100% / 0** | **100% / 0** | 100% / 0 | 76.9% / 6 |
| 0.15 | 82.1% / 7 | **100% / 0** | **100% / 0** | 94.7% / 1 | 76.9% / 6 |
| 0.20 | 79.5% / 8 | 92.9% / 1 | **100% / 0** | 94.7% / 1 | 73.1% / 7 |
| 0.25 | 76.9% / 9 | 92.9% / 1 | **100% / 0** | 89.5% / 2 | 69.2% / 8 |

**同样 floor 下的旧 oracle 回退**：

| floor | arXiv | BERT | W2V | Seq2Seq | revised |
|---:|---|---|---|---|---|
| 0.00 | 92.3% / 11 | 100% / 0 | 86.7% / 4 | 100% / 0 | 88.5% / 3 |
| 0.02 | 89.7% / 11 | 100% / 0 | 80.0% / 5 | 100% / 0 | 84.6% / 4 |
| 0.10 | 79.5% / 15 | 92.9% / 1 | 73.3% / 6 | 100% / 0 | 76.9% / 6 |
| 0.15 | 71.8% / 18 | 92.9% / 1 | 73.3% / 6 | 94.7% / 1 | 76.9% / 6 |

## 对照验收指标

**W2V 目标**：semantic 3 → ≤ 1 failure —— **达成**（floor=0.02 起）。
**arXiv 目标**：semantic 6 → ≤ 3 —— **未达成**（任何 floor 下 failure 数 ≥ 6）。
**Location 不回退** —— arXiv 在 floor=0.02 下 location 从 76.5% → 78.8%（微升）；其他基准 location 无变化。

## 为什么 arXiv 不响应

arXiv 6 条真失败的 match.py `context_similarity`（不是 oracle 的 ctx）：

```
anc_e3ba2da8 BLEU                   match.py ctx=0.126  ← floor=0.15 才抓到
anc_aa74a353 BLEU                   match.py ctx=0.108  ← floor=0.12 才抓到
anc_bc48e155 Scaled Dot-Product     match.py ctx=0.576  ← floor=0.58 才抓到（已经太高）
anc_896b8a39 Multi-Head Attention   match.py ctx=0.290  ← floor=0.30 才抓到
anc_273fc7d3 residual connection    match.py ctx=0.072  ← floor=0.08 才抓到
anc_68ab8fb0 Multi-Head Attention   match.py ctx=0.008  ← 容易抓但 gt=preserved，抓了反而错
```

在 floor=0.10~0.25 的范围内：
- 只有 ctx < floor 的那几条会被修正（fix: 2-3 条）
- 其他 arXiv anchor 里，`pred=relocated vs gt=relocated but pred 挑错页` 的一大批案例
  正好落在 ctx 0.05-0.15 区间，会被 floor **误伤**成 broken（over-fire）
- 净效果：break > fix，arXiv 总失败数反而上升

Word2Vec 幸运：它的 broken-truth case ctx 就是 0.00，floor=0.02 就能精准抓且不误伤其他。

## 根本诊断

`match.py._context_similarity` 用 `(before_ratio + after_ratio) / 2`，oracle 用
`SequenceMatcher(anchor_before||anchor_after, cand_before||cand_after).ratio()`。
**两个 ctx 度量在 arXiv 上的区分度不同** —— match.py 给很多 "位置 relocated but
ctx 实际较差" 的案例打了中等偏低的分（0.05-0.15），正好落在 floor 的拦截区。oracle
的 ctx 分布更极端（要么接近 0，要么接近 1），单一 floor 能精确划线。

## 决策

- **不** 作为默认行为启用。保留作 opt-in env toggle。
- **不** tag v0.2.2 —— 用户可见改动只是 W2V 1 条边缘 case，不足以构成 release story。
- **不** 继续在 floor 这条路径上调参 —— 数据显示任何 floor 都 fix / break 比例接近 1:1 在 arXiv 上。

## 下一步候选（Week 10+）

1. **对齐 match.py 的 ctx 算法与 oracle 的拼接算法**。如果两个 ctx 数字能匹配，
   floor 的 fix/break 比例应该能改善。改动点在 `match._context_similarity`。
2. **within-page ctx tie-breaking** —— 仍是备选，但现在看只解 `anc_68ab8fb0` 一条，
   影响面小。
3. **上 group assignment 加 ctx 优先级**（Week 7 Hungarian 加 ctx 约束）——
   Week 7 单纯 Hungarian 失败，但配合 ctx 作为 tie-breaker 可能有用。

embedding 继续延后。

## 本轮 commits

- `pdfanno/diff/match.py`: 加 `BROKEN_CTX_FLOOR` + `_active_broken_floor()` + 
  `_maybe_broken()` 进 `_classify`
- 本报告
- 无 baseline 改动（默认关闭，所有基线 Δ=0）
