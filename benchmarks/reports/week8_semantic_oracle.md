# Week 8: semantic-aware oracle —— 痛点减半但未消失

## 做了什么

写 `benchmarks/tools/ground_truth_semantic.py` —— 对每条 v1 anchor，在 v2 的所有
同 token occurrences 中选 **context_similarity 最高** 的作为 gt（而不是 old
oracle 的 rank-k-to-rank-k）。若最佳 ctx_sim < 0.15，标 `gt_status="broken"`
（token 在 v2 语义上已失位）。

生成的 GT 存在 `benchmarks/baselines/gt_semantic_<benchmark>.json`，与
`evaluate.py` 兼容，可以直接 diff 旧 gt 看差异。

## 结果

| benchmark | OLD oracle status / loc / failures | NEW oracle status / loc / failures |
|---|---|---|
| arXiv 1706 v1↔v5 | 92.3% / 56.4% / **11** | 87.2% / 56.4% / **5** |
| BERT 1810 v1↔v2 | 100% / 78.6% / 0 | 92.9% / 76.9% / **1** |
| Word2Vec 1301 v1↔v3 | 86.7% / 57.1% / 4 | 80.0% / **91.7%** / **3** |

**核心数字**：失败数 11 → 5 + 0 → 1 + 4 → 3。Week 7 假设 "arXiv 56% location 痛点
大部分是 oracle 伪反例" —— **部分成立**。

## 细节

### arXiv：6 条失败是 oracle 伪反例，5 条是真算法缺陷

26/39 的 anchor 在新 oracle 下换了 v2 的 target rank（semantic 挑的不是 rank-k）。
8/39 换了 gt_page。old oracle 的 11 条失败里：

- **6 条** 在新 oracle 下消失（我们的 pred 语义上本来就对）。
- **5 条** 在新 oracle 下仍然失败，是真正的算法缺陷：

```
anc_68ab8fb0 'Multi-Head Attention' preserved vs relocated  (same page, 0pt → pred 吻合旧位置，语义应该移位)
anc_75144146 'BLEU'                 preserved vs relocated  (p7, pred 原位，ctx 说应该移 45pt)
anc_ca659072 'BLEU'                 preserved vs relocated  (p0, pred 原位，ctx 说移 102pt)
anc_19618585 'BLEU'                 preserved vs relocated  (p0, pred 原位，ctx 说移 54pt)
anc_325ab5d4 'WMT 2014'             preserved vs relocated  (p0, pred 原位，ctx 说移 102pt)
```

**全都是 "pred=preserved, gt=relocated, 同页但 shifted > 15pt"**。模式很一致：

- 算法找到了跟 anchor 的旧 quad 位置匹配的 v2 同 token occurrence（因为 y_sim 打分高）
- 语义正确的 target 是同页 **另一个** occurrence，ctx 吻合
- 当前 `layout.y_sim` 把 "y 位置相近" 当强信号，但 v2 在同页可能有 2-3 个同 token
  候选，y_sim 挑错

### Word2Vec：location 57% → 92% 是真的跳涨，但失败仍有 3 条

5/15 anchor 换了 v2 的 target rank。4/15 换了 gt_page。位置精度大幅提升说明 Word2Vec
大部分 "失败" 是 rank-shift 艺术品。3 条残留的真失败：

- `anc_d886ca46 'vector representations of words'` → gt=broken（ctx 0.085，语义已丢失）
- `anc_bf673eaa 'neural network language models'` → gt=broken（ctx 0.000）
- `anc_01346b5f 'Mikolov'` preserved vs relocated（和 arXiv 一样的"同页 shift"模式）

2 条 broken 说明 v2 里 Mikolov 2013 团队的经典表述被重写了；pred 强行找到同 token
的别的位置，但语义上不对。

### BERT：新暴露 1 条 over-relocation

`anc_f86ca436 'Fine-tuning'` pred 映射到 v2 某处，但所有 v2 occurrences 的 ctx_sim
都 < 0.10 — 新 oracle 说"这个 anchor 的语义在 v2 已不存在，应该 broken"。这是
**新 oracle 更严的判断**暴露出的"过度乐观 relocation"。

## 结论修正

之前的口径 **"arXiv 56% location 痛点大部分是 oracle 伪反例"** 需要收紧为：

> **约一半**是 rank-shift oracle 伪反例。另一半是真实的算法缺陷 —— 具体表现为
> "**同页多 occurrence 时，y_sim 把 pred 锁到原位置，忽略了 ctx 更吻合的同页
> 移位候选**"。

## 对 Week 9 的方向（按数据收紧）

原先三个候选：
1. group assignment（Week 7 已否决）
2. section-normalized y
3. rank_sim 归一化重调

**重新排序**：

1. **within-page ctx tie-breaking**（NEW · 最高优先）—— 当候选们都在同一页且 y_sim
   接近时，用 ctx_sim 作为决定者。这直接针对新 oracle 暴露的 5 条 arXiv 失败：
   同页多 BLEU / Multi-Head，pred 选 "y 原位"，应该选 "ctx 吻合"。

   实现上简单：在 `_layout_score` 之外，增加 "同页 y 近似 + ctx 显著高" 时的
   score bonus；或者重新平衡 W_CONTEXT ∈ {0.30 → 0.40}，W_LAYOUT ∈ {0.15 → 0.10}
   让 ctx 更主导。

2. **over-relocation 保守化** —— 当所有 v2 候选 ctx 都显著低（< 0.15）时，应该
   倾向于 broken 而不是强行 relocate。目前 BERT 的 Fine-tuning 和 W2V 的两条
   vector/NNLM 都属于这一类。可以在 classification 层加 "ctx floor" 阈值。

3. **rank_sim 归一化** 维持 defer —— 新 oracle 结果看 W2V location 大幅提升是
   因为 oracle 对齐了我们的 pred，不是 rank_sim 变好，所以不急着改。

embedding 继续延后。

## Week 9 的触发条件

- arXiv 在 new oracle 下：status 87.2% → 95%+，failures 5 → 1-2。
- W2V 在 new oracle 下：status 80% → 90%+，failures 3 → 1。
- 旧 oracle 的数字也不回退（作为兼容性护城河）。

## Commit

- `benchmarks/tools/ground_truth_semantic.py` —— 新 oracle 实现
- `benchmarks/baselines/gt_semantic_{arxiv,bert,word2vec}.json` —— 3 个 benchmark
  的 semantic GT
- 本报告

---

## Addendum: oracle v2 —— 1-to-1 修正

第一版 oracle 独立地为每个 anchor 挑 ctx-best v2 occurrence，结果发现 **28/39
arXiv anchor 指向了被其他 anchor 同时认领的 v2 位置**（5 条 BLEU 都指到 p=0 y=443；
5 条 Multi-Head/Scaled-Dot 都指到 p=3 y=76；等）。这样的 oracle 不是有效的 GT ——
任何 1-to-1 分配的算法都无法同时满足多个 anchor 指向同一位置的要求。

修法：**global greedy 1-to-1** —— 按 ctx_sim desc 排所有 (anchor, v2_occ) 三元组，
每个 anchor 只认领一次，每个 v2 位置只被认领一次。对无法被分配的 anchor（它想要的
v2 位置被更匹配的 anchor 抢了，自己的第二候选 ctx 又 < 0.15），标 `gt_status=broken`。

重跑后的**正确**数字：

| benchmark | OLD oracle | NEW (1-to-1 semantic) | failures |
|---|---|---|---:|
| arXiv 1706 | 92.3% / 56.4% / 11 | 84.6% / **76.5%** / 6 | 11 → 6 |
| BERT 1810 | 100% / 78.6% / 0 | 92.9% / 76.9% / 1 | 0 → 1 |
| Word2Vec 1301 | 86.7% / 57.1% / 4 | 80.0% / **100%** / 3 | 4 → 3 |

arXiv location 从 56.4% 涨到 **76.5%**（而不是 v1 oracle 误报的 "56.4%不变"）。
这才是正确的数字。

### 失败模式再次聚类（1-to-1 GT）

arXiv 6 条真实失败：
```
anc_68ab8fb0 Multi-Head Attention  relocated vs preserved  (同页 0pt)
anc_e3ba2da8 BLEU                  relocated vs broken     (own_ctx=0.126, 其他 anchor 抢光了)
anc_aa74a353 BLEU                  relocated vs broken     (own_ctx=0.108)
anc_bc48e155 Scaled Dot-Product..  relocated vs broken     (own_ctx=0.576 —— 实际较高但被抢)
anc_896b8a39 Multi-Head Attention  relocated vs broken     (own_ctx=0.290)
anc_273fc7d3 residual connection   relocated vs broken     (own_ctx=0.072)
```

Word2Vec 3 条：
```
anc_bf673eaa neural network language models  relocated vs broken (ctx=0.000)
anc_d886ca46 vector representations of words  relocated vs broken (ctx=0.085)
anc_01346b5f Mikolov                          relocated vs broken (其他 anchor 抢光)
```

**核心模式**：真实失败里 **8/9 是 "pred=relocated, gt=broken" 而不是 "同页 shift"**。
我们的算法 **太乐观地 relocate 到 semantic ctx 很弱的位置**，而 1-to-1 oracle 会说
"你的真实语义在 v2 已丢失，应该 broken"。

### 对 Week 9 攻关方向的修正

之前写的两个候选：
1. within-page ctx tie-breaking
2. broken floor

数据一出来，**priority 反转**：

- **#1 broken floor**（新第一）—— 当 anchor 的 best candidate ctx_sim 很低 OR 所有
  候选看起来都不语义对齐时，分类给 `broken`。具体门槛可以参考 oracle 的 0.15：
  所有 candidate 的 ctx_sim 都低于某阈值（如 0.20）→ broken。这直接对付 8/9 真失败。
- **#2 within-page ctx tie-breaking** —— 只对 `anc_68ab8fb0` 一条，不够广。

**Week 9 新计划**：先上 broken floor，看 arXiv / W2V 的 8 条 broken 失败能解多少。
解完后再决定是否继续 ctx tie-breaking。
