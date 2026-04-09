"""State definitions for VeriFlow LangGraph.

This module defines the TypedDict state structure used by the LangGraph
state machine, including per-stage outputs, error categorization,
multi-level rollback targeting, and token budget tracking.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages

logger = logging.getLogger("veriflow")


# ── Error categorization for multi-level rollback ────────────────────────


class ErrorCategory(str, Enum):
    """Classification of RTL errors for rollback target selection.

    SYNTAX:    Compile/lint errors (missing semicolons, typos, undeclared wires)
    LOGIC:     Functional/simulation failures (wrong outputs, assertion violations)
    TIMING:    Synthesis timing violations (setup/hold, clock skew)
    RESOURCE:  Synthesis resource overflows (area, power, cell count exceeds target)
    UNKNOWN:   Unclassifiable errors → conservative full rollback to lint
    """
    SYNTAX = "syntax"
    LOGIC = "logic"
    TIMING = "timing"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


# Keyword patterns for error classification
_SYNTAX_PATTERNS = [
    r"syntax\s+error",
    r"unexpected\s+token",
    r"undeclared\s+identifier",
    r"unknown\s+port",
    r"port\s+width\s+mismatch",
    r"missing\s+semicolon",
    r"error:\s*\d+",                # generic compiler error with line number
    r"iverilog.*error",
    r"compilation\s+failed",
]

_LOGIC_PATTERNS = [
    r"simulation\s+fail",
    r"mismatch",
    r"assertion\s+(fail|violation)",
    r"wrong\s+(output|result|value)",
    r"timeout",
    r"VVP.*F",
    r"testbench.*fail",
    r"expected.*got",
]

_TIMING_PATTERNS = [
    r"timing\s+violation",
    r"setup\s+(time|violation|check)",
    r"hold\s+(time|violation|check)",
    r"clock\s+skew",
    r"slack.*negative",
    r"max\s+frequency",
    r"critical\s+path",
    r"met\s*.*unmet",
]

_RESOURCE_PATTERNS = [
    r"area\s+(exceeds|over|limit)",
    r"cell\s+count\s+(exceeds|over)",
    r"resource\s+(exceeds|over|limit)",
    r"power\s+(exceeds|over|limit)",
    r"LUT\s+\w*\s*(exceeds|over)",
    r"FF\s+\w*\s*(exceeds|over)",
    r"BRAM\s+\w*\s*(exceeds|over)",
    r"DSP\s+\w*\s*(exceeds|over)",
]


def categorize_error(error_messages: list[str]) -> ErrorCategory:
    """Classify errors by scanning error messages against keyword patterns.

    Args:
        error_messages: List of error strings from the failed check.

    Returns:
        The most specific ErrorCategory found, or UNKNOWN if no match.
    """
    combined = "\n".join(error_messages).lower()

    for pattern in _TIMING_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return ErrorCategory.TIMING

    for pattern in _RESOURCE_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return ErrorCategory.RESOURCE

    for pattern in _SYNTAX_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return ErrorCategory.SYNTAX

    for pattern in _LOGIC_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return ErrorCategory.LOGIC

    return ErrorCategory.UNKNOWN


def get_rollback_target(
    error_category: ErrorCategory,
    feedback_source: str,
) -> str:
    """Determine the rollback target stage based on error category and source.

    Rollback strategy:
    - SYNTAX errors: Roll back to coder (code generation issue)
    - LOGIC errors:
        - From sim → microarch (design/architecture issue)
        - From lint/synth → coder (code generation issue)
    - TIMING/RESOURCE errors:
        - From synth → timing (timing model needs revision)
        - From lint/sim → coder (code didn't follow timing model)
    - UNKNOWN → lint (conservative full rollback)
    - skill_d failures → coder (quality pre-check, no EDA involved)

    Args:
        error_category: Classified error type.
        feedback_source: Which check triggered the failure
                         ("lint"/"sim"/"synth"/"skill_d").

    Returns:
        Target stage name for rollback ("coder", "microarch", "timing", "lint").
    """
    # SkillD quality gate always rolls back to coder
    if feedback_source == "skill_d":
        return "coder"

    if error_category == ErrorCategory.SYNTAX:
        return "coder"

    if error_category == ErrorCategory.LOGIC:
        if feedback_source == "sim":
            return "microarch"
        return "coder"

    if error_category in (ErrorCategory.TIMING, ErrorCategory.RESOURCE):
        if feedback_source == "synth":
            return "timing"
        return "coder"

    # UNKNOWN → conservative full rollback
    return "lint"


# ── Token budget ─────────────────────────────────────────────────────────

DEFAULT_TOKEN_BUDGET = 1_000_000  # 1M tokens default budget


def check_token_budget(state: VeriFlowState) -> tuple[bool, str]:
    """Check if token usage is within budget.

    Args:
        state: Current pipeline state.

    Returns:
        Tuple of (is_within_budget, message).
        is_within_budget is False only when budget is exceeded (>100%).
        Message is "" when under 80%, a warning at 80-100%, or an error above 100%.
    """
    budget = state.get("token_budget", DEFAULT_TOKEN_BUDGET)
    usage = state.get("token_usage", 0)

    if budget <= 0:
        return True, ""

    ratio = usage / budget
    if ratio >= 1.0:
        return False, f"Token budget exceeded: {usage}/{budget} ({ratio:.0%})"
    if ratio >= 0.8:
        return True, f"Token budget warning: {usage}/{budget} ({ratio:.0%})"
    return True, ""


@dataclass
class StageOutput:
    """Standardized output format for each pipeline stage.

    Attributes:
        success: Whether the stage completed successfully
        artifacts: List of file paths generated by this stage
        metrics: Key performance indicators and measurements
        errors: List of error messages if any
        warnings: List of warning messages
        metadata: Additional stage-specific data
        duration_s: Wall-clock time of this stage in seconds
    """
    success: bool
    artifacts: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    raw_output: str = ""
    llm_trace: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "errors": self.errors,
            "warnings": self.warnings,
            "metadata": self.metadata,
            "duration_s": self.duration_s,
            "raw_output": self.raw_output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageOutput:
        """Create from dictionary."""
        return cls(
            success=data.get("success", False),
            artifacts=data.get("artifacts", []),
            metrics=data.get("metrics", {}),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
            metadata=data.get("metadata", {}),
            duration_s=data.get("duration_s", 0.0),
            raw_output=data.get("raw_output", ""),
        )


# Maximum retry attempts per check point (lint, sim, synth)
MAX_RETRIES = 3


class VeriFlowState(TypedDict):
    """Complete state for the VeriFlow LangGraph.

    Single pipeline mode — all stages always execute.
    Multi-level rollback — debugger routes to different targets based on error category.

    Attributes:
        # Project Configuration
        project_dir: Root directory of the project

        # Execution State
        current_stage: Name of currently executing stage
        stages_completed: List of successfully completed stages
        stages_failed: List of stages that failed
        retry_count: Per-check-point retry counter (lint/sim/synth)
        error_history: Per-check-point accumulated error messages
        feedback_source: Which check point triggered the debugger
                         ("lint" / "sim" / "synth" / "")

        # Multi-level Rollback
        error_categories: Per-check-point classified error category
        target_rollback_stage: Where debugger should route after fixing
                               ("coder" / "microarch" / "timing" / "lint")

        # Token Budget
        token_budget: Total token budget for this pipeline run
        token_usage: Accumulated token usage so far
        token_usage_by_stage: Per-stage token usage breakdown

        # Stage Outputs
        architect_output: StageOutput
        microarch_output: StageOutput
        timing_output: StageOutput
        coder_output: StageOutput
        skill_d_output: StageOutput
        lint_output: StageOutput
        sim_output: StageOutput
        synth_output: StageOutput
        debugger_output: StageOutput

        # Quality Gates
        quality_gates_passed: Map of gate name to pass/fail

        # Debug/Logging
        messages: Accumulated log messages
    """

    # Project Configuration
    project_dir: str

    # LLM Configuration (propagated from session config / config.json)
    llm_api_key: str
    llm_base_url: str
    llm_model: str

    # Execution State
    current_stage: str
    stages_completed: Annotated[list[str], lambda x, y: list(dict.fromkeys(x + y))]
    stages_failed: list[str]
    retry_count: dict[str, int]
    error_history: dict[str, list[str]]
    feedback_source: str  # "lint" | "sim" | "synth" | ""

    # Multi-level Rollback
    error_categories: dict[str, str]         # checkpoint → ErrorCategory value
    target_rollback_stage: str               # "coder" | "microarch" | "timing" | "lint"

    # Token Budget
    token_budget: int
    token_usage: int
    token_usage_by_stage: dict[str, int]

    # Stage Outputs
    architect_output: StageOutput | None
    microarch_output: StageOutput | None
    timing_output: StageOutput | None
    coder_output: StageOutput | None
    skill_d_output: StageOutput | None
    lint_output: StageOutput | None
    sim_output: StageOutput | None
    synth_output: StageOutput | None
    debugger_output: StageOutput | None

    # Quality Gates
    quality_gates_passed: dict[str, bool]

    # ── Real-time Observability (Phase 1 addition) ────────────────────────
    event_stream: Annotated[list[dict], lambda x, y: x + y]
    event_stream_version: int
    active_stage: str | None
    active_stage_start_time: float | None
    active_llm_call_start_time: float | None
    total_tokens_used: int
    total_cost_usd: float
    total_llm_calls: int
    total_tool_calls: int
    stage_durations: dict[str, float]

    # Debug/Logging
    messages: Annotated[Sequence, add_messages]


def create_initial_state(
    project_dir: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
) -> VeriFlowState:
    """Create initial state for a new pipeline run.

    Args:
        project_dir: Path to the project directory
        token_budget: Total token budget for the pipeline run

    Returns:
        Initial VeriFlowState with default values
    """
    return VeriFlowState(
        project_dir=project_dir,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        current_stage="",
        stages_completed=[],
        stages_failed=[],
        retry_count={
            "lint": 0,
            "sim": 0,
            "synth": 0,
        },
        error_history={
            "lint": [],
            "sim": [],
            "synth": [],
        },
        feedback_source="",
        error_categories={
            "lint": "",
            "sim": "",
            "synth": "",
        },
        target_rollback_stage="lint",
        token_budget=token_budget,
        token_usage=0,
        token_usage_by_stage={},
        architect_output=None,
        microarch_output=None,
        timing_output=None,
        coder_output=None,
        skill_d_output=None,
        lint_output=None,
        sim_output=None,
        synth_output=None,
        debugger_output=None,
        quality_gates_passed={},
        # Observability defaults
        event_stream=[],
        event_stream_version=0,
        active_stage=None,
        active_stage_start_time=None,
        active_llm_call_start_time=None,
        total_tokens_used=0,
        total_cost_usd=0.0,
        total_llm_calls=0,
        total_tool_calls=0,
        stage_durations={},
        messages=[],
    )
