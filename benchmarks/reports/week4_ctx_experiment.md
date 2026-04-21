# Week 4 / v0.2.1: `_context_window` anchoring experiment

## Hypothesis (from v0.2.0 post-mortem)

> v0.2.0 的 `_context_window` 用 `norm_page.find(selected)` 定位 anchor 的
> 上下文，这总是返回 page 中 **第一次** 出现的位置。当 anchor 本身是某个重复
> 短 token 的 rank-k（k > 0）实例时，其保存的 ctx 来自 rank-0 的位置 —— 系统
> 性偏差。修这个 "bug" 应该让 cross-section fixture 翻盘，并改善 arXiv 的
> 56.4% location accuracy。

## Experiment

改动（单文件）：`pdfanno/diff/anchors.py`

- 新增 `_anchor_occurrence_index(page, selected, anchor_quads) -> int`：用 anchor
  quad 中心找到 `page.search_for(selected)` 返回 quads 中最近的那一个的索引。
- `_context_window(page_text, selected, *, occurrence_index=0)` 改为跳到第
  `occurrence_index` 次 `find` 再抽 ±300 字符。
- `extract_anchors` 调用链传入从 quad 推算的 `occurrence_index`。

测试行为：91 pytest 全绿，ruff 干净。基础逻辑正确（单元行为与预期一致）。

## Result

| benchmark | v0.2.0 baseline | ctx-fix | Δ |
|---|---:|---:|---:|
| arXiv status | 92.3% | **89.7%** | **−2.6pp** |
| arXiv location | 56.4% | **48.7%** | **−7.7pp** |
| arXiv failures | 11 | **12** | +1 |
| revised status | 88.5% | 88.5% | 0 |
| revised location | 100% | 100% | 0 |
| cross-section fixture | WRONG 胜 | WRONG 胜 | 0 |

**hypothesis 同时在 arXiv 和 cross-section fixture 上被证伪**。

## 根因分析

1. **cross-section fixture 没翻** —— 因为 fixture 里 anchor 是 rank 0 of 1，
   `occurrence_index=0`，新旧 `_context_window` 行为完全等价。fixture 的 ctx_sim
   不对称来自 **候选位置**（WRONG 在 v2 页首窗口长、CORRECT 在页尾窗口短），
   而 `extract_anchors` 层的 ctx 修改触达不到这个问题。
2. **arXiv 反而倒退** —— arXiv 里被高亮的短 token（`BLEU` / `WMT 2014` /
   `Multi-Head Attention`）大多是某个 section 内的第 k 次出现（k > 0）。v0.2.0
   的 "anchor 取第一次出现 ctx" **意外地** 起到了 flatten 作用：所有同 token 的
   anchor 共享同一份 ctx，让 ctx 信号对短 token 变得 "钝"，layout/rank 就能主导
   判断。ctx-fix 后每个 anchor 的 ctx 变得更独特，但 v2 发生结构重构后，anchor
   真实位置 ctx 与目标位置 ctx 不再对齐，反而让错误候选的局部 ctx 匹配更高。
   具体失败案例：`anc_68ab8fb0 Multi-Head Attention`，v0.2.0 judged relocated@p3
   (gt: relocated@p2)，v0.2.1-exp judged preserved@p3（gt: relocated@p2）。位置
   准确率下降 3 例（另两例为同页 y-shift 越过 15pt 阈值）。

## 决策

**revert `_context_window` 改动**（HEAD 撤回到 "首次出现 ctx" 行为）。保留：

- `_anchor_occurrence_index` 被移除（没有调用方）。
- `_context_window` 恢复 v0.2.0 签名，注释里记录这次实验和证伪结论。
- `benchmarks/baselines/v0.2.0.json` + `benchmarks/tools/compare_baseline.py` **保留** ——
  这层回归防线让这次实验能在 30 秒内被证伪。以后每次改动都先跑 compare，
  Δ 方向不对就立即丢弃。

## 下一步思考

1. **cross-section fixture 的翻盘** 需要解决 **候选位置窗口不对称** 问题，而非
   anchor ctx 的问题。可能方向：用 SequenceMatcher 长度归一化，或让 ctx 窗口在
   文档边界截断时填 padding。这是 ctx_sim 本身的 re-design，比想象中大。
2. **arXiv 的 location accuracy 痛点**（56.4%）本质是 "同页大幅位移 + 短 token
   重复"。10 条 BLEU/WMT/Multi-Head 失败全是这一类。简单的 ctx 修不掉；可能需要
   单独的 "候选位置偏置" 信号（比如 v1 anchor y 位置在 page 上的归一化值 vs v2
   candidate y 的归一化值，配合 section path 做 within-section normalization）。
3. **不要** 把 embedding 作为下一步。这次实验说明：在 ctx_sim 本身没调准之前，
   加更复杂的语义信号只会让归因更难。

## v0.2.1 的状态

原计划 v0.2.1 = "修 ctx + 摘 section_sim 实验标签"。实验证伪后两个目标都落空：

- ctx 修改被 revert。
- section_sim 仍是 experimental（cross-section fixture 没翻过来）。

**本轮实际交付物**：
- benchmark baseline 快照基础设施（`benchmarks/baselines/v0.2.0.json` + 
  `compare_baseline.py`）。
- 本实验报告，把 "first-match ctx anchoring is load-bearing" 这个反直觉发现钉下来。

**是否发 v0.2.1 tag**：建议 **不发**。没有可对外宣传的改进；但 commit 保留，因为
基础设施对后续工作有价值。下一次数字真正涨了再发 0.2.1（或 0.3.0，按改动量）。
