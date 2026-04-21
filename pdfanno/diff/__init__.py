"""v0.2 annotation diff/migrate —— plan.md + PRD v0.2。

职责：跨版本 PDF 的注释追踪与迁移。与 v0.1 内核（pdf_core / rules / store）解耦，
通过公开接口组合：
  anchors.extract_anchors(doc, doc_id) -> list[Anchor]
  match.diff_against(old_anchors, new_doc, new_doc_id) -> DiffReport
"""
