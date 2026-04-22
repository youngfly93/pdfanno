# Week 10: ctx-mode 对齐 + broken floor 矩阵 sweep

## 机制

`pdfanno/diff/context.py` 抽出共享 `context_similarity(before_a, after_a, before_b,
after_b, mode=...)`，两种 mode：

- **`mean`**（默认）：`(before_ratio + after_ratio) / 2` —— v0.2.1 以来的 matcher 行为。
- **`concat`**：`SequenceMatcher(before_a || after_a, before_b || after_b).ratio()`
  —— 与 `ground_truth_semantic` 相同算法（之前散在 oracle 里，现在共用 helper）。

开关：
- `PDFANNO_CTX_SIM_MODE=concat` 切 matcher 到 concat。
- `PDFANNO_CTX_SIM_MODE=mean` 或不设为默认。
- oracle 端 **固定** concat（不走环境变量，那是 oracle 的定义）。

Oracle 重构：`_local_ctx` 改为返回 `(before, after)` 分离，`concat` 拼接交给共享
helper。重生成的 3 份 semantic GT 与存档版本字面一致（`summary` 完全相同），确认
只是 refactor 不改语义。

## 全矩阵 sweep

注：`fail` = eval 报告里的 failure case 条数（status 错 OR location 错）。

### mode=mean（与 v0.2.1 行为一致；Week 9 已跑过）

| floor | arxiv sem | arxiv old | bert sem | bert old | w2v sem | w2v old |
|---:|---|---|---|---|---|---|
| 0.00 | 84.6% / 6 / loc 76.5% | 92.3% / 11 / 56.4% | 92.9% / 1 | 100% / 0 | 80.0% / 3 / 100% | 86.7% / 4 / 57.1% |
| 0.05 | 84.6% / 6 / 78.8% | 89.7% / 11 / 57.9% | 92.9% / 1 | 100% / 0 | **93.3% / 1 / 100%** | 80.0% / 5 |
| 0.10 | 79.5% / 8 / 86.7% | 79.5% / 15 | 100% / 0 | 92.9% / 1 | **100% / 0 / 100%** | 73.3% / 6 |
| 0.15 | 82.1% / 7 / 86.2% | 71.8% / 18 | 100% / 0 | 92.9% / 1 | 100% / 0 | 73.3% / 6 |

### mode=concat（与 oracle ctx 对齐）

| floor | arxiv sem | arxiv old | bert sem | bert old | w2v sem | w2v old |
|---:|---|---|---|---|---|---|
| 0.00 | 84.6% / 7 / 85.3% | 92.3% / 10 / 53.8% | 92.9% / 1 | 100% / 0 | 73.3% / 4 / 90.9% | 100% / 2 / 71.4% |
| 0.05 | 84.6% / 6 / 90.6% | 84.6% / 12 | 92.9% / 1 | 100% / 0 | 80.0% / 3 / 90.9% | 93.3% / 3 |
| 0.10 | **89.7% / 4 / 90.6%** | 79.5% / 14 / 50.0% | 100% / 0 | 92.9% / 1 | 86.7% / 2 / 90.9% | 86.7% / 4 |
| 0.15 | **89.7% / 4 / 90.6%** | 79.5% / 14 | 100% / 0 | 92.9% / 1 | 86.7% / 2 / 90.9% | 86.7% / 4 |

## 核心发现

1. **concat 模式确实让 arXiv 的 broken floor 起作用**：
   - arXiv semantic：84.6% / 6 fail → **89.7% / 4 fail** at concat+floor=0.10（baseline 之外首次在 arXiv 上有可验证的 **2 条真失败修复**）
   - arXiv semantic location：76.5% → **90.6%**（+14pp）
   - 这是 Week 7-9 以来 arXiv 第一次在任何 configuration 下拿到净正收益

2. **但 concat 把 W2V 打回去了**：
   - W2V baseline 在 concat 下从 80% 掉到 73.3% (semantic) / 100% 掉到 73.3% (old)
   - mean 模式 floor=0.05 的 W2V 成绩（93.3% / 1 fail）在 concat 下需要 floor=0.10 才能达到（86.7% / 2 fail）
   - 原因：W2V 的 broken-truth case ctx 在 mean 下 ≈ 0（易抓），在 concat 下可能 0.05-0.1 区间（需要更高 floor）

3. **old oracle 继续被惩罚**：
   - concat+floor=0.10 下 arXiv old 92.3 → 79.5（−13pp），和 mean 模式差不多
   - 这不是 mode 的问题，是 old oracle 本身和 broken-floor 的哲学不兼容

## 验收对照（Week 10 目标）

| 目标 | mean best | concat best | 评价 |
|---|---|---|---|
| concat + floor 让 W2V 保持 3 → ≤ 1 | 3→0 (floor=0.10) | 3→2 (floor=0.10) | **mean 胜** |
| arXiv semantic failures 6 → ≤ 4（最好 ≤3） | 6→6 | **6→4 (floor=0.10)** | **concat 胜**（刚好摸到 ≤4 门槛） |
| old oracle 不大退 | 差不多 −13pp | 差不多 −13pp | 都不满足 |
| default mode 不变全 Δ=0 | MET | MET | ✓ |

**第一次 arXiv 有真正改进**，但 **没有单一 (mode, floor) 同时满足三个 benchmark 的验收**。

## 诊断：floor 是"方向对但力度小"的 lever

把数据摆开看：
- concat 模式比 mean 模式，在 arXiv 上 **修对更多（fix）但不更破坏（break 差不多）**，净正。
- concat 模式在 W2V 上比 mean 模式 **破坏更多（break）但 fix 不够多**，净负。
- 没有单一 floor 能让 arXiv 和 W2V 的最优点重合。

这不是"floor 机制错了"，也不是"ctx 度量错了"——是 **broken floor 只是一个"降级阈值"**，
它能把"ctx 弱 + 确实该 broken"的案例修好（W2V mean 的本职），但也会在"ctx 弱但
其实是 relocated"的边界带误伤（arXiv concat 的副作用）。

## 结论：Week 10 目标部分达成，broken floor 不再作为主线

- **refactor 成功**：`context.py` 共享 helper，oracle 和 matcher 不再各说各话。Week
  11 之后任何 ctx 相关改动都在同一套算法上调。
- **concat mode 找到 arXiv 上 6→4 的甜蜜点**，第一次真实改进
  `anc_bc48e155 Scaled Dot-Product` / `anc_e3ba2da8 BLEU` 这类的 "own best ctx 虽然
  存在但不够高" 案例。
- 但 W2V 被 concat 模式天然拖累，不满足"W2V 3→≤1"。
- **broken floor 作为单一 lever 的探索到此结束**。继续调参会把 W2V 和 arXiv 拉扯，
  没有意义。

## Week 11 候选

Week 10 spec 的判决分支成立："broken floor 不是单纯 ctx 度量问题，需要进入
assignment / tie-breaking 层。"

优先级：

1. **Assignment 层的 ctx-aware dedup**（NEW #1）—— Week 9 的数据显示 arXiv 5 条
   "pred=relocated vs gt=broken" 失败，其中几条（`anc_bc48e155 ctx=0.576`）
   "自己的 best candidate 本身 ctx 不差，是被其他 anchor 抢走，只能退到 ctx=0.07 的
   次优"。这是 Week 7 Hungarian-in-group 想解决但姿势不对的问题。新姿势：
   在 greedy 分配时，对同 token 组内用 ctx_sim 作 tie-breaker（score 相近时 ctx
   高的优先认领），组间保持 greedy 避免 Week 2 B 的全局 sum-max 陷阱。
2. **within-page ctx tie-breaking** —— 仍保留作 #2，解剩下的 `anc_68ab8fb0` 那一类。
3. **concat mode 升为默认？** —— 等 assignment 层改完后回头看。现在 arXiv +3 / W2V
   -1 的 net 不够干净。

embedding 继续延后。

## 本轮 commits

- `pdfanno/diff/context.py` —— 新 helper，`mean` + `concat` 两 mode
- `pdfanno/diff/match.py::_context_similarity` —— 委托到 helper，读
  `PDFANNO_CTX_SIM_MODE`
- `benchmarks/tools/ground_truth_semantic.py::_local_ctx` —— 返回分离的
  (before, after)；调用 helper 固定 concat
- 本报告
- 无 baseline 改动（default mode=mean + floor=0 时所有基线 Δ=0）
