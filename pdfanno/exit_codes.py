"""统一退出码 —— 按 plan.md §12 定义。

agent 通过退出码分档区分 "成功"、"用户输入问题"、"文件问题"、"处理失败"。
0 保留给所有正常完成（包括无命中、幂等去重后无新增）。
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """pdfanno 退出码分档。"""

    SUCCESS = 0
    USAGE_ERROR = 2
    INPUT_ERROR = 3
    PROCESSING_ERROR = 4
