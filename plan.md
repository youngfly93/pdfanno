# PDF 注释 CLI/TUI 应用 PRD

## 1. 产品定位

本项目先做一个面向论文和技术文档的 **agent-friendly PDF 注释 CLI 工具**，后续再扩展为 keyboard-first 的终端 TUI 阅读与标注工具。

第一目标不是替代完整 PDF 阅读器，而是把“搜索文本、生成高亮、添加 note、列出/导出注释”做成可靠、可脚本化、可测试、适合 agent 调用的命令行能力。

推荐技术路线：

```text
Python 3.12+
PyMuPDF / MuPDF    PDF 文本提取、quads、annotation 读写
Typer              CLI
Pydantic           JSON schema / 数据校验，可选
SQLite             sidecar 注释数据库，Phase 1 引入
Textual            TUI，Phase 2 引入
```

v0-v1 采用纯 Python 实现。TypeScript/MCP wrapper 可以作为后续集成层，不进入第一阶段核心路径。

## 2. 许可证决策

本项目默认接受 **PyMuPDF / MuPDF 的 AGPL 路线**，v0-v1 不做闭源分发或商业闭源集成。

如果未来要闭源分发、商业集成、SaaS 后端托管处理用户 PDF，必须在进入商业化前完成其中一个决策：

- 购买 MuPDF 商业授权。
- 或重新评估 PDFium + qpdf/pikepdf 等替代方案。

不要把“以后再换 PDF 内核”当作计划。annotation 写回、quads、appearance stream、已有注释兼容会深度绑定内核。

## 3. 目标与非目标

### 3.1 v1 目标

- 给定 PDF 和关键词，搜索并生成 highlight annotation。
- 支持导出副本，默认不覆盖原文件。
- 支持列出现有 PDF annotations。
- 支持添加 note / freetext 注释的基础能力。
- 支持 `--dry-run` 预览将要创建的 annotations。
- 支持幂等执行，同一规则重复运行不产生重复注释。
- 支持 JSON/Markdown 提取，便于 agent 读取和二次处理。
- 支持 sidecar 存储草稿，并通过显式命令写回或导出。
- 建立覆盖旋转、多栏、扫描件、加密 PDF 的 fixtures 和不变量测试。

### 3.2 v1 非目标

- 不做多文档知识库。
- 不做 OCR。
- 不做图像模式 PDF 阅读器。
- 不做鼠标拖选。
- 不做复杂 sidecar 与外部 PDF annotation 的自动 merge。
- 不在 v1 内置 LLM 语义检索；v1 只做 literal、ignore-case、page range 等确定性规则。
- 不在 v1 支持 regex。regex 需要独立的 text layer 到 quads 映射，进入 v1.5。
- 不在 v1 提供 TypeScript 子进程封装或 MCP server。
- 不承诺处理所有复杂 PDF 版式；v1 以 text layer 可用的论文 PDF 为主。

## 4. 用户场景

### 4.1 批量高亮关键词

用户希望在论文中高亮某个术语，并生成一个带注释的副本：

```bash
pdfanno highlight paper.pdf "transformer" -o paper.annotated.pdf
pdfanno highlight paper.pdf "transformer" -o paper.annotated.pdf --dry-run --json
```

验收标准：

- 原始 `paper.pdf` 不被修改。
- 输出 PDF 重新打开后能看到高亮。
- 高亮使用 text quads，不使用粗暴矩形覆盖。
- 命令返回命中数量和输出路径。
- `--dry-run` 不写任何文件，只返回将要创建的 annotation plan。

### 4.2 查看已有注释

```bash
pdfanno list paper.annotated.pdf
pdfanno list paper.annotated.pdf --json
```

验收标准：

- 能列出 page、type、content、color、xref 或稳定 id。
- JSON 输出可被脚本消费。

### 4.3 sidecar 草稿

用户先把注释保存在 sidecar 中，不立即修改 PDF：

```bash
pdfanno highlight paper.pdf "related work" --sidecar
pdfanno status paper.pdf
pdfanno export paper.pdf -o paper.annotated.pdf
```

验收标准：

- `--sidecar` 不修改 PDF。
- `export` 将 sidecar 中的注释写入导出副本。
- 未写回状态可查询。

### 4.4 Agent 读取与回写

agent 先提取已有注释为结构化数据，分析后再将新注释应用回 PDF：

```bash
pdfanno extract paper.pdf --format json > annotations.json
pdfanno apply paper.pdf agent_annotations.json -o paper.reviewed.pdf
```

验收标准：

- `extract --format json` 输出稳定 schema。
- `apply` 支持 dry-run 和幂等去重。
- agent 生成的 annotation 带有可追踪的 rule/query/id。

## 5. 阶段规划

### Phase 0 · 一小时可跑通的 spike

目标：证明 PyMuPDF annotation 写回链路可行。

命令范围：

```bash
pdfanno highlight INPUT.pdf "needle" -o OUTPUT.pdf
pdfanno list OUTPUT.pdf
```

实现要求：

- 使用 `page.search_for(needle, quads=True)`。
- 使用 `page.add_highlight_annot(quads)`。
- 保存到 `-o` 指定的新文件。
- 不引入 sidecar、不引入 TUI、不做交互选择。

验收标准：

- 正常 PDF 可高亮并导出。
- 重新打开导出 PDF 后 `list` 能读回 annotation。
- 有最小 pytest 覆盖。

### Phase 1 · CLI + sidecar

目标：形成可用的 CLI 产品。

建议命令：

```bash
pdfanno search INPUT.pdf "needle"
pdfanno highlight INPUT.pdf "needle" [-o OUTPUT.pdf] [--sidecar] [--dry-run]
pdfanno note INPUT.pdf --page 3 --text "important" [--sidecar]
pdfanno list INPUT.pdf [--json]
pdfanno extract INPUT.pdf --format json|markdown
pdfanno apply INPUT.pdf ANNOTATIONS.json -o OUTPUT.pdf [--dry-run]
pdfanno import INPUT.pdf
pdfanno status INPUT.pdf
pdfanno rebind OLD_PDF_PATH NEW_PDF_PATH [--doc-id OLD_DOC_ID]
pdfanno export INPUT.pdf -o OUTPUT.pdf
```

默认行为：

- 默认不覆盖原 PDF。
- 原地写回必须显式使用 `--in-place`。
- 重复运行默认去重，不产生重复 annotation；如需重复创建，必须显式使用 `--allow-duplicates`。
- 对加密、签名、权限受限 PDF，默认拒绝原地写回，建议导出副本。

### Phase 2 · Textual TUI

目标：在 CLI core 稳定后，提供 keyboard-first 的终端交互。

优先能力：

- 搜索结果列表。
- 高亮列表和 note 面板。
- 文本模式阅读。
- label-based 选词，参考 Sioyek 的 keyboard selection 思路。

暂缓能力：

- Kitty/Sixel/Chafa 图像渲染。
- 鼠标拖选。
- OCR。

### Phase 3 · 图像模式与高级兼容

目标：改善版式、图表、公式阅读体验。

能力：

- PyMuPDF 渲染页面为 raster image。
- Kitty graphics protocol 优先，Sixel/Chafa fallback。
- 更完整地导入、编辑、删除已有 PDF annotations。

## 6. Agent-First CLI 要求

### 6.1 结构化 I/O

所有核心命令都必须支持 `--json`，输出稳定 schema，字段名保持向后兼容。普通人类输出可以更友好，但不能替代 JSON。

JSON 输出必须至少包含：

- `input` / `output`
- `command`
- `dry_run`
- `matches`
- `annotations_planned`
- `annotations_created`
- `warnings`

### 6.2 Dry Run

`--dry-run` 是 agent 调用的默认推荐模式。它必须：

- 不修改 PDF。
- 不写 sidecar。
- 返回将要命中的 page、text、quads、annotation id、rule id。
- 能和真实执行结果使用同一 JSON schema。

### 6.3 幂等性

同一个 PDF、同一条规则、同一段文本位置，重复执行不应产生重复注释。

推荐生成稳定 id：

```text
annotation_id = sha256(
  doc_id + kind + page + normalized_quads + normalized_text + rule_hash
)
```

`normalized_quads` 定义：

- 始终使用 PDF point 坐标，不使用屏幕像素或终端 cell 坐标。
- 按 PyMuPDF quad 点顺序序列化。
- 每个 float 四舍五入到 2 位小数后再参与 hash。
- fixtures 必须覆盖 roundtrip 后 id 不变。

`normalized_text` 定义：

- 使用 PDF text layer 中实际命中的原文（即 §8.2 的 `matched_text`），不是用户/agent 后加的 `contents` note。
- 对 whitespace 做 `\s+` → 单空格的归一化，两端 strip。
- 如此可保证"先高亮、再加 note"不改变 `annotation_id`。

幂等记录优先保存在 sidecar。写入 PDF annotation 时，尽量将 id 同步到可回读字段，例如 annotation name 或 content metadata；如果目标阅读器丢弃该字段，仍以 sidecar 为准。

### 6.4 查询规则

v1 支持确定性规则：

```text
literal text
ignore-case
page range
color
note content
```

v1.5 再支持：

```text
regex
sentence-level matching
section-scoped matching
```

regex 不能依赖 `page.search_for()`，需要基于 `page.get_text("words")` 或 `page.get_text("dict")` 构建 text layer，并把匹配范围映射回 quads。跨行、跨 span、多栏场景必须有 fixtures。

上下文规则，例如“只高亮 Methods 章节”或“高亮包含 p < 0.05 的句子”，可以进入 v1.5，但必须编译成可解释的规则计划，而不是直接依赖不可复现的 LLM 输出。

### 6.5 Extract / Apply 闭环

`extract` 面向“agent 读 PDF annotation”，`apply` 面向“agent 写回 annotation plan”。

`apply` 输入必须是显式 annotation plan，而不是自由文本指令。自由文本指令应由上游 agent 转换成 plan 后再调用本工具。

## 7. 核心架构

建议包结构：

```text
pdfanno/
  cli.py
  models.py
  pdf_core/
    document.py
    text.py
    annotations.py
    save.py
  rules/
    match.py
    idempotency.py
  store/
    sidecar.py
    schema.sql
  tui/
    app.py
    page_view.py
    panels.py
  viewport/
    transform.py
    cache.py
tests/
  fixtures/
  test_cli_highlight.py
  test_annotations.py
  test_transform.py
```

分层原则：

- `pdf_core` 只处理 PDF 文件和 PDF 坐标。
- `store` 只处理 sidecar 数据和同步状态。
- `rules` 处理查询、匹配、dry-run plan 和幂等 id。
- `cli` 只编排命令，不直接操作 PyMuPDF 细节。
- `tui` 不直接写 PDF，通过 service/API 调用 core。

## 8. 数据模型

### 8.1 文档身份

不要使用整文件字节哈希作为唯一身份。incremental save 会改变文件字节。

推荐策略：

```text
primary_id:
  PDF trailer /ID[0]，如果存在

fallback_id:
  page_count + first_page_text_hash + file_size
```

路径不是文档身份的一部分。sidecar 应单独保存 `last_known_path`，用于定位和重绑定。

需要提供：

```bash
pdfanno rebind OLD_PDF_PATH NEW_PDF_PATH [--doc-id OLD_DOC_ID]
```

日常用法接受两个 PDF 路径，工具自行计算 `OLD_DOC_ID`。`--doc-id` 用于旧 PDF 已不可访问、只保留 sidecar 记录的高级场景。`rebind` 用于 PDF 改名、移动目录或 fallback id 变化后的人工确认绑定。sidecar schema 必须允许未来迁移或重新绑定 document id。

### 8.2 Annotation 记录

内部注释记录应保存 quads，而不是只保存 rect：

```json
{
  "id": "uuid",
  "annotation_id": "stable-sha256",
  "doc_id": "...",
  "page": 12,
  "kind": "highlight",
  "quads": [
    [x1, y1, x2, y2, x3, y3, x4, y4]
  ],
  "color": [1.0, 1.0, 0.0],
  "contents": "important definition",
  "matched_text": "original matched text",
  "rule_hash": "stable rule hash",
  "query": "transformer",
  "source": "sidecar",
  "pdf_xref": null,
  "created_at": "...",
  "modified_at": "..."
}
```

字段说明：

- `id`：sidecar 行的本地 uuid，仅在本机 sidecar 内唯一。
- `annotation_id`：跨实例、跨设备的稳定 sha256，用于幂等去重和同步，按 §6.3 公式生成。
- `matched_text`：PDF text layer 中实际命中的原文，参与 `annotation_id` hash。
- `contents`：用户/agent 后加的 note 文本，**不参与** `annotation_id` hash，修改它不会让 id 漂移。

### 8.3 Annotation Plan

`--dry-run` 和 `apply` 共用 annotation plan，避免预览结果和真实执行路径分叉：

```json
{
  "schema_version": 1,
  "doc_id": "...",
  "rules": [
    {
      "rule_id": "rule-001",
      "kind": "highlight",
      "query": "transformer",
      "mode": "literal",
      "color": [1.0, 1.0, 0.0]
    }
  ],
  "annotations": [
    {
      "annotation_id": "stable-sha256",
      "rule_id": "rule-001",
      "kind": "highlight",
      "page": 12,
      "matched_text": "transformer",
      "quads": [
        [72.12, 144.34, 130.55, 144.34, 72.12, 158.02, 130.55, 158.02]
      ],
      "color": [1.0, 1.0, 0.0],
      "contents": "",
      "source": "plan"
    }
  ]
}
```

`extract --format json` 输出的 annotation 记录应能作为 `apply` 输入的一部分回放。`apply` 必须忽略已经存在的同 `annotation_id` 记录，除非用户显式传入 `--allow-duplicates`。

## 9. sidecar 与 PDF 注释同步策略

v1 使用保守策略：

- PDF 内已有 annotations 可以 `list` 和 `import`。
- `import` 会复制为 sidecar 记录，并保留 `pdf_xref` 和来源标记。
- sidecar 新增 annotations 默认只追加，不自动覆盖外部阅读器创建的注释。
- 不做自动冲突解决。
- 如果发现同一 `pdf_xref` 被外部修改，只提示状态，不自动 merge。

后续版本再支持：

- 双向同步。
- 删除/修改外部 annotations。
- 冲突 diff 与人工选择。

## 10. 坐标与 fixtures 测试

PDF 注释 bug 的主要来源是坐标、旋转、跨行和分栏。测试必须从 Phase 0 开始建立。

建议 fixtures：

```text
fixtures/simple.pdf
fixtures/two_columns.pdf
fixtures/rotated_90.pdf
fixtures/rotated_270.pdf
fixtures/scanned_no_text.pdf
fixtures/encrypted.pdf
fixtures/existing_annotations.pdf
```

关键不变量：

- 所有 quads 坐标在页面边界内。
- 旋转页面保存后重开，annotation 页码和位置仍正确。
- 多栏文本搜索不会跨错误列合并。
- 扫描件没有 text layer 时给出明确错误或降级提示。
- `-o OUTPUT.pdf` 不修改输入文件。
- 同一命令重复执行不会产生重复 annotations。
- `--dry-run` 的 planned annotations 与真实执行创建的 annotations 一致。
- quads roundtrip 后归一化 id 不变。

## 11. 保存策略

默认保存策略：

```text
-o OUTPUT.pdf
  导出副本，默认推荐。

--sidecar
  只写 sidecar，不改 PDF。

--dry-run
  只生成 annotation plan，不写 PDF，不写 sidecar。

--in-place
  显式原地写回。仅在安全条件满足时允许。
```

原地写回要求：

- `doc.can_save_incrementally()` 为真。
- 保留原 PDF encryption 设置。
- 对签名 PDF、权限受限 PDF 默认拒绝。
- 对 XFA、动态表单、带 JavaScript 行为或实现无法安全判断的 PDF，默认拒绝。
- 命令输出必须明确说明写回的是原文件。

颜色输入：

```text
--color yellow
--color "1.0,1.0,0.0"
```

v1 固定命名色：

```text
yellow, green, blue, pink, orange, red, purple
```

命名色优先对齐 Zotero、Acrobat、Preview 等常见阅读器的高亮色习惯。RGB float 输入保留给脚本和高级用户。

## 12. 错误处理与 CLI 输出

CLI 输出要对人可读，也要能被脚本消费。

规则：

- 普通输出写 stdout。
- warning/error 写 stderr。
- `--json` 输出稳定 JSON schema。
- 无命中时退出码为 `0`，但输出 `matches: 0`。
- 幂等去重导致没有新 annotation 时退出码为 `0`，但输出 `annotations_created: 0`。
- 文件不存在、PDF 打不开、保存失败时退出码非 0。
- 支持 `--verbose`、`--quiet`、`--log-format text|json`。
- structured log 写 stderr，不污染 stdout JSON。

退出码：

```text
0  success，包括无命中、幂等去重后无新增
2  usage error，例如参数缺失、bad regex、page range 越界
3  input/file error，例如文件不存在、PDF 打不开、加密无密码、权限拒绝
4  processing error，例如部分页面处理失败、保存失败、annotation 写回失败
```

示例：

```bash
pdfanno highlight paper.pdf "transformer" -o out.pdf --json
```

```json
{
  "input": "paper.pdf",
  "output": "out.pdf",
  "command": "highlight",
  "dry_run": false,
  "matches": 12,
  "annotations_planned": 12,
  "annotations_created": 12,
  "warnings": []
}
```

## 13. 主要风险

| 风险 | 影响 | 对策 |
|---|---|---|
| AGPL 许可不清 | 后续无法闭源分发 | 现在明确接受 AGPL 或购买商业授权 |
| 坐标错误 | 高亮位置错乱 | 从 Phase 0 建 fixtures 和不变量测试 |
| PDF 变体复杂 | 部分文件无法正确写回 | 默认导出副本，失败时给出明确错误 |
| sidecar 冲突 | 外部阅读器修改后状态不一致 | v1 只提示，不自动 merge |
| 幂等 id 不稳定 | 重复注释或无法同步 | 使用 doc_id + normalized_quads + rule_hash，并用 fixtures 做 roundtrip 测试 |
| JSON schema 漂移 | agent 集成易坏 | schema version 固定，变更走版本升级 |
| regex 过早进入 v1 | 拖慢 CLI core 交付 | regex 放到 v1.5，先稳定 literal search |
| TUI 范围膨胀 | MVP 延期 | TUI 放到 Phase 2，CLI core 先交付 |

## 14. 推荐第一周任务

1. 建立 Python 项目骨架和 `pdfanno` CLI 入口。
2. 实现 `highlight INPUT "needle" -o OUTPUT`。
3. 实现 `highlight --dry-run --json`，输出 annotation plan。
4. 实现 `list INPUT --json`。
5. 添加幂等 id：同一命令重复执行不重复高亮。
6. 添加 `simple.pdf`、`existing_annotations.pdf`、`rotated_90.pdf`、`rotated_270.pdf` fixtures。
7. 添加 `normalized_quads` 测试：对 simple、rotated_90、rotated_270 执行 highlight → save → reopen → re-highlight 的 roundtrip，三次 `annotation_id` 必须一致。旋转 fixture 是必需的，否则测不到归一化真正要防的 bug。
8. 添加 pytest：高亮导出、重开读回、输入文件不变、dry-run 不写文件、重复执行去重。
9. 写清楚 README 中的 AGPL 许可说明和“不默认覆盖原 PDF”原则。

## 15. 后续集成方向

如果未来要接入现有 TypeScript/MCP 生态，推荐把 Python CLI 作为稳定内核，然后增加薄封装：

```text
MCP/TypeScript wrapper -> 调用 pdfanno --json -> 解析稳定 schema
```

不要在 PDF core 稳定前引入跨语言 IPC。v1 的工程目标是先把 annotation 写回、幂等、JSON schema 和 fixtures 测试做稳。

## 16. 参考资料

- PyMuPDF annotations: https://pymupdf.readthedocs.io/en/latest/recipes-annotations.html
- PyMuPDF page API: https://pymupdf.readthedocs.io/en/latest/page.html
- Textual: https://textual.textualize.io/
- MuPDF license: https://github.com/ArtifexSoftware/mupdf
- Sioyek commands: https://sioyek-documentation.readthedocs.io/en/latest/commands.html
