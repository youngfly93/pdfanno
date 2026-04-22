"""v0.2 Week 1 PoC 测试：diff CLI 对 3 对 fixture 的期望行为。

10 条注释覆盖三档：identical -> 全 preserved；reordered -> 部分 relocated；
partial -> 被删的那条 broken。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pdfanno.cli import app
from pdfanno.exit_codes import ExitCode

runner = CliRunner()


def _run_diff(old: Path, new: Path) -> dict:
    result = runner.invoke(app, ["diff", str(old), str(new), "--json"])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    return json.loads(result.stdout)


def test_diff_identical_all_preserved(pair_identical) -> None:
    v1, v2 = pair_identical
    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 4
    assert s["preserved"] == 4
    assert s["relocated"] == 0
    assert s["broken"] == 0
    for r in report["results"]:
        assert r["status"] == "preserved"
        assert r["confidence"] == 1.0
        assert r["new_anchor"]["page_index"] == r["old_anchor"]["page_index"]


def test_diff_reordered_produces_relocated(pair_reordered) -> None:
    v1, v2 = pair_reordered
    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 3
    assert s["relocated"] >= 1, "reordered fixture must surface at least one relocated"
    assert s["broken"] == 0

    # 具体：LineA5 从 page 0 被挤到 page 1
    relocated = [r for r in report["results"] if r["status"] == "relocated"]
    assert any(
        "LineA5" in r["old_anchor"]["selected_text"]
        and r["new_anchor"]["page_index"] != r["old_anchor"]["page_index"]
        for r in relocated
    )


def test_diff_partial_deletion_is_broken(pair_partial) -> None:
    """v2 删除了 BetaUnique → 该 highlight 应 broken；Gamma 因为上移一行应 relocated。

    Week 2 quad proximity 检查：同页同文本但位置偏移 > 15 pt 视为 relocated
    (同一 token 的不同实例 / 或同一实例被挤到新位置)；不再糊成 preserved。
    """

    v1, v2 = pair_partial
    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 3
    assert s["broken"] == 1
    # Alpha 在首行没动 → preserved；Gamma 因 Beta 删除上移 ≈ 24 pt → relocated（同页）。
    assert s["preserved"] == 1
    assert s["relocated"] == 1

    broken = [r for r in report["results"] if r["status"] == "broken"]
    assert len(broken) == 1
    assert "BetaUnique" in broken[0]["old_anchor"]["selected_text"]
    assert broken[0]["confidence"] == 0.0
    assert broken[0]["new_anchor"] is None
    assert broken[0]["review_required"] is True

    preserved = [r for r in report["results"] if r["status"] == "preserved"]
    assert len(preserved) == 1
    assert "AlphaUnique" in preserved[0]["old_anchor"]["selected_text"]

    relocated = [r for r in report["results"] if r["status"] == "relocated"]
    assert len(relocated) == 1
    assert "GammaUnique" in relocated[0]["old_anchor"]["selected_text"]
    # 同页 relocated：page_delta 为 0
    assert relocated[0]["match_reason"]["page_delta"] == 0


def test_diff_case_only_edit_is_changed(tmp_path: Path) -> None:
    """PyMuPDF search_for is ASCII case-insensitive; diff must not call this preserved."""

    import pymupdf

    v1 = tmp_path / "case_v1.pdf"
    v2 = tmp_path / "case_v2.pdf"
    old_sentence = "Case Only Biomarker ABC remains in the abstract."
    new_sentence = "case only biomarker abc remains in the abstract."

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), old_sentence, fontsize=12)
    annot = page.add_highlight_annot(page.search_for(old_sentence, quads=True))
    annot.update()
    doc.save(v1)
    doc.close()

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), new_sentence, fontsize=12)
    doc.save(v2)
    doc.close()

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] == 1
    assert report["summary"]["preserved"] == 0
    assert report["summary"]["changed"] == 1
    result = report["results"][0]
    assert result["status"] == "changed"
    assert result["new_anchor"]["page_index"] == 0
    assert result["new_anchor"]["matched_text"] == new_sentence


def test_diff_non_text_annotations_are_unsupported(tmp_path: Path) -> None:
    """Text note / square annotations are real anchors, but not migratable text coverage."""

    import pymupdf

    v1 = tmp_path / "unsupported_v1.pdf"
    v2 = tmp_path / "unsupported_v2.pdf"

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "A page with unsupported annotations.", fontsize=12)
    note = page.add_text_annot((90, 150), "sticky body")
    note.set_info(content="TEXT_NOTE")
    note.update()
    square = page.add_rect_annot(pymupdf.Rect(72, 190, 180, 240))
    square.set_info(content="SQUARE_NOTE")
    square.update()
    doc.save(v1)
    doc.close()

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "A page with unsupported annotations.", fontsize=12)
    doc.save(v2)
    doc.close()

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] == 2
    assert report["summary"]["unsupported"] == 2
    assert report["summary"]["broken"] == 0
    assert {r["status"] for r in report["results"]} == {"unsupported"}
    assert all(r["review_required"] for r in report["results"])


def test_diff_schema_version_is_2(pair_identical) -> None:
    v1, v2 = pair_identical
    report = _run_diff(v1, v2)
    assert report["schema_version"] == 2
    assert "old_doc_id" in report
    assert "new_doc_id" in report
    assert report["old_doc_id"] != report["new_doc_id"] or len(report["results"]) == 0


def test_diff_writes_to_diff_out_file(pair_identical, tmp_path: Path) -> None:
    v1, v2 = pair_identical
    out = tmp_path / "diff.json"
    result = runner.invoke(app, ["diff", str(v1), str(v2), "--diff-out", str(out)])
    assert result.exit_code == ExitCode.SUCCESS, result.output
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["preserved"] == 4


def test_diff_total_annotations_matches_ten(pair_identical, pair_reordered, pair_partial) -> None:
    """PRD §10 Week 1 deliverable: ≥10 条注释的 diff JSON。"""

    total = 0
    for v1, v2 in (pair_identical, pair_reordered, pair_partial):
        report = _run_diff(v1, v2)
        total += report["summary"]["total_annotations"]
    assert total == 10


def test_diff_missing_input_returns_input_error(tmp_path: Path, simple_pdf: Path) -> None:
    ghost = tmp_path / "nope.pdf"
    result = runner.invoke(app, ["diff", str(ghost), str(simple_pdf)])
    assert result.exit_code == ExitCode.INPUT_ERROR


def test_diff_respects_backward_compat_exit_codes(simple_pdf: Path, tmp_path: Path) -> None:
    """新 diff 命令不得破坏 v0.1.x 已有 highlight 命令的退出码语义。"""

    out = tmp_path / "o.pdf"
    r = runner.invoke(app, ["highlight", str(simple_pdf), "transformer", "-o", str(out)])
    assert r.exit_code == ExitCode.SUCCESS


def test_diff_bad_pdf_returns_processing_error(simple_pdf: Path, tmp_path: Path) -> None:
    """坏 PDF（非 PDF 字节）走 ExitCode.PROCESSING_ERROR=4，不是未捕获 exit 1。"""

    bad = tmp_path / "not_a_pdf.pdf"
    bad.write_text("this is not a pdf", encoding="utf-8")
    r = runner.invoke(app, ["diff", str(bad), str(simple_pdf)])
    assert r.exit_code == ExitCode.PROCESSING_ERROR, r.output

    r2 = runner.invoke(app, ["diff", str(simple_pdf), str(bad)])
    assert r2.exit_code == ExitCode.PROCESSING_ERROR, r2.output


def test_diff_negative_page_window_is_usage_error(
    pair_identical,
) -> None:
    v1, v2 = pair_identical
    r = runner.invoke(app, ["diff", str(v1), str(v2), "--page-window", "-1"])
    assert r.exit_code == ExitCode.USAGE_ERROR


def test_page_window_is_not_module_global(pair_identical) -> None:
    """--page-window 必须作为函数参数传递，不能改 module-level 常量。"""

    from pdfanno.diff import match as match_mod

    before = match_mod.DEFAULT_PAGE_WINDOW

    v1, v2 = pair_identical
    # 跑一次使用非默认值；之后 module 常量应保持不变
    r = runner.invoke(app, ["diff", str(v1), str(v2), "--page-window", "7"])
    assert r.exit_code == ExitCode.SUCCESS, r.output

    after = match_mod.DEFAULT_PAGE_WINDOW
    assert before == after, "--page-window 不应污染 module-level 常量"
    # 旧代码会留 PAGE_WINDOW 属性；新代码只有 DEFAULT_PAGE_WINDOW
    assert not hasattr(match_mod, "PAGE_WINDOW"), (
        "match module 不应再有可变的 PAGE_WINDOW 全局；参数通过 diff_against(page_window=...) 传递"
    )


# ----- Week 2 H1: candidate pool + 1:1 + quad 回填 -----


def test_exact_matches_populate_new_anchor_quads(pair_reordered) -> None:
    """exact 命中必须把 new_anchor.quads 填上（供 Week 4-5 migrate 写回 PDF）。"""

    v1, v2 = pair_reordered
    report = _run_diff(v1, v2)
    exact_hits = [r for r in report["results"] if r["status"] in ("preserved", "relocated")]
    assert exact_hits, "fixture should produce at least one exact hit"
    for r in exact_hits:
        # Week 2: exact 匹配（text_sim=1.0）必须带 quads
        if r["match_reason"]["selected_text_similarity"] == 1.0:
            quads = r["new_anchor"]["quads"]
            assert quads, f"exact match must populate new_anchor.quads, got {r}"
            assert len(quads[0]) == 8, "quad must be 8 floats (ul,ur,ll,lr * x,y)"


def test_multi_instance_assignment_is_one_to_one(tmp_path: Path) -> None:
    """同文本在同页出现 N 次 → N 条 v1 anchor 必须映射到 N 个不同 v2 位置。

    直接触达 spike 发现的 "residual connection" bug：之前所有 3 条都指向首个命中。
    """

    import pymupdf

    v1 = tmp_path / "v1.pdf"
    v2 = tmp_path / "v2.pdf"

    # v1：文本 + 三条 highlight 一次性写入（避免重开后 annot unbind 的问题）。
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "unique_token in the first position")
    page.insert_text((72, 130), "unique_token in the second position")
    page.insert_text((72, 160), "unique_token in the third position")
    for q in page.search_for("unique_token", quads=True):
        annot = page.add_highlight_annot(q)
        annot.update()
    doc.save(str(v1))
    doc.close()

    # v2：文本内容相同，无注释。
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "unique_token in the first position")
    page.insert_text((72, 130), "unique_token in the second position")
    page.insert_text((72, 160), "unique_token in the third position")
    doc.save(str(v2))
    doc.close()

    report = _run_diff(v1, v2)
    s = report["summary"]
    assert s["total_annotations"] == 3

    # 关键断言：三条 anchor 的 new_anchor 必须指向 **3 个不同的** quad 中心点。
    centers = []
    for r in report["results"]:
        assert r["new_anchor"] is not None, r
        q = r["new_anchor"]["quads"][0]
        cx = round((q[0] + q[4]) / 2, 1)
        cy = round((q[1] + q[5]) / 2, 1)
        centers.append((cx, cy))
    assert len(set(centers)) == 3, (
        f"1:1 assignment broken: 3 anchors mapped to only {len(set(centers))} "
        f"unique positions: {centers}"
    )


def test_context_similarity_discriminates_same_token_occurrences(tmp_path: Path) -> None:
    """同 token 在 v2 多页出现时，context 相近那一条应拿更高 confidence。

    验证 context_similarity 被正确计入 score：
    - v1: "widget" 在 "the widget is red" 句中，page 0。
    - v2: "widget" 在 page 0 有 "the widget is red"（真正原位置）+ page 2 有无关
      上下文的 "widget" 孤词。两条候选 text_similarity 都是 1.0，但 context
      差异显著 —— anchor 应配到 page 0（context 相似的那条）。
    """

    import pymupdf

    v1 = tmp_path / "v1.pdf"
    v2 = tmp_path / "v2.pdf"

    # v1: 单页，"the widget is red"，高亮 "widget"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "intro text before the widget")
    page.insert_text((72, 130), "the widget is red and works well")
    page.insert_text((72, 160), "more text after the widget for context")
    for q in page.search_for("widget", quads=True)[:1]:
        annot = page.add_highlight_annot(q)
        annot.update()
    doc.save(str(v1))
    doc.close()

    # v2: page 0 保留 "the widget is red"（原 context）；page 2 有完全无关上下文的 widget。
    doc = pymupdf.open()
    p0 = doc.new_page(width=595, height=842)
    p0.insert_text((72, 100), "intro text before the widget")
    p0.insert_text((72, 130), "the widget is red and works well")
    p0.insert_text((72, 160), "more text after the widget for context")
    doc.new_page(width=595, height=842)  # 空 page 1
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 100), "unrelated page about lasers and rockets")
    p2.insert_text((72, 130), "another widget appears in alien context")
    p2.insert_text((72, 160), "nothing about the original intro or red colors")
    doc.save(str(v2))
    doc.close()

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] >= 1
    r = report["results"][0]
    # 应该配到 page 0 而不是 page 2（因为 context 更匹配）
    assert r["new_anchor"]["page_index"] == 0, (
        f"context_similarity 应该把 anchor 拉回 page 0，实际到了 p{r['new_anchor']['page_index']}"
    )
    # context_similarity 真的被填了，不是 0
    assert r["match_reason"]["context_similarity"] > 0.0


def test_changed_status_for_edited_in_place_text(tmp_path: Path) -> None:
    """同位置的文本被编辑 → fuzzy text + 强 context → 应判 changed 而非 relocated。

    v1: "The kinase activity was measured at 37 degrees."
    v2: 同 page 同位置，句子改成 "The kinase activity was measured at 42 degrees."
    anchor 选中原句子（包括 37）。
    """

    import pymupdf

    v1 = tmp_path / "v1.pdf"
    v2 = tmp_path / "v2.pdf"

    # 长句让单字符编辑后 text_similarity 仍在 ≥ 0.85 的 fuzzy 候选区间。
    old_sentence = (
        "The kinase activity was carefully measured at 37 degrees Celsius "
        "under sterile conditions in triplicate."
    )
    new_sentence = (
        "The kinase activity was carefully measured at 42 degrees Celsius "
        "under sterile conditions in triplicate."
    )

    # v1
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Before the experiment we prepared buffer.")
    page.insert_text((72, 110), old_sentence)
    page.insert_text((72, 140), "After the reaction we quantified product.")
    for q in page.search_for(old_sentence, quads=True):
        annot = page.add_highlight_annot(q)
        annot.update()
    doc.save(str(v1))
    doc.close()

    # v2
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Before the experiment we prepared buffer.")
    page.insert_text((72, 110), new_sentence)
    page.insert_text((72, 140), "After the reaction we quantified product.")
    doc.save(str(v2))
    doc.close()

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] == 1
    r = report["results"][0]
    assert r["status"] == "changed", (
        f"text 改动+context 不变 应判 changed, 实际 {r['status']}: {r['message']}"
    )
    assert r["review_required"] is True
    # text_sim 应在 [0.60, 1.0)
    ts = r["match_reason"]["selected_text_similarity"]
    assert 0.60 <= ts < 1.0, f"text_similarity 不在 fuzzy 段: {ts}"
    # context_sim 应 >= 0.70 阈值
    cs = r["match_reason"]["context_similarity"]
    assert cs >= 0.70, f"context 应该高，实际 {cs}"


def test_fuzzy_changed_does_not_reuse_exact_slot_for_deleted_near_duplicate(
    tmp_path: Path,
) -> None:
    """Deleted near-duplicate text must not steal a surviving exact anchor's slot.

    Old has anchors 00..19. New keeps only 00..13. The deleted 14..19 differ
    from surviving lines by two digits, so fuzzy text similarity is ~0.99. They
    should still be broken because every fuzzy target is already owned by a real
    exact match.
    """

    import pymupdf

    v1 = tmp_path / "v1.pdf"
    v2 = tmp_path / "v2.pdf"
    total = 20
    kept = 14

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i in range(total):
        text = f"Near duplicate anchor {i:02d} remains individually identifiable."
        y = 80 + i * 28
        page.insert_text((72, y), text, fontsize=10)
        annot = page.add_highlight_annot(page.search_for(text, quads=True))
        annot.update()
    doc.save(v1)
    doc.close()

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i in range(kept):
        text = f"Near duplicate anchor {i:02d} remains individually identifiable."
        y = 80 + i * 28
        page.insert_text((72, y), text, fontsize=10)
    doc.save(v2)
    doc.close()

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] == total
    assert report["summary"]["preserved"] == kept
    assert report["summary"]["changed"] == 0
    assert report["summary"]["broken"] == total - kept

    broken_texts = [
        r["old_anchor"]["selected_text"] for r in report["results"] if r["status"] == "broken"
    ]
    assert {f"{i:02d}" for i in range(kept, total)} == {
        text.split("anchor ", 1)[1][:2] for text in broken_texts
    }


def test_layout_score_picks_kth_occurrence_on_same_page(tmp_path: Path) -> None:
    """同页 3 次 'pivot' 出现，v1 高亮的是中间那个 (y≈130)。v2 顺序相同。

    Week 2 H3 前 (text+context+proximity 都相等)：1:1 allocator 按原 ID 顺序硬分，
    结果可能对可能不对。加了 layout_score 后，y-ratio 相似度应该把中间→中间、
    上→上、下→下 这个映射钉死（每条 anchor 的 layout 首选和自己 y 位置最接近的候选）。
    """

    import pymupdf

    v1 = tmp_path / "v1.pdf"
    v2 = tmp_path / "v2.pdf"

    def _build(path: Path, annotate: bool) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), "pivot sentence appears at the top of page")
        page.insert_text((72, 400), "pivot sentence appears in the middle region")
        page.insert_text((72, 700), "pivot sentence appears near the bottom margin")
        if annotate:
            for q in page.search_for("pivot sentence appears", quads=True):
                annot = page.add_highlight_annot(q)
                annot.update()
        doc.save(str(path))
        doc.close()

    _build(v1, annotate=True)
    _build(v2, annotate=False)

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] == 3

    # 按 v1 anchor 的 y_center 排序，期望 v2 new_anchor.y_center 也单调递增。
    items = []
    for r in report["results"]:
        old_q = r["old_anchor"]["quads"][0]
        new_q = r["new_anchor"]["quads"][0]
        old_cy = (old_q[1] + old_q[5]) / 2
        new_cy = (new_q[1] + new_q[5]) / 2
        items.append((old_cy, new_cy, r))
    items.sort(key=lambda t: t[0])
    new_ys = [t[1] for t in items]
    # 必须单调递增，且三个 y 分别接近 100/400/700 (±15 pt)
    assert new_ys == sorted(new_ys), f"layout 没锁 k-th 顺序: {new_ys}"
    for (_, new_y, _r), expected in zip(items, [100, 400, 700], strict=False):
        assert abs(new_y - expected) < 25, f"new_y={new_y} 偏离目标 {expected}"


def test_match_reason_includes_layout_and_length(pair_identical) -> None:
    """schema check: Week 2 H3 起 MatchReason 填 layout_score / length_similarity。"""

    v1, v2 = pair_identical
    report = _run_diff(v1, v2)
    for r in report["results"]:
        mr = r["match_reason"]
        assert "layout_score" in mr, f"layout_score 缺失: {mr}"
        assert "length_similarity" in mr, f"length_similarity 缺失: {mr}"
        # identical fixture 下 exact 命中 quad 完全一致 → y/x ratio 完全相等
        assert mr["layout_score"] >= 0.99, f"identical 下 layout 应接近 1.0: {mr}"
        assert mr["length_similarity"] == 1.0


def test_same_page_shifted_location_is_relocated_not_preserved(tmp_path: Path) -> None:
    """同页同文本但 quad 位置偏移 > 15 pt → 应判 relocated（不再是 preserved）。

    直接触达 spike 发现的 BLEU 假阳性问题。
    """

    import pymupdf

    v1 = tmp_path / "v1.pdf"
    v2 = tmp_path / "v2.pdf"

    # v1: "keyword" 在 y=100，一并标 highlight
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "keyword appears here alone")
    for q in page.search_for("keyword", quads=True):
        annot = page.add_highlight_annot(q)
        annot.update()
    doc.save(str(v1))
    doc.close()

    # v2: "keyword" 下移到 y=300（远超 15 pt 阈值），无注释
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 50), "inserted new intro content at top")
    page.insert_text((72, 300), "keyword appears here alone")
    doc.save(str(v2))
    doc.close()

    report = _run_diff(v1, v2)
    assert report["summary"]["total_annotations"] == 1
    r = report["results"][0]
    assert r["status"] == "relocated", (
        f"same-page-shifted should be relocated, got {r['status']}: {r['message']}"
    )
    assert r["match_reason"]["page_delta"] == 0
    # 距离应该显著大于阈值
    assert "shifted" in r["message"].lower() or "same page" in r["message"].lower()
