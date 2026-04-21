"""一键下载 arXiv PDF 对 + 打 highlight + 生成 ground truth —— 用于扩展真实 benchmark 集。

对每个 spec（arxiv_id + v1/v2 版本 + phrase 列表），下载两版 PDF、给 v1 加 highlight、
跑 ground_truth。默认输出到 `/tmp/pdfanno_benches/<name>/`。

用法：
    python -m benchmarks.fixtures.build_real_pairs           # 默认跑所有 spec
    python -m benchmarks.fixtures.build_real_pairs --only bert

选 phrase 时注意 **行边界**：紧排版 PDF（如 Word2Vec / Seq2Seq）的 `_selected_text`
会把相邻行字符 leak 进来，建议只用出现在行首/行尾或自成一行的 phrase。详见
`benchmarks/reports/week5_widen_bench.md` 的 "tight-layout 边界问题"。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.fixtures.annotate_real_pair import annotate
from benchmarks.tools.ground_truth import main as gt_main


@dataclass
class PairSpec:
    name: str
    arxiv_id: str
    v1_version: str
    v2_version: str
    phrases: list[str] = field(default_factory=list)


# phrase 列表的选取原则：短 token（测 repeat） + 中长 phrase（测结构化搬移） + 少量 unique token。
# 对紧排版 PDF，避免挑那些 quad 边缘离相邻行太近的 phrase（详见 module docstring）。
SPECS: list[PairSpec] = [
    PairSpec(
        name="arxiv_1706.03762",
        arxiv_id="1706.03762",
        v1_version="1",
        v2_version="5",
        # 1706.03762 的 39 条 highlight 由 Week 1 spike 手工策划，这里只是占位；
        # 真正的 v1_hl.pdf 已经在 /tmp/pdfanno_arxiv_spike/v1_hl.pdf 生成过。
        phrases=[],
    ),
    PairSpec(
        name="bert_1810.04805",
        arxiv_id="1810.04805",
        v1_version="1",
        v2_version="2",
        phrases=[
            "BERT",
            "Transformer",
            "masked",
            "Pre-training",
            "Fine-tuning",
            "WordPiece",
            "GLUE",
            "SQuAD",
            "attention",
            "Bidirectional",
            "Encoder",
            "softmax",
            "next sentence",
            "embedding",
        ],
    ),
    PairSpec(
        name="word2vec_1301.3781",
        arxiv_id="1301.3781",
        v1_version="1",
        v2_version="3",
        # 紧排版论文 —— 注意每条 phrase 都要在正文里能单独作为行内短语出现，
        # 让 quad 不跨行。_selected_text 的混合策略已经修过跨行 leak，
        # 但挑 phrase 时尽量避开包围空白太少的 token。
        phrases=[
            "continuous bag-of-words model",
            "Skip-gram",
            "vector representations of words",
            "neural network language model",
            "Semantic-Syntactic Word Relationship",
            "projection layer",
            "hierarchical softmax",
            "large amount of data",
        ],
    ),
    PairSpec(
        name="seq2seq_1409.3215",
        arxiv_id="1409.3215",
        v1_version="1",
        v2_version="3",
        phrases=[
            "Sequence to Sequence",
            "Neural Network",
            "BLEU score",
            "Long Short-Term Memory",
            "recurrent neural network",
            "beam search",
            "English to French",
            "Deep Learning",
        ],
    ),
]


def build(spec: PairSpec, out_root: Path) -> dict:
    d = out_root / spec.name
    d.mkdir(parents=True, exist_ok=True)
    v1_url = f"https://arxiv.org/pdf/{spec.arxiv_id}v{spec.v1_version}.pdf"
    v2_url = f"https://arxiv.org/pdf/{spec.arxiv_id}v{spec.v2_version}.pdf"
    v1_path = d / "v1.pdf"
    v2_path = d / "v2.pdf"
    v1_hl_path = d / "v1_hl.pdf"
    gt_path = d / "gt.json"

    _curl(v1_url, v1_path)
    _curl(v2_url, v2_path)

    if not spec.phrases:
        # Week 1 spike 级别的 fixture —— v1_hl 手工生成，本脚本不重建。
        return {
            "name": spec.name,
            "v1": v1_path,
            "v2": v2_path,
            "v1_hl": None,
            "gt": None,
            "note": "no phrases; skip annotation & ground_truth",
        }

    ann_result = annotate(v1_path, v1_hl_path, spec.phrases)

    # 用 sys.argv 驱动 ground_truth.main —— 避开它的 argparse。
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "ground_truth",
            str(v1_hl_path),
            str(v2_path),
            "--out",
            str(gt_path),
        ]
        gt_main()
    finally:
        sys.argv = old_argv

    return {
        "name": spec.name,
        "v1": v1_path,
        "v2": v2_path,
        "v1_hl": v1_hl_path,
        "gt": gt_path,
        "annotate": ann_result,
    }


def _curl(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 10_000:
        return  # 已存在的合法 PDF 直接复用
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["curl", "-sSL", "--max-time", "60", "-o", str(dst), url],
        check=True,
    )
    head = dst.read_bytes()[:4]
    if head != b"%PDF":
        raise RuntimeError(f"{url} -> {dst} 不是合法 PDF (head={head!r})")


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-root", type=Path, default=Path("/tmp/pdfanno_benches"))
    ap.add_argument("--only", default=None, help="只跑指定的 spec.name")
    args = ap.parse_args()

    for spec in SPECS:
        if args.only and spec.name != args.only:
            continue
        info = build(spec, args.out_root)
        print(f"\n=== {spec.name} ===")
        for k, v in info.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
