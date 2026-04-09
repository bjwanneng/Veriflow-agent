"""Unified logging utilities for VeriFlow-Agent Gateway and TUI.

Two modes:
  - Gateway: logs to console (stderr) — visible in the Gateway terminal
  - TUI:     logs to file only         — keeps the TUI terminal clean

Usage:
    from veriflow_agent.gateway.log import setup_logging, Log, L

    # Gateway (console logging)
    setup_logging("DEBUG", prefix="Gateway", mode="console")

    # TUI (file-only logging)
    setup_logging("DEBUG", prefix="TUI", mode="file")
"""

from __future__ import annotations

import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any

# ── Log tags ────────────────────────────────────────────────────────────────


class LogTag(Enum):
    """Structured log categories for filtering and readability."""
    CONN = "CONN"        # WebSocket connection lifecycle
    MSG_IN = "MSG_IN"    # Inbound messages (client → server)
    MSG_OUT = "MSG_OUT"  # Outbound messages (server → client)
    CHUNK = "CHUNK"      # Streaming chunk details
    STREAM = "STREAM"    # Stream start/end statistics
    STAGE = "STAGE"      # Pipeline stage transitions
    PERF = "PERF"        # Performance / timing measurements
    ERR = "ERR"          # Errors and exceptions


# Short alias for convenience
L = LogTag


# ── Log file location ──────────────────────────────────────────────────────


def _log_dir() -> Path:
    """Return the log directory, creating it if needed."""
    d = Path.home() / ".veriflow" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Logger wrapper ──────────────────────────────────────────────────────────


class _TagLogger:
    """Provides tag-prefixed log methods: Log.info(tag, msg, **kwargs)."""

    def __init__(self, logger: logging.Logger, prefix: str = "") -> None:
        self._logger = logger
        self._prefix = prefix

    def _format(self, tag: LogTag, msg: str, **kwargs: Any) -> str:
        parts = [f"[{tag.value}]", msg]
        for k, v in kwargs.items():
            parts.append(f"{k}={v}")
        text = "  ".join(parts)
        if self._prefix:
            return f"[{self._prefix}] {text}"
        return text

    def debug(self, tag: LogTag, msg: str, **kwargs: Any) -> None:
        self._logger.debug(self._format(tag, msg, **kwargs))

    def info(self, tag: LogTag, msg: str, **kwargs: Any) -> None:
        self._logger.info(self._format(tag, msg, **kwargs))

    def warning(self, tag: LogTag, msg: str, **kwargs: Any) -> None:
        self._logger.warning(self._format(tag, msg, **kwargs))

    def error(self, tag: LogTag, msg: str, **kwargs: Any) -> None:
        self._logger.error(self._format(tag, msg, **kwargs))


# Singleton — configured by setup_logging()
Log = _TagLogger(logging.getLogger("veriflow"))


# ── Setup ───────────────────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)-5s] %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def resolve_level(cli_verbose: bool = False, cli_quiet: bool = False) -> str:
    """Resolve log level from CLI flags and environment variable.

    Priority: CLI flags > VERIFLOW_LOG env > default INFO.
    """
    if cli_verbose:
        return "DEBUG"
    if cli_quiet:
        return "WARNING"
    return os.environ.get("VERIFLOW_LOG", "INFO").upper()


def setup_logging(
    level: str = "INFO",
    prefix: str = "",
    mode: str = "console",
) -> None:
    """Configure the veriflow logger.

    Args:
        level:  "DEBUG" | "INFO" | "WARNING" | "ERROR"
        prefix: "Gateway" or "TUI"
        mode:   "console" → log to stderr + file (Gateway)
                "file"    → log to ~/.veriflow/logs/ only (TUI)
    """
    numeric = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Get our namespace logger — do NOT touch root logger
    vf_logger = logging.getLogger("veriflow")
    vf_logger.setLevel(numeric)
    vf_logger.handlers.clear()
    vf_logger.propagate = False  # Never bubble to root

    if mode == "file":
        # TUI: write to file, keep console clean
        log_file = _log_dir() / "tui.log"
        handler = logging.FileHandler(str(log_file), encoding="utf-8")
        handler.setFormatter(formatter)
        vf_logger.addHandler(handler)
    else:
        # Gateway: log to both stderr AND file
        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(formatter)
        vf_logger.addHandler(stderr_handler)

        log_file = _log_dir() / "gateway.log"
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(formatter)
        vf_logger.addHandler(file_handler)

    # Always suppress noisy third-party loggers
    for noisy in (
        "websockets", "websockets.protocol", "websockets.server",
        "uvicorn.access", "httpcore", "httpx",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn.error at INFO is fine (startup/shutdown messages)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    global Log
    Log = _TagLogger(vf_logger, prefix=prefix)

    log_dest = f"file://{_log_dir() / 'tui.log'}" if mode == "file" else "stderr"
    Log.info(L.CONN, "Logging initialized", level=level, dest=log_dest)


# ── Timer helper ────────────────────────────────────────────────────────────


class Timer:
    """Simple context manager for timing blocks.

    Usage:
        with Timer() as t:
            do_work()
        Log.debug(L.PERF, "Work done", elapsed_ms=t.elapsed_ms)
    """

    def __init__(self) -> None:
        self.start: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> Timer:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = (time.perf_counter() - self.start) * 1000
