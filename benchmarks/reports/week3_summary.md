# Week 3 summary — section detection + section_sim

## What changed vs Week 2

- **New module** `pdfanno/diff/sections.py`：`build_section_index(doc)` 优先用 `doc.get_toc()`，否则 font-size 启发；返回带完整路径（"1 / 1.2 / 1.2.3 Title"）的 `SectionSpan` 列表。
- **新字段** `Anchor.section_path: str | None`（`types.py`）；`extract_anchors` 现在用 quad 中心 y 定位 anchor 所属 section，填入该字段。
- **layout_score 从 3 子分变 4 子分**（`match.py`），新增 `section_sim`：

  | subscore | weight | 含义 |
  |---|---:|---|
  | section_sim | 0.20 | 同 section=1.0，跨 section=0.0，双向 None=1.0（对称噪声不扣分），单侧 None=0.5 |
  | rank_sim    | 0.50 | reading-order k-th 映射（Week 2 原值 0.60） |
  | y_sim       | 0.25 | 同页 y-位置 |
  | x_sim       | 0.05 | 双栏 column 提示 |

- **单测** `tests/test_sections.py`：10 条，覆盖 TOC 路径、font-heuristic 路径、`section_for` 查找、层级 path、siblings 不叠加。全部 88 pytests 绿。

## Eval 结果

| benchmark | Week 2 | Week 3 | Δ |
|---|---:|---:|---:|
| arXiv 1706.03762 v1↔v5 (status) | 92.3% | 92.3% | 0 |
| arXiv 1706.03762 v1↔v5 (location) | 56.4% | 56.4% | 0 |
| revised synthetic (status) | 88.5% | 88.5% | 0 |
| revised synthetic (location) | 100.0% | 100.0% | 0 |

**两个 benchmark 数值完全未变**。分析：

1. **arXiv 1706.03762**：所有 11 条失败都是短 token 跨页/同页大幅位移（`BLEU` × 2, `WMT 2014` × 4, `Multi-Head Attention` × 3, `Scaled Dot-Product Attention` × 2）。这些重复全部发生在 **同一 section 内**（Results / Attention 相关章节），因此 `section_sim` 对所有候选一视同仁（全 1.0），提供不了额外判别力。
2. **revised synthetic**：fixture 用均匀字号合成，既无 TOC 也无字号 heading → `build_section_index` 返回空 → anchor 与 candidate 的 `section_path` 都是 None → 走新引入的 "两侧都 None → 1.0" 分支（退 0.5 会静默惩罚所有候选，详见 `_layout_score` 注释）。`section_sim` 同样无判别贡献，等价于 Week 2。

## 结论

`section_sim` 是 **潜在能力（latent capability）改动** —— 当前两个 benchmark 都不触发它的判别路径，所以看不到数字变化；但代码路径已通、单测已覆盖、权重已调到保守值，未来任何一对 **短 token 跨 section 重复** 的 PDF 都能立刻用上它。

## 待验证（Week 3 后续或 Week 4）

现有 eval 集缺失 "短 token 跨 section 出现" 场景。要真正验证 `section_sim` 的价值，需要构造或寻找：

- 同一术语（例如 "Figure 1" / "Table 2" / 某个数据集名）在两个不同 section 都被高亮的 PDF；
- v2 把其中一条搬到第三个 section 的场景；
- 期望 `section_sim` 使正确匹配赢过 "同文本不同 section" 的错误候选。

在这样的 fixture 没建起来之前，Week 3 的 eval 读数虽然与 Week 2 持平，但 **不意味着改动无意义** —— 只意味着当前 benchmark 不涵盖它的立项场景。

## Weights 取舍

初版试了 `section=0.45, rank=0.30, y=0.20, x=0.05`，arXiv 立刻从 92.3% 跌到 89.7%（1 条 `Multi-Head Attention` 从 relocated 被误判为 preserved）。根因不是 section_sim 逻辑本身，而是 rank 权重从 0.60 砍到 0.30 过猛 —— 当所有候选 `section_sim` 都是 1.0 时，rank 才是唯一判别信号，削弱它就会让原本边缘的相邻候选翻盘。

最终采用 `section=0.20, rank=0.50, y=0.25, x=0.05`，保持 rank 的主导地位；下一次如果真在跨 section fixture 上看到 section_sim 有判别贡献，再考虑上调它的权重。
