"""stderr 路由的日志 —— plan.md §12。

规则：
- 普通人类可读输出写 stdout。
- 警告、错误、调试信息一律走 stderr，不污染 stdout 的 JSON。
- `--log-format json` 时 stderr 每行是一条 JSON，方便 agent 解析。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from enum import IntEnum
from typing import TextIO


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


class Logger:
    """轻量 logger —— 不使用 stdlib logging，避免 handler 重入和 stdout 污染。"""

    def __init__(
        self,
        *,
        level: LogLevel = LogLevel.WARNING,
        fmt: str = "text",
        stream: TextIO | None = None,
    ) -> None:
        self.level = level
        self.fmt = fmt
        self.stream = stream if stream is not None else sys.stderr

    def _emit(self, level: LogLevel, msg: str, **extra: object) -> None:
        if level < self.level:
            return
        if self.fmt == "json":
            payload = {
                "ts": datetime.now(UTC).isoformat(),
                "level": level.name,
                "message": msg,
                **extra,
            }
            print(json.dumps(payload, ensure_ascii=False), file=self.stream)
        else:
            prefix = level.name.lower()
            if extra:
                tail = " ".join(f"{k}={v}" for k, v in extra.items())
                print(f"{prefix}: {msg} [{tail}]", file=self.stream)
            else:
                print(f"{prefix}: {msg}", file=self.stream)

    def debug(self, msg: str, **extra: object) -> None:
        self._emit(LogLevel.DEBUG, msg, **extra)

    def info(self, msg: str, **extra: object) -> None:
        self._emit(LogLevel.INFO, msg, **extra)

    def warning(self, msg: str, **extra: object) -> None:
        self._emit(LogLevel.WARNING, msg, **extra)

    def error(self, msg: str, **extra: object) -> None:
        self._emit(LogLevel.ERROR, msg, **extra)


def build_logger(verbose: bool = False, quiet: bool = False, log_format: str = "text") -> Logger:
    """按 CLI flag 构造 logger。`verbose` 与 `quiet` 同时给时以 quiet 为准。"""

    if quiet:
        level = LogLevel.ERROR
    elif verbose:
        level = LogLevel.DEBUG
    else:
        level = LogLevel.WARNING
    return Logger(level=level, fmt=log_format)
