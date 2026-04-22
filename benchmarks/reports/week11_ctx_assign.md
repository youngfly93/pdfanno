# Week 11: ctx-aware assignment preemption —— 机制对，但打不响

## 机制（落实 Week 11 spec）

在 `_assign_one_to_one` 的 greedy 分配里加一条 **可选 preemption 规则**：候选 slot
已被另一 anchor 占用（holder），contender 只在以下三个条件同时满足时抢占：

1. **同 token**：`normalize_text(holder.selected_text) == normalize_text(contender.selected_text)`
2. **score 接近**：`holder.score - contender.score ≤ ε`（默认 0.05）
3. **ctx 显著优势**：`contender.ctx - holder.ctx ≥ Δ`（默认 0.10）

触发后：holder 被清除，contender 占 slot，holder 进入 displaced 队列，扫完所有
pair 后给 displaced anchor 按自己的 score-desc 找下一个未占用候选。

默认**关闭**。开关：`PDFANNO_CTX_AWARE_ASSIGN=1`，阈值用
`PDFANNO_CTX_ASSIGN_EPSILON` / `PDFANNO_CTX_ASSIGN_MIN_ADVANTAGE`。

## A/B 结果（4 配置 × 5 benchmark × 2 oracle）

| 配置 | mode | floor | ctx_assign | arXiv sem | W2V sem | old oracle 伤害 |
|---|---|---|---|---|---|---|
| A（baseline） | mean | 0.00 | off | 84.6%/6 | 80.0%/3 | 无 |
| B | mean | 0.00 | **on** | 84.6%/6 | 80.0%/3 | 0 |
| C | concat | 0.00 | **on** | 84.6%/7 | 73.3%/4 | 小 |
| D | concat | 0.10 | **on** | **89.7%/4** | 86.7%/2 | arXiv −13pp |

**关键**：config D 的 89.7%/4 和 **Week 10 的 concat+floor=0.10（无 ctx_assign）
完全相同**。Week 11 的 preemption **在此组合下净贡献为 0**。

## 诊断：为什么打不响

跑了一遍 arXiv 的所有 preemption opportunities（same-token + slot 竞争）：

- **32 个 same-token slot 冲突** 里，30 个 `ctx_delta ≈ 0`（contender 和 holder 对同
  一 candidate 看到的 ctx 数值完全一样），1 个 `ctx_delta = +0.1228`（真正的可抢占
  机会），1 个 `ctx_delta < 0`（contender 的 ctx 反而更低）。
- 按默认阈值（eps=0.05, adv=0.10），**只有 1 个 case 触发抢占**：
  `anc_fb5847c6 'Scaled Dot-Product Attention'` 抢走了 `anc_644fcf6f` 的 p3 y=279。
  - 抢占前：fb5847→y=343, 644fcf→y=279
  - 抢占后：fb5847→y=279, 644fcf→y=343
  - semantic gt 说 fb5847 应该在 y=324, 644fcf 应该在 y=279
  - 所以抢占让 644fcf 从 PERFECT MATCH (y=279=gt) 变成 MISS (y=343)，
    fb5847 从 MISS-19pt 变成 MISS-45pt —— **两者都更糟**
- 结果：arXiv semantic location 76.5% → 73.5%（config B vs A），**单 case 偏置伤
  全局**。

## 根因：Week 4 的 ctx 锚定问题再次出现

30/32 ctx_delta = 0.000 的解释：**同 token 的 anchor 在 v1 里共享 `context_before` /
`context_after`**。因为 `_context_window` 用 `norm_page.find(selected)` 永远定位到
page 内第一次出现，导致同 page 同 token 的多个 anchor 拥有字面一致的 context 字串。
两个 anchor 和同一候选比 ctx 时，公式是：

    match.py._context_similarity(anchor, cand_before, cand_after)

`anchor` 的字段相同（bug），`cand_before/after` 相同（同一 slot），输出必然相同。
`ctx_delta = 0`，preemption 规则永远不可能触发。

Week 4 曾尝试修 `_context_window` 按 anchor 自身 quad 取 per-anchor ctx，结果
arXiv 92.3→89.7 / 56.4→48.7% 回退，当时判死刑后 revert。那次的教训是 "first-match
ctx anchoring is load-bearing"：虽然它让同 token anchor 看不清细节，但它的 flatten
效应让短 token 重复场景下 rank/layout 能主导，整体更稳。

**所以这是一个设计约束环**：
- Week 11 preemption 需要 per-anchor ctx 有区分度
- per-anchor ctx 需要 Week 4 的 `_context_window` 修复
- Week 4 的修复会整体让 arXiv 回退

## 对照 Week 11 spec 验收

| 验收 | 实际 | 评价 |
|---|---|---|
| arXiv semantic failures 4-6 → 至少少 1 | 6→4 at config D，但这 2 条来自 Week 10 concat+floor，Week 11 preemption 净贡献 0 | **不归功于 Week 11** |
| W2V semantic 不回退 | config B / D 都不回退（mean 保持 3，concat+floor 到 2） | ✅ |
| old oracle 不大退 | config D 仍 −13pp on arXiv（Week 10 已知，不是 Week 11 带来的） | ✅ Week 11 本身不新增 |
| default off 全绿 | 91 pytests ✓，v0.2.1 baseline Δ=0 ✓ | ✅ |

**Week 11 自身的净贡献：0 个 case 修好**。

## 结论

- Preemption 机制实现、守卫、开关都是对的 —— config B 干干净净 zero-change，说明
  机制不会在不该触发时乱动。
- 但 match.py 的 ctx 在同 token 场景下没有区分度（Week 4 锚定 bug 的设计副作用），
  让 preemption 永远没有真实可用的 ctx 优势去抢占。
- 强行降 `min_advantage` 到 0.05 会让更多 case 触发，但那些 case 的 ctx_delta 也接
  近 0，很可能随机抢占反而加大噪声（fb5847/644fcf 那例就是前车之鉴）。

**broken floor** (Week 9) 和 **ctx_aware_assign** (Week 11) 两条分配 / 分类层的
attack 都被 Week 4 的 ctx 锚定决定碰上了天花板。

## Week 12 候选

1. **分层 ctx**：保留 match.py 用 "first-match ctx"（保护 arXiv 整体不退），但给
   assignment 层单独算一份 per-anchor ctx（仅用于 preemption / floor 决策）。技术
   上就是再挖一次 Week 4 的坑，但只在 `_assign_one_to_one` 内部用，不污染主 score。
2. **换一条攻路**：放弃 assignment 层改进，转向 **"已选赢家回头对 ctx 重算一次"**
   —— `_classify` 阶段用 per-anchor ctx 判 status（broken vs relocated），不改
   assignment。这是 Week 9 的 broken floor 的精细化版本。
3. **先保留 v0.2.1 + 新基础设施，停这条线**。ctx_aware assignment / broken floor 已
   证伪两次，继续在同路线上投入边际收益在变小。转向 **降级信号处理**（fuzzy 候选
   的 ctx 权重？text≠1.0 时的 layout 依赖？）可能更有收益。

embedding 继续延后。

## 本轮 commits

- `pdfanno/diff/match.py`：`_ctx_aware_assign_params()` + `_assign_one_to_one`
  加 preemption pass + displaced 队列。
- 本报告
- 无 baseline 改动（default off → Δ=0）
