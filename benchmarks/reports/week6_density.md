# Week 6: anchor-density stress test

## 要回答的问题

v0.2.1 的 5-benchmark 里 Word2Vec / Seq2Seq 都是 100%/100%。这让"arXiv 56.4% 痛点
是 paper-specific" 有两种可能解释：

- **A: arXiv-only pattern** —— 这类失败只在 1706.03762 发生，W2V/Seq2Seq 永远 100%。
- **B: density-driven** —— W2V/Seq2Seq 每篇只 8 条 anchor 且全是 "干净" 短语，
  不碰重复短 token 的 k-th 映射。anchor 密度和挑战度上去了同样会失败。

Week 6 的实验直接判 B 还是 A：给 W2V / Seq2Seq 加重复短 token 的 **第 N 次命中**，
看失败是否出现。

## 实现

扩展 `annotate_real_pair.py`：同一 phrase 在列表里出现 K 次 → 依次高亮它在全局
reading-order 下的第 1、2、...、K 次命中。这样 "LSTMs, LSTMs, LSTMs, LSTMs" 会
标出 LSTMs 的前 4 次出现，模拟 arXiv v1 里 `BLEU × 2` / `WMT 2014 × 4` /
`Multi-Head Attention × 3` 的真实分布。

扩容后的 spec：

- **Word2Vec**: 8 原始 + `NNLM × 3 + CBOW × 2 + Mikolov × 2` = 15 条
- **Seq2Seq**: 8 原始 + `LSTMs × 4 + BLEU × 3 + SMT × 2 + RNN × 2` = 19 条

## 结果（`benchmarks/baselines/week6_stressed.json`）

| benchmark | paired | status | location | failures | vs v0.2.1 |
|---|---:|---:|---:|---:|---|
| arXiv 1706 v1↔v5 | 39 | 92.3% | 56.4% | 11 | Δ=0 |
| revised synthetic | 26 | 88.5% | 100.0% | 3 | Δ=0 |
| BERT 1810 v1↔v2 | 14 | 100.0% | 78.6% | 0 | Δ=0 |
| **Word2Vec 1301 v1↔v3** | **15** | **86.7%** | **57.1%** | **4** | **100→86.7%** |
| Seq2Seq 1409 v1↔v3 | 19 | 100.0% | 94.7% | 0 | 100→100% |

### 判决

**B 成立（部分）**：Word2Vec 压力测试下 86.7% status + 57.1% location，**数字几乎
完美镜像 arXiv 的 92.3% / 56.4%**。4 条失败全是 Mikolov / NNLM 的第 k 次命中被
映射到 v2 错误位置 —— 和 arXiv 的 BLEU / WMT 失败一模一样。

Seq2Seq 压力测试仍然 100% status（但 location 掉到 94.7%，1 条 same-page 偏移
> 15pt）。Seq2Seq 是反例，说明不是所有紧排版论文都会触发 —— 当 v1→v3 重构幅度
小（paper 主体结构没动，短 token 的 reading-order 位置和 v1 基本一致）时，k-th
映射自然对齐，信号层的缺陷显不出来。

## Word2Vec 失败细节

```
anc_a76798b3 'mikolov'  relocated pred, preserved gt  (same-page, 同位置)
anc_9f28db2d 'NNLM)'    relocated pred p2, gt p1       (跨页，映射到错误页)
anc_01346b5f 'Mikolov'  relocated pred, preserved gt  (same-page, 同位置)
anc_defff5e1 'NNLM'     relocated pred p2, gt p1       (跨页，映射到错误页)
```

模式和 arXiv 的 `anc_75144146 'BLEU'` / `anc_ef1075cc 'WMT 2014'` 完全一致：
短 token 第 k 次命中的 anchor，`layout.rank_sim` 和 v2 的 k 对齐失败，score 让
错误的候选胜出。

## 结论

**arXiv 1706 的痛点不是 paper-specific，是 repeated-short-token relocation 的信号
缺陷**。只要 anchor 中含 ≥ 2 次出现的短 token 且 v2 结构有重构，任何 paper 都会
出现同类失败。

之前的假设列表 **"arXiv 痛点坐实为 Results 章节特有模式"** 被否定。更新的描述：

> 痛点 = "repeated short-token × 结构重构" 组合场景。受影响面比我们之前以为的大。

## 对 Week 7+ 的方向收紧

1. **信号层攻关方向重新获得优先级**（之前曾讨论过 defer）：
   - **section-normalized y**：候选 y 在它所在 section 内的相对位置，与 anchor 的
     对齐。Seq2Seq 就是因为 section 内部没动，天然对齐了 —— 说明这个信号正确时
     100% 可达。
   - **group assignment for repeated tokens**：同 token 的 K 个 anchor 作为一组，
     一次性求与 v2 K 个候选的最优匹配（Hungarian-in-group），而不是逐个 greedy。
     Week 2 B 的 Hungarian 失败是因为在 **所有** anchor 上全局 Hungarian，本次
     只在同 token 的小组内做，风险小很多。
   - **rank_sim 归一化重调**：当前 `v1_norm = v1_rank / v1_total` 对跨版本 total
     变化敏感（v1 有 13 个 Mikolov，v2 可能有 10 个，norm 漂移）。改用绝对 rank
     差或 log-scale 距离可能更稳。

2. **embedding 继续延后** —— 信号层的 rank_sim 还没榨干之前别引入黑盒。

3. **下次 tag** 的触发条件：Word2Vec stressed 从 86.7%/57.1% → 至少 95%/85%，
   且 arXiv 不回退。

## Commit 本轮

- `annotate_real_pair.py`：支持同 phrase 多次出现标第 N 次命中
- `build_real_pairs.py`：W2V / Seq2Seq 加入 stress phrases
- `benchmarks/baselines/week6_stressed.json`：压力测试基线
