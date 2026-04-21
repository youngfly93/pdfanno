# Week 5: Widen the benchmark base

## 动机

v0.2.1 的 `_context_window` 实验再次提醒：在单篇 arXiv 1706.03762 上调参 = 过拟合。
本轮目标：加 2-3 篇真实论文对，让失败模式统计有可靠依据，再决定攻哪个信号。

## 尝试的 4 篇（v1 vs latest）

| paper | arxiv_id | v1 / latest | 纳入基线? | 理由 |
|---|---|---|---|---|
| Transformer | 1706.03762 | v1 / v5 | ✅ 已有 | Week 1 spike baseline |
| BERT | 1810.04805 | v1 / v2 | ✅ **新加** | 版式开阔，14 条 anchor 全部 clean |
| Word2Vec | 1301.3781 | v1 / v3 | ❌ | `_selected_text` 暴露新 bug，见下 |
| Seq2Seq | 1409.3215 | v1 / v3 | ❌ | 同 Word2Vec |

## 新 baseline（`benchmarks/baselines/v0.2.0-multi.json`）

| benchmark | status | location | paired | failures |
|---|---:|---:|---:|---:|
| arXiv 1706.03762 v1↔v5 | 92.3% | 56.4% | 39 | 11 |
| revised synthetic | 88.5% | 100.0% | 26 | 3 |
| **BERT 1810.04805 v1↔v2** | **100.0%** | **78.6%** | **14** | **0** |

`compare_baseline.py` 已支持多 benchmark 对比 —— 任一指标回退都会 flag。

## 关键发现：`_selected_text` 的 **tight-layout 边界 leak**

Word2Vec 和 Seq2Seq 都跑不通，不是 diff 层的问题，是 **anchor 抽取层** 的问题：

```
Word2Vec v1_hl.pdf anchor selected_text 实例：
  'pared to the pre\nneural network\nmputational cos'
       ^^^^^^^^^^^^^^^              ^^^^^^^^^^^^^^^
       上一行末尾 leak                下一行开头 leak
```

### 根因

`pdfanno/diff/anchors.py::_selected_text` 用 `page.get_textbox(rect)` 抽取 annot
覆盖区域的文本。`rect` 是 annot quad 的包围盒。**PyMuPDF 返回所有 glyph bbox 与
rect 相交的字符**，而紧排版 PDF（行高 / 字号比 ≈ 1.1）中，相邻行的 glyph bbox 会
和 quad 的上下边界擦边 —— 哪怕人眼看 quad 只覆盖一行文字，算法也会 leak 进上一行
最后几个字符和下一行最前几个字符。

### 影响

- anchor 的 `selected_text` 变成多行混合串，再 `search_for(normalized)` 拿 quad 就
  0 命中 → `ground_truth.py::_rank_in_v1` 返回 `None` → `gt_status="needs_review"`。
- `context_similarity` 比的是污染过的 anchor 文本 vs v2 候选周围的文本，相似度失真。
- **不影响 arXiv 1706 / BERT**，因为它们的版式 leading 够宽。

### 影响范围

大多数会议 paper（NeurIPS、ACL、EMNLP）用紧排版 —— 这个 bug 在野外非常普遍。
现在的 benchmark 全是 "宽松版式论文"，掩盖了这个问题。

## 对下一步方向的修正

先前我建议：
> 1. 扩 benchmark 基础
> 2. 攻 arXiv location 56.4% 痛点

现在加上：
> 0. **先修 `_selected_text` 的边界 leak**，否则没法跑紧排版 benchmark —— 也没法
>    确认 location 痛点在 "宽松版式" vs "紧排版" 两类论文里是不是同一个问题。

### 修法预览

把 `_selected_text` 里的 `page.get_textbox(rect)` 改成 **按 y 中心过滤**：
只保留 glyph bbox 中心 y 落在 quad y-范围内（留 small epsilon）的 chars。
伪代码：

```python
# 原实现
txt = page.get_textbox(rect)

# 修法
words = page.get_text("words")  # [(x0,y0,x1,y1,word,block,line,word_no)]
y_lo, y_hi = rect.y0, rect.y1
txt = " ".join(
    w[4] for w in words
    if (w[1] + w[3]) / 2 >= y_lo - 1 and (w[1] + w[3]) / 2 <= y_hi + 1
    and max(w[0], rect.x0) < min(w[2], rect.x1)  # x 轴有重叠
)
```

需要测：
- Word2Vec / Seq2Seq 的 anchor.selected_text 是否 clean。
- arXiv 1706 / BERT 的 baseline 不回退（compare_baseline 验证）。

## 不再立即做的

- **section_sim 完整 judicial 测试**：仍然留给 "ctx 边界问题解决后" 再看。
- **arXiv 56.4% location 攻关**：需要先有"多样本紧排版"benchmark 才能分辨这是 "短
  token repeats in loose-layout" 特殊症状还是"short-token-in-all-layouts" 通病。

## 本轮 commits

- `benchmarks/fixtures/annotate_real_pair.py` —— 给 PDF 打 highlight 的通用工具
- `benchmarks/fixtures/build_real_pairs.py` —— 一键下载+打标+生成 gt
- `benchmarks/baselines/v0.2.0-multi.json` —— 三 benchmark baseline 快照
