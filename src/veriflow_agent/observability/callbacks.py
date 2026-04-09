"""Callback implementations for LLM observability.

This module provides ready-to-use callback implementations for common use cases
like UI updates, logging, and metrics collection.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from .events import LLMEvent

logger = logging.getLogger("veriflow.observability")


class EventCallback(Protocol):
    """Protocol for event callback functions."""

    def __call__(self, event: LLMEvent) -> None: ...


class LoggingCallback:
    """Callback that logs events to a Python logger."""

    def __init__(
        self,
        logger_name: str = "veriflow.observability.events",
        level: int = logging.DEBUG,
        include_payload: bool = True,
    ):
        """
        Initialize the logging callback.

        Args:
            logger_name: Name of the logger to use
            level: Logging level for events
            include_payload: Whether to include event payload in log
        """
        self.logger = logging.getLogger(logger_name)
        self.level = level
        self.include_payload = include_payload

    def __call__(self, event: LLMEvent) -> None:
        """Log an event."""
        msg = f"[{event.stage}] {event.event_type}"
        if self.include_payload:
            # Truncate payload for readability
            payload_str = str(event.payload)
            if len(payload_str) > 200:
                payload_str = payload_str[:200] + "..."
            msg += f" | {payload_str}"

        self.logger.log(self.level, msg)


class JsonlFileCallback:
    """Callback that writes events to a JSONL file."""

    def __init__(
        self,
        file_path: str | Path,
        buffer_size: int = 10,
        flush_interval: float = 5.0,
    ):
        """
        Initialize the JSONL file callback.

        Args:
            file_path: Path to the JSONL file
            buffer_size: Number of events to buffer before writing
            flush_interval: Maximum seconds between automatic flushes
        """
        self.file_path = Path(file_path)
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval

        self._buffer: list[LLMEvent] = []
        self._last_flush = time.time()
        self._lock = threading.Lock()

        # Ensure directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: LLMEvent) -> None:
        """Buffer an event for writing."""
        with self._lock:
            self._buffer.append(event)
            should_flush = (
                len(self._buffer) >= self.buffer_size
                or (time.time() - self._last_flush) >= self.flush_interval
            )

        if should_flush:
            self._flush()

    def _flush(self) -> None:
        """Write buffered events to file."""
        with self._lock:
            if not self._buffer:
                return
            events_to_write = self._buffer[:]
            self._buffer = []
            self._last_flush = time.time()

        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                for event in events_to_write:
                    json_line = json.dumps(event.to_dict(), ensure_ascii=False)
                    f.write(json_line + "\n")
        except Exception as e:
            logger.error(f"Failed to write to {self.file_path}: {e}")

    def close(self) -> None:
        """Flush remaining events and close."""
        self._flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class MetricsAggregationCallback:
    """Callback that aggregates metrics from events."""

    def __init__(self):
        """Initialize the metrics aggregation callback."""
        self.reset()

    def reset(self) -> None:
        """Reset all aggregated metrics."""
        self.event_count = 0
        self.event_counts_by_type: dict[str, int] = {}
        self.total_tokens = 0
        self.total_cost = 0.0
        self.tool_calls = 0
        self.errors = 0
        self.first_event_time: float | None = None
        self.last_event_time: float | None = None

    def __call__(self, event: LLMEvent) -> None:
        """Aggregate metrics from an event."""
        self.event_count += 1

        # Count by type
        event_type = str(event.event_type)
        self.event_counts_by_type[event_type] = (
            self.event_counts_by_type.get(event_type, 0) + 1
        )

        # Track timing
        if self.first_event_time is None:
            self.first_event_time = event.timestamp
        self.last_event_time = event.timestamp

        # Extract metrics from payload
        payload = event.payload

        # Token counts
        if event_type in ("text_delta", "metrics"):
            tokens = payload.get("output_tokens", 0)
            self.total_tokens += tokens

        # Cost
        if event_type == "metrics":
            cost = payload.get("cost_usd", 0.0)
            self.total_cost += cost

        # Tool calls
        if event_type == "tool_start":
            self.tool_calls += 1

        # Errors
        if event_type in ("error", "tool_error"):
            self.errors += 1

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of aggregated metrics."""
        duration = 0.0
        if self.first_event_time and self.last_event_time:
            duration = self.last_event_time - self.first_event_time

        return {
            "event_count": self.event_count,
            "event_counts_by_type": self.event_counts_by_type.copy(),
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost, 6),
            "tool_calls": self.tool_calls,
            "errors": self.errors,
            "duration_seconds": round(duration, 3),
            "events_per_second": round(self.event_count / duration, 2) if duration > 0 else 0,
        }
