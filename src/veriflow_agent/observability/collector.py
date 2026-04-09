"""Event collector for LLM observability.

This module provides the EventCollector class for collecting and managing
LLM execution events in real-time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

from .events import LLMEvent
from .trace import LLMStep, LLMTrace

logger = logging.getLogger("veriflow.observability")


# Global registry of active collectors (for UI to find them)
_active_collectors: dict[str, EventCollector] = {}
_registry_lock = threading.Lock()


def register_collector(name: str, collector: EventCollector) -> None:
    """Register a collector in the global registry."""
    with _registry_lock:
        _active_collectors[name] = collector


def unregister_collector(name: str) -> None:
    """Unregister a collector from the global registry."""
    with _registry_lock:
        _active_collectors.pop(name, None)


def get_active_collector(name: str) -> EventCollector | None:
    """Get an active collector by name."""
    with _registry_lock:
        return _active_collectors.get(name)


def list_active_collectors() -> list[tuple[str, EventCollector]]:
    """List all active collectors."""
    with _registry_lock:
        return list(_active_collectors.items())


class EventCollector:
    """
    Collects and manages LLM execution events in real-time.

    This class provides:
    1. Event collection and storage with thread-safety
    2. Callback notification for real-time updates
    3. LLMTrace building from accumulated events
    4. Query methods for retrieving events by index or type

    Example usage:
        ```python
        # Create collector with UI callback
        def on_event(event: LLMEvent):
            print(f"New event: {event.event_type}")

        collector = EventCollector(
            stage="coder",
            callbacks=[on_event],
            build_trace=True,
        )

        # Collect events during LLM execution
        for event in llm_stream():
            collector.on_event(event)

        # Get the complete trace
        trace = collector.get_trace()
        ```
    """

    def __init__(
        self,
        stage: str,
        callbacks: list[Callable[[LLMEvent], None]] | None = None,
        build_trace: bool = True,
        register: bool = True,
    ):
        """
        Initialize the event collector.

        Args:
            stage: Pipeline stage name (e.g., "coder", "architect")
            callbacks: Optional list of callback functions to invoke on each event
            build_trace: Whether to build an LLMTrace from collected events
            register: Whether to register this collector in the global registry
        """
        self.stage = stage
        self.callbacks = callbacks or []
        self.build_trace = build_trace

        # Event storage with thread safety
        self._events: list[LLMEvent] = []
        self._lock = threading.Lock()

        # LLMTrace building
        self._trace: LLMTrace | None = None
        self._current_step: LLMStep | None = None

        if build_trace:
            self._trace = LLMTrace(
                trace_id=f"{stage}_{time.time():.0f}",
                stage=stage,
                start_time=time.time(),
            )

        # Register in global registry
        if register:
            register_collector(stage, self)

    def on_event(self, event: LLMEvent) -> None:
        """
        Process a new event: store it, trigger callbacks, and update trace.

        This method is thread-safe and can be called from multiple threads
        concurrently (e.g., from a streaming response handler).

        Args:
            event: The event to process
        """
        with self._lock:
            self._events.append(event)

            if self._trace:
                self._trace.add_event(event)
                self._update_trace_from_event(event)

        # Trigger callbacks outside the lock to avoid blocking
        for callback in self.callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Event callback failed: {e}")

    def _update_trace_from_event(self, event: LLMEvent) -> None:
        """Internal: Update the LLMTrace based on an event."""
        payload = event.payload
        event_type = event.event_type

        if event_type == "session_init":
            # Store session info in trace metadata
            if self._trace:
                self._trace.input_tokens = payload.get("input_tokens", 0)

        elif event_type == "text_delta":
            # Accumulate text generation
            if self._current_step and self._current_step.step_type == "text":
                self._current_step.text_content += payload.get("text", "")
                self._current_step.token_count += 1
            else:
                # Start new text step
                self._current_step = LLMStep(
                    step_index=len(self._trace.steps) if self._trace else 0,
                    step_type="text",
                    text_content=payload.get("text", ""),
                    start_time=event.timestamp,
                    token_count=1,
                )
                if self._trace:
                    self._trace.add_step(self._current_step)

        elif event_type == "tool_start":
            # Complete previous step
            if self._current_step:
                self._current_step.end_time = event.timestamp

            # Start new tool_call step
            self._current_step = LLMStep(
                step_index=len(self._trace.steps) if self._trace else 0,
                step_type="tool_call",
                tool_name=payload.get("tool_name", ""),
                tool_input=payload.get("tool_input", {}),
                start_time=event.timestamp,
            )
            if self._trace:
                self._trace.add_step(self._current_step)

        elif event_type in ("tool_complete", "tool_error"):
            if self._current_step and self._current_step.step_type == "tool_call":
                self._current_step.end_time = event.timestamp
                self._current_step.tool_output = payload.get("tool_output", {})
                if event_type == "tool_error":
                    self._current_step.error_message = payload.get("error", "Tool failed")

        elif event_type == "metrics":
            if self._trace:
                self._trace.input_tokens = payload.get("input_tokens", 0)
                self._trace.output_tokens = payload.get("output_tokens", 0)
                self._trace.cost_usd = payload.get("cost_usd", 0.0)

        elif event_type == "stream_end":
            if self._current_step:
                self._current_step.end_time = event.timestamp
            if self._trace:
                self._trace.finalize(
                    success=payload.get("success", True),
                    error=payload.get("error_message", ""),
                )

        elif event_type == "error":
            if self._current_step:
                self._current_step.end_time = event.timestamp
                self._current_step.error_message = payload.get("error", "Unknown error")
            if self._trace:
                self._trace.finalize(success=False, error=payload.get("error", ""))

    def get_events(
        self,
        since_index: int = 0,
        event_type: str | None = None,
    ) -> list[LLMEvent]:
        """
        Get events from the collector.

        Args:
            since_index: Return events starting from this index
            event_type: If provided, only return events of this type

        Returns:
            List of matching events
        """
        with self._lock:
            events = self._events[since_index:]
            if event_type:
                events = [e for e in events if str(e.event_type) == event_type]
            return events.copy()

    def get_all_events(self) -> list[LLMEvent]:
        """Get all collected events."""
        with self._lock:
            return self._events.copy()

    def get_event_count(self) -> int:
        """Get total number of collected events."""
        with self._lock:
            return len(self._events)

    def get_trace(self) -> LLMTrace | None:
        """Get the built LLMTrace (if trace building is enabled)."""
        with self._lock:
            return self._trace

    def clear(self) -> None:
        """Clear all collected events and reset trace."""
        with self._lock:
            self._events.clear()
            self._trace = None
            self._current_step = None

    def close(self) -> None:
        """Close the collector and unregister from global registry."""
        unregister_collector(self.stage)

    def __enter__(self) -> EventCollector:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()


@contextmanager
def event_collector_context(
    stage: str,
    callbacks: list[Callable[[LLMEvent], None]] | None = None,
    build_trace: bool = True,
    register: bool = True,
) -> Generator[EventCollector, None, None]:
    """
    Context manager for creating and using an EventCollector.

    Example:
        ```python
        with event_collector_context("coder", callbacks=[ui_callback]) as collector:
            result = agent.execute_with_streaming(context, collector)
        trace = collector.get_trace()
        ```

    Args:
        stage: Pipeline stage name
        callbacks: Optional callbacks for real-time updates
        build_trace: Whether to build an LLMTrace
        register: Whether to register in global registry

    Yields:
        Configured EventCollector instance
    """
    collector = EventCollector(
        stage=stage,
        callbacks=callbacks,
        build_trace=build_trace,
        register=register,
    )
    try:
        yield collector
    finally:
        # Ensure trace is finalized if not already done
        trace = collector.get_trace()
        if trace and not trace.end_time:
            trace.finalize(
                success=False,
                error="Collector terminated without explicit finalization",
            )
        collector.close()
