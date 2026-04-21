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

## 判决力试验：尝试了，没跑通，诚实说明

为了验证 section_sim **不只是 latent**，Week 3 C-3 加了：
- `PDFANNO_DISABLE_SECTION_SIM=1` 开关（`match.py`），支持 counterfactual 测试。
- `benchmarks/fixtures/build_cross_section.py` —— 合成 v1/v2 对：v1 Results 里 1 个
  "Figure 1 data"；v2 Discussion/Results 各 1 个，body 上下文对称。
- `tests/test_cross_section.py` —— 原目标：OFF 选错、ON 选对的翻盘测试。

**构造翻盘失败**。原因：
1. `_context_window` 用 `norm_page.find(selected)` 定位，永远返回 page 里 **第一次** 出现
   的位置；当 v2 把 "Figure 1 data" 在 Discussion 先、Results 后时，WRONG (Discussion)
   候选的 ctx 天然包含 v2 后续的大量 "Intro sentence / Figure 1 data" 重复，而 CORRECT
   (Results) 候选位于页尾，ctx 更短、重复更少。**实测 ctx_sim WRONG=0.85, CORRECT=0.68**。
   差值 0.17 × W_CONTEXT(0.30) = 0.051 overall 优势给 WRONG。
2. section_sim 在 W_LAYOUT(0.15) × W_LAYOUT_SECTION(0.20) 下 overall 上限 = 0.030。
   填不满 ctx 的 0.051 坑。试过把 W_LAYOUT_SECTION 抬到 0.25/0.35/0.45 几档，
   要么 section 预算仍不足，要么需要把 W_LAYOUT_RANK 砍低到让 arXiv 92.3% → 89.7%。

**降档的断言**（`test_cross_section.py`，3 条全绿）：
1. `section_path` 成功填入 anchor 和两个 candidate（wiring 不丢）。
2. `PDFANNO_DISABLE_SECTION_SIM` 开关确实改变 layout_score（代码路径生效）。
3. section_sim ON 相对 OFF，把 WRONG 对 CORRECT 的 layout 领先缩小了 **0.20±0.05**
   —— 与 W_LAYOUT_SECTION × (1.0 - 0.0) 的理论预期一致（方向对、幅度对）。

这些是 **机理证据**（mechanistic evidence），不是 end-to-end 翻盘证据。section_sim
的信号方向正确、幅度对，但在当前全局权重平衡下还不足以单独翻盘。

## Week 4 的两件事（按优先级）

1. **修 `_context_window`**：让 anchor 的 context 从自身 quad 中心抽取，而非第一次
   出现的位置。这个小改动会让 ctx_sim 在所有多实例场景下都更准（不仅是本 fixture）。
   修完后重跑 arXiv 看 location accuracy 是否涨（当前 56.4% 的主要痛点）。
2. **修完 ctx 之后再补 end-to-end 翻盘测试**：同样的 fixture、同样的开关，目标是
   OFF 选错、ON 选对。若那时仍不能翻，就果断收斩 W_LAYOUT_SECTION 或者明确把 section
   降级为 tie-breaker（只在其他信号 tie 时生效）。

embedding 留给更后面。

## Weights 取舍

初版试了 `section=0.45, rank=0.30, y=0.20, x=0.05`，arXiv 立刻从 92.3% 跌到 89.7%（1 条 `Multi-Head Attention` 从 relocated 被误判为 preserved）。根因不是 section_sim 逻辑本身，而是 rank 权重从 0.60 砍到 0.30 过猛 —— 当所有候选 `section_sim` 都是 1.0 时，rank 才是唯一判别信号，削弱它就会让原本边缘的相邻候选翻盘。

最终采用 `section=0.20, rank=0.50, y=0.25, x=0.05`，保持 rank 的主导地位；下一次如果真在跨 section fixture 上看到 section_sim 有判别贡献，再考虑上调它的权重。
