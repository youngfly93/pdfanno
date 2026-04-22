# Week 7: Hungarian-in-group + 诊断出 oracle 的系统性偏差

## 原计划

Week 6 发现：Word2Vec 压力测试 86.7% / 57.1% 镜像 arXiv 的 92.3% / 56.4%，
失败都是 "repeated short-token × 结构重构"。本周按优先级 1 的方案尝试：

> Group assignment for repeated tokens —— 同 normalized selected_text 的 K 个
> anchor 作为一组，组内对 v2 候选池做 Hungarian 最大化分配。

为什么预期比 Week 2 B 的全局 Hungarian 更稳：组内所有 score 测量的都是"哪个 v2
占位是我的 k"，**sum-max 的总和最大应当和语义对齐**（Week 2 B 的失败是跨 token
sum-max 跨越了语义不可比的比较）。

## 实现

`_assign_one_to_one` 改为两阶段：Phase A 对 K ≥ 2 的 group 跑 `assign_max_score`
（复用 `_hungarian.py`），Phase B 对剩余 anchor 走原 greedy。加 `PDFANNO_DISABLE_
GROUP_ASSIGN=1` 环境开关支持 A/B 回退。

## 结果 —— 失败

| benchmark | 原 greedy | Hungarian-in-group | Δ status | Δ location |
|---|---|---|---:|---:|
| arXiv 1706 | 92.3% / 56.4% | 89.7% / 53.8% | **−2.6pp** | **−2.6pp** |
| revised | 88.5% / 100% | 88.5% / 100% | 0 | 0 |
| BERT | 100% / 78.6% | 100% / 78.6% | 0 | 0 |
| Word2Vec stressed | 86.7% / 57.1% | 86.7% / 57.1% | **0** | **0** |
| Seq2Seq stressed | 100% / 94.7% | 100% / 94.7% | 0 | 0 |

两个坏消息：

1. arXiv 下跌，**和 Week 2 B 完全一样的 1 条 case 翻车**：
   `anc_68ab8fb0 Multi-Head Attention`：greedy 判 relocated@p3（正确）→ Hungarian-
   in-group 判 preserved@p3（错）。
2. Word2Vec —— 本应由 group assignment 救起 —— **数字毫无变化**。

Word2Vec 不动的原因：它的 "repeated" anchor 实际上 **没有被成功分组**。扫描
selected_text 发现：

```
Mikolov (rank 0), mikolov (rank 1)    -- 大小写不同
NNLM (rank 2), NNLM) (rank 0), nnlm (rank 0)  -- 带标点 / 大小写不同
CBOW. (rank 0), CBOW, (rank 0)              -- 带标点
```

`_selected_text` 按 anchor 实际 quad 抽出的文本保留了大小写和首尾标点。`normalize_
text` 不处理这两类差异 → group key 分裂 → 每个"重复"实例被当成独立单例，Hungarian
没机会启动。

简单的 "aggressive normalization (lower + strip punct)" 能解这个分组问题，但要
**先确认主流程真能用上 Hungarian** 再做。而 arXiv 的回归表明主流程本身不工作。

## 深挖 `anc_68ab8fb0`：发现 GT oracle 的系统性偏差

这是历次 revert 的标志性 case：Week 2 B / Week 3 C (0.45 section 权重) / Week 4
ctx-fix / Week 7 Hungarian —— 每次 regression 都在这一条上翻。

### v1 `Multi-Head Attention` 分布（8 次）
```
rank 0: p1 y=317.4
rank 1: p1 y=706.5
rank 2: p1 y=717.5
rank 3: p3 y=76.2   ← ANCHOR（anchor.section='3.2.3 Applications of Attention in our Model'）
rank 4..7: p3 分散
```

### v5 `Multi-Head Attention` 分布（10 次）
```
rank 0: p0 y=626.6
rank 1: p0 y=636.5
rank 2: p1 y=428.4
rank 3: p2 y=576.4  ← GT 说 anchor 应映射到这里（rank-k-to-k 规则）
rank 4: p2 y=587.4
rank 5: p3 y=76.2   ← 我们的 algorithm 挑了这里（同页同 y，ctx=0.73）
rank 6..9: p3-p4 分散
```

### 候选 score 剖面（我们的 algorithm 视角）

```
candidate                      text    ctx     layout  score
p=3 y=71   ← pred winner       1.000   0.733   0.737   0.8806
p=3 y=274                      1.000   0.290   0.621   0.7301
p=3 y=605                      1.000   0.008   0.451   0.6201
p=2 y=571  ← GT claims correct 1.000   0.000   0.598   0.6064   ← ctx=0 !
p=2 y=582                      1.000   0.000   0.603   0.6071
p=4 y=74                       1.000   0.000   0.565   0.6015
p=4 y=260                      1.000   0.000   0.464   0.5863
p=0 y=622                      1.000   0.000   0.432   0.5149
p=0 y=632                      1.000   0.000   0.463   0.5194
p=1 y=423                      1.000   0.015   0.598   0.5774
```

**关键数字**：
- Pred winner 在 v5 p=3 y=71 有 **ctx_sim = 0.733**（anchor ctx 有 "Scaled Dot-
  Product Attention..." heading，v5 p=3 y=71 上面正好是 3.2.1 Scaled Dot-Product
  Attention section 的末尾 —— ctx 完美对齐）。
- GT claims correct 在 v5 p=2 y=571 有 **ctx_sim = 0.000**（那个位置是 Figure 2
  caption "Figure 2: (left) Scaled Dot-Product Attention. (right) Multi-Head
  Attention..."，与 anchor ctx 几乎无重叠）。
- 我们的 section_path: anchor="3.2.3 Applications of Attention in our Model"；
  pred winner 在 v5 的 3.2.2 Multi-Head Attention section 下；gt claims correct
  在 v5 p=2 y=571 的 3.2 Attention section 下（Figure caption 位于子节之前）。

### 语义判断

**pred 选的位置更对**。理由：
1. ctx=0.733 vs 0.000 —— pred 在语义上是 anchor 的自然延续，gt 选的是 Figure
   caption（一个视觉元素），语义完全不同。
2. 从章节结构看 —— v1 anchor 在 "3.2.3 Applications of Attention"，v5 的 pred
   候选在 "3.2.2 Multi-Head Attention" 中，两者都是关于 Multi-Head Attention
   在模型中的使用；v5 加了 Figure 2 caption 让 rank 向前位移 2 位，
   **rank-k-to-rank-k 的 oracle 被这个位移诱导到了错误的位置**。

### Oracle 的根本缺陷

`benchmarks/tools/ground_truth.py` 的 GT 生成逻辑：

> v1 中的第 k 次出现（按阅读顺序）→ v2 中的第 k 次出现 = ground truth 位置。

这在 **v1 和 v2 的 token 数量相同且 reading-order 不变** 时 100% 正确。但论文
修订往往 **增删同 token 的出现**（例如 v5 加了 Figure 2 caption 引入新
occurrences）。此时 rank-k-to-rank-k 与 "同一 semantic 位置" 的对应就断了。

我们的打分器其实在 **修正这个 oracle 的错误**（靠 ctx_similarity），但被 eval
报告成 "失败"。

## 影响 —— 重新看待 arXiv 的 56.4% location 痛点

17 条 location 失败中：
- 3 条 **status 也错**（pred preserved / gt relocated）：其中至少 2 条（BLEU × 2
  rank 6、7，ctx_sim=0.99）看起来也是同页同 y 的 rank-shift 情况，oracle 说
  "moved"，pred 说 "didn't move" —— 可能也是 oracle 偏差。
- 14 条 **status 对、location 错**：pred 选的候选往往有显著更高的 ctx_sim（上表
  清楚展示）。rank-shift 的解释能覆盖很大比例。

**假设**（待 Week 8 验证）：**arXiv 56.4% location accuracy 中有相当部分不是算法
缺陷，是 oracle 的 rank-k-to-k 规则在遇到 token 增删时的伪反例**。

## 处置

1. **revert Week 7 代码改动**。`_assign_one_to_one` 回到 greedy。本次 commit 只
   留报告和这个日志性 finding。
2. **不 tag**。没有数字改进。
3. **Week 8 方向切换到 oracle audit**（详见下节）。

## Week 8 的新任务

替代"继续调信号层"，先做一件更基础的事：

**重写 ground_truth.py 为 semantic-aware oracle**。思路：

- 对 v1 的每条 anchor，提取其局部 ctx（around quad）。
- 对 v2 的同 token 所有 occurrences，每个都用 local ctx 和 anchor ctx 比对。
- 选 ctx match 最高的 v2 占位作为 ground truth，而不是 rank-k。

评估框架：

1. 在 arXiv / Word2Vec 上对比 new GT 和 old GT：哪些 case 换了 gt_quad？
2. 用 new GT 重新跑 eval：arXiv / W2V 的 location accuracy 是不是会涨？
3. 如果涨，说明大部分"location 失败"确实是 oracle 偏差；真正需要改进的算法错误
   数会显著变少。

**如果 new GT 让 arXiv 从 56.4% → 85%+，那"本季度最该攻 location"这件事本身就
失焦了**。真正的算法缺陷可能只是 3-5 条，而不是 11-17 条。

**embedding 继续延后**。所有信号层改进在 oracle audit 完成前都是打空气。
