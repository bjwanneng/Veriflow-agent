"""LLM execution tracing structures.

This module defines the data structures for tracking complete LLM execution traces,
including all steps, metrics, and raw events.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from .events import LLMEvent


@dataclass
class LLMStep:
    """A single step in an LLM execution trace.

    A step can be:
    - text: Text generation (accumulated tokens)
    - tool_call: Tool invocation
    - tool_result: Tool execution result
    - error: Error during execution

    Attributes:
        step_index: Sequential index within the trace
        step_type: Type of step
        start_time: Unix timestamp when step started
        end_time: Unix timestamp when step ended (0 if ongoing)

        # For text steps
        text_content: Accumulated generated text
        token_count: Number of tokens generated

        # For tool steps
        tool_name: Name of the tool being called
        tool_input: Input parameters for the tool
        tool_output: Output from the tool (for tool_result steps)

        # For error steps
        error_message: Error description
    """

    step_index: int = 0
    step_type: Literal["text", "tool_call", "tool_result", "error"] = "text"
    start_time: float = 0.0
    end_time: float = 0.0

    # Text fields
    text_content: str = ""
    token_count: int = 0

    # Tool fields
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: dict[str, Any] = field(default_factory=dict)

    # Error fields
    error_message: str = ""

    @property
    def duration_ms(self) -> int:
        """Calculate step duration in milliseconds."""
        end = self.end_time if self.end_time > 0 else time.time()
        return int((end - self.start_time) * 1000)

    @property
    def is_complete(self) -> bool:
        """Check if the step has completed."""
        return self.end_time > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert step to dictionary."""
        return {
            "step_index": self.step_index,
            "step_type": self.step_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "text_content": self.text_content,
            "token_count": self.token_count,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
            "error_message": self.error_message,
        }


@dataclass
class LLMTrace:
    """Complete trace of an LLM execution.

    Captures all steps, metrics, and raw events for a single LLM call.

    Attributes:
        trace_id: Unique identifier for this trace
        stage: Pipeline stage name (e.g., "coder", "architect")
        start_time: Unix timestamp when trace started
        end_time: Unix timestamp when trace ended (0 if ongoing)

        steps: Sequential list of execution steps
        raw_events: Original LLM events (for debugging/replay)

        # Metrics
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        total_tokens: Total token count (calculated)
        cost_usd: Estimated cost in USD
        latency_ms: Total execution time in milliseconds

        # Result
        success: Whether execution completed successfully
        error_message: Error description if failed
    """

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    stage: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0

    steps: list[LLMStep] = field(default_factory=list)
    raw_events: list[dict] = field(default_factory=list)

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    success: bool = False
    error_message: str = ""

    def add_step(self, step: LLMStep) -> None:
        """Add a step to the trace, automatically assigning index."""
        step.step_index = len(self.steps)
        self.steps.append(step)

    def add_event(self, event: LLMEvent | dict) -> None:
        """Add a raw event to the trace."""
        if isinstance(event, LLMEvent):
            self.raw_events.append(event.to_dict())
        else:
            self.raw_events.append(event)

    def update_metrics(
        self,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Update trace metrics."""
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens
        if cost_usd is not None:
            self.cost_usd = cost_usd
        self.total_tokens = self.input_tokens + self.output_tokens

    def finalize(self, success: bool = True, error: str = "") -> None:
        """Finalize the trace with completion status."""
        self.end_time = time.time()
        self.success = success
        self.error_message = error
        self.latency_ms = int((self.end_time - self.start_time) * 1000)
        self.total_tokens = self.input_tokens + self.output_tokens

        # Estimate cost if not set
        if self.cost_usd == 0 and self.total_tokens > 0:
            self.cost_usd = self._estimate_cost()

    # claude-sonnet-4-6 pricing — update when model changes.
    # Override via VERIFLOW_COST_INPUT_PER_M / VERIFLOW_COST_OUTPUT_PER_M (USD per 1M tokens).
    _INPUT_COST_PER_M: float = 3.0
    _OUTPUT_COST_PER_M: float = 15.0

    def _estimate_cost(self) -> float:
        """Estimate cost based on tokens using configurable per-model pricing."""
        import os
        input_per_m = float(os.environ.get("VERIFLOW_COST_INPUT_PER_M", self._INPUT_COST_PER_M))
        output_per_m = float(os.environ.get("VERIFLOW_COST_OUTPUT_PER_M", self._OUTPUT_COST_PER_M))
        return (self.input_tokens * input_per_m + self.output_tokens * output_per_m) / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        """Convert trace to dictionary."""
        return {
            "trace_id": self.trace_id,
            "stage": self.stage,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "latency_ms": self.latency_ms,
            "steps": [s.to_dict() for s in self.steps],
            "raw_events": self.raw_events,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "success": self.success,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LLMTrace:
        """Create trace from dictionary."""
        trace = cls(
            trace_id=data.get("trace_id", ""),
            stage=data.get("stage", ""),
            start_time=data.get("start_time", 0.0),
            end_time=data.get("end_time", 0.0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cost_usd=data.get("cost_usd", 0.0),
            success=data.get("success", False),
            error_message=data.get("error_message", ""),
        )
        # Restore steps
        for step_data in data.get("steps", []):
            step = LLMStep(**step_data)
            trace.steps.append(step)
        # Restore raw events
        trace.raw_events = data.get("raw_events", [])
        return trace
