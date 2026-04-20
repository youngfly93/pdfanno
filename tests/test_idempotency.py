"""幂等与旋转 roundtrip 的稳定 id —— plan.md §6.3 + §14 任务 7。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode
from pdfanno.pdf_core.annotations import existing_pdfanno_ids
from pdfanno.pdf_core.document import compute_doc_id, open_pdf
from pdfanno.rules.match import plan_for_query

runner = CliRunner()


def test_rerun_highlight_produces_zero_new_annotations(simple_pdf: Path, tmp_path: Path) -> None:
    """在同一 output 上重复执行同 query，第二次应 annotations_created=0。"""

    out = tmp_path / "out.pdf"
    r1 = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out), "--json"])
    assert r1.exit_code == ExitCode.SUCCESS, r1.output
    p1 = json.loads(r1.stdout)
    assert p1["annotations_created"] >= 1

    # 对第一次的 output 作为新输入再跑一次同 query。
    out2 = tmp_path / "out2.pdf"
    r2 = runner.invoke(app, ["highlight", str(out), "transformer", "-o", str(out2), "--json"])
    assert r2.exit_code == ExitCode.SUCCESS, r2.output
    p2 = json.loads(r2.stdout)
    assert p2["annotations_created"] == 0, "第二次执行不应新增 annotation"
    assert p2["matches"] == p1["matches"]


def _roundtrip_ids(src: Path, tmp: Path, query: str = "transformer") -> list[list[str]]:
    """三次生成 plan 应得到同样的 annotation_id 集合：

    1. 打开 src，对其规划
    2. 高亮并另存到 A
    3. 打开 A，再次规划（含幂等 id）
    4. 高亮（期望 0 新增）并另存到 B
    5. 打开 B，再次规划

    返回三次规划的 annotation_id 列表。
    """

    local_src = tmp / "src.pdf"
    shutil.copyfile(src, local_src)

    def plan_ids(path: Path) -> list[str]:
        with open_pdf(path) as doc:
            doc_id = compute_doc_id(doc, path)
            plan = plan_for_query(doc, doc_id, query=query)
        return [a.annotation_id for a in plan.annotations]

    ids_0 = plan_ids(local_src)

    out_a = tmp / "a.pdf"
    r_a = runner.invoke(app, ["highlight", str(local_src), query, "-o", str(out_a), "--json"])
    assert r_a.exit_code == ExitCode.SUCCESS, r_a.output
    ids_1 = plan_ids(out_a)

    out_b = tmp / "b.pdf"
    r_b = runner.invoke(app, ["highlight", str(out_a), query, "-o", str(out_b), "--json"])
    assert r_b.exit_code == ExitCode.SUCCESS, r_b.output
    # 第二次的 annotations_created 必须为 0（roundtrip 幂等）
    assert json.loads(r_b.stdout)["annotations_created"] == 0
    ids_2 = plan_ids(out_b)

    return [ids_0, ids_1, ids_2]


def test_simple_pdf_annotation_id_stable_through_roundtrip(
    simple_pdf: Path, tmp_path: Path
) -> None:
    ids_0, ids_1, ids_2 = _roundtrip_ids(simple_pdf, tmp_path)
    assert sorted(ids_0) == sorted(ids_1) == sorted(ids_2)


def test_rotated_90_annotation_id_stable_through_roundtrip(
    rotated_90_pdf: Path, tmp_path: Path
) -> None:
    ids_0, ids_1, ids_2 = _roundtrip_ids(rotated_90_pdf, tmp_path)
    assert sorted(ids_0) == sorted(ids_1) == sorted(ids_2)
    assert ids_0, "rotated_90 fixture 必须至少命中一条 transformer"


def test_rotated_270_annotation_id_stable_through_roundtrip(
    rotated_270_pdf: Path, tmp_path: Path
) -> None:
    ids_0, ids_1, ids_2 = _roundtrip_ids(rotated_270_pdf, tmp_path)
    assert sorted(ids_0) == sorted(ids_1) == sorted(ids_2)
    assert ids_0, "rotated_270 fixture 必须至少命中一条 transformer"


def test_existing_pdfanno_ids_after_write(simple_pdf: Path, tmp_path: Path) -> None:
    """写完 highlight 后，read_annotations 能通过 /NM 回读出所有 annotation_id。"""

    out = tmp_path / "out.pdf"
    r = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out), "--json"])
    assert r.exit_code == ExitCode.SUCCESS, r.output

    # 非 dry-run 不携带 plan；跑 dry-run 拿 planned annotation_id 做对比。
    dry = runner.invoke(
        app, ["highlight", str(simple_pdf), "transformer", "-o", str(out), "--dry-run", "--json"]
    )
    planned_ids = {
        a["annotation_id"] for a in json.loads(dry.stdout)["data"]["plan"]["annotations"]
    }

    with open_pdf(out) as doc:
        read_ids = existing_pdfanno_ids(doc)

    assert planned_ids == read_ids, "写入后回读的 /NM 必须与 plan 一致"
