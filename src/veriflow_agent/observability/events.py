"""Event definitions for LLM observability.

This module defines the core event types used for real-time LLM execution tracking.
All events are designed to be serializable to JSON for persistence and transmission.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Types of LLM execution events."""

    # Session lifecycle
    SESSION_INIT = "session_init"  # Session initialized with tools/config

    # Text generation
    TEXT_DELTA = "text_delta"  # New text token/chunk generated
    TEXT_COMPLETE = "text_complete"  # Text generation finished

    # Tool execution
    TOOL_START = "tool_start"  # Tool invocation started
    TOOL_PROGRESS = "tool_progress"  # Tool execution progress update
    TOOL_COMPLETE = "tool_complete"  # Tool execution succeeded
    TOOL_ERROR = "tool_error"  # Tool execution failed

    # Metrics and finalization
    METRICS = "metrics"  # Token/cost metrics update
    STREAM_END = "stream_end"  # Stream completed successfully
    ERROR = "error"  # General error occurred


@dataclass
class LLMEvent:
    """A single event in the LLM execution stream.

    Attributes:
        event_id: Unique identifier for this event
        timestamp: Unix timestamp when event occurred
        stage: Pipeline stage name (e.g., "coder", "architect")
        event_type: Type of event (from EventType enum)
        payload: Event-specific data (structure varies by type)
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    stage: str = ""
    event_type: EventType | str = EventType.TEXT_DELTA
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "stage": self.stage,
            "event_type": str(self.event_type),
            "payload": self.payload,
        }

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LLMEvent:
        """Create event from dictionary."""
        return cls(
            event_id=data.get("event_id", ""),
            timestamp=data.get("timestamp", 0.0),
            stage=data.get("stage", ""),
            event_type=data.get("event_type", EventType.TEXT_DELTA),
            payload=data.get("payload", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> LLMEvent:
        """Deserialize event from JSON string."""
        return cls.from_dict(json.loads(json_str))


# Convenience factory methods for creating events

def create_session_init_event(
    stage: str,
    session_id: str,
    tools: list[str],
    model: str = "",
    **extra,
) -> LLMEvent:
    """Create a session initialization event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.SESSION_INIT,
        payload={
            "session_id": session_id,
            "tools": tools,
            "model": model,
            **extra,
        },
    )


def create_text_delta_event(
    stage: str,
    text: str,
    cumulative_text: str = "",
    token_index: int = 0,
) -> LLMEvent:
    """Create a text delta (new token) event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.TEXT_DELTA,
        payload={
            "text": text,
            "cumulative_text": cumulative_text,
            "token_index": token_index,
        },
    )


def create_tool_start_event(
    stage: str,
    tool_name: str,
    tool_input: dict[str, Any],
    call_id: str = "",
) -> LLMEvent:
    """Create a tool execution start event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.TOOL_START,
        payload={
            "tool_name": tool_name,
            "tool_input": tool_input,
            "call_id": call_id or str(uuid.uuid4())[:8],
        },
    )


def create_tool_complete_event(
    stage: str,
    call_id: str,
    tool_output: dict[str, Any],
    duration_ms: int = 0,
) -> LLMEvent:
    """Create a tool execution complete event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.TOOL_COMPLETE,
        payload={
            "call_id": call_id,
            "tool_output": tool_output,
            "duration_ms": duration_ms,
        },
    )


def create_metrics_event(
    stage: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> LLMEvent:
    """Create a metrics update event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.METRICS,
        payload={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": cost_usd,
        },
    )


def create_tool_error_event(
    stage: str,
    call_id: str,
    error: str,
    duration_ms: int = 0,
) -> LLMEvent:
    """Create a tool execution error event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.TOOL_ERROR,
        payload={
            "call_id": call_id,
            "error": error,
            "duration_ms": duration_ms,
        },
    )


def create_stream_end_event(
    stage: str,
    success: bool = True,
    error_message: str = "",
    duration_ms: int = 0,
) -> LLMEvent:
    """Create a stream end event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.STREAM_END,
        payload={
            "success": success,
            "error_message": error_message,
            "duration_ms": duration_ms,
        },
    )


def create_error_event(
    stage: str,
    error: str,
) -> LLMEvent:
    """Create a general error event."""
    return LLMEvent(
        stage=stage,
        event_type=EventType.ERROR,
        payload={
            "error": error,
        },
    )
