"""Observability module for VeriFlow-Agent.

This module provides real-time observability for LLM execution,
including event collection, trace building, and metrics aggregation.

Example usage:
    ```python
    from veriflow_agent.observability import (
        EventCollector,
        LLMEvent,
        LLMTrace,
        event_collector_context,
    )

    # Using context manager (recommended)
    with event_collector_context("coder") as collector:
        result = agent.execute_with_streaming(context, collector)
        trace = collector.get_trace()

    # Or manual usage
    collector = EventCollector("architect")
    for event in llm_stream():
        collector.on_event(event)
    trace = collector.get_trace()
    ```
"""

from __future__ import annotations

# Callback utilities
from .callbacks import (
    EventCallback,
    JsonlFileCallback,
    LoggingCallback,
    MetricsAggregationCallback,
)

# Collector and context manager
from .collector import (
    EventCollector,
    event_collector_context,
    get_active_collector,
    list_active_collectors,
    register_collector,
    unregister_collector,
)

# Event types and utilities
from .events import (
    EventType,
    LLMEvent,
    create_error_event,
    create_metrics_event,
    create_session_init_event,
    create_stream_end_event,
    create_text_delta_event,
    create_tool_complete_event,
    create_tool_error_event,
    create_tool_start_event,
)

# Trace structures
from .trace import (
    LLMStep,
    LLMTrace,
)

__all__ = [
    # Core types
    "LLMEvent",
    "LLMStep",
    "LLMTrace",
    "EventType",
    "EventCollector",

    # Context manager
    "event_collector_context",

    # Registry functions
    "get_active_collector",
    "register_collector",
    "unregister_collector",
    "list_active_collectors",

    # Event factory functions
    "create_session_init_event",
    "create_text_delta_event",
    "create_tool_start_event",
    "create_tool_complete_event",
    "create_tool_error_event",
    "create_metrics_event",
    "create_stream_end_event",

    # Callback classes
    "EventCallback",
    "LoggingCallback",
    "JsonlFileCallback",
    "MetricsAggregationCallback",
]

__version__ = "0.1.0"
