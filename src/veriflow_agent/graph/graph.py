"""LangGraph graph assembly for VeriFlow-Agent.

Defines the complete RTL design pipeline as a LangGraph StateGraph with
declarative feedback loops and multi-level rollback:

  architect → microarch → timing → coder → skill_d → lint
                                                      ↓
                          ┌───────────────────────────┘
                          │
                          ├─(pass)→ sim
                          │           ↓
                          │      (pass)→ synth
                          │                ↓
                          │           (pass)→ END
                          │
                          ├─(fail, retry<3)→ debugger ──┐
                          └─(fail, retry≥3)→ END        │
                                                        │
                          ┌─────────────────────────────┘
                          │ rollback target (multi-level):
                          │   SYNTAX error  → coder
                          │   LOGIC error   → microarch (from sim) / coder
                          │   TIMING error  → timing (from synth) / coder
                          │   RESOURCE error→ timing (from synth) / coder
                          │   UNKNOWN       → lint (conservative)

All feedback is implemented via LangGraph conditional edges, not inline loops.
Debugger is a proper graph node tracked by checkpointing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from rich.console import Console

from veriflow_agent.agents.architect import ArchitectAgent
from veriflow_agent.agents.coder import CoderAgent
from veriflow_agent.agents.debugger import DebuggerAgent
from veriflow_agent.agents.lint_agent import LintAgent
from veriflow_agent.agents.microarch import MicroArchAgent
from veriflow_agent.agents.sim_agent import SimAgent
from veriflow_agent.agents.skill_d import SkillDAgent
from veriflow_agent.agents.synth import SynthAgent
from veriflow_agent.agents.timing import TimingAgent
from veriflow_agent.graph.state import (
    MAX_RETRIES,
    StageOutput,
    VeriFlowState,
    categorize_error,
    check_token_budget,
    get_rollback_target,
)
from veriflow_agent.observability import (
    EventCollector,
    MetricsAggregationCallback,
)

logger = logging.getLogger("veriflow")
_console = Console(quiet=True)

# Stage display labels (shared with chat/formatters.py)
STAGE_LABELS = {
    "architect": "Architecture Analysis",
    "microarch": "Micro-Architecture Design",
    "timing": "Timing Model",
    "coder": "RTL Code Generation",
    "skill_d": "Quality Check",
    "lint": "Lint Check",
    "sim": "Simulation",
    "synth": "Synthesis",
    "debugger": "Debugger",
}


# ── Node wrapper functions ────────────────────────────────────────────


def _run_stage(
    state: VeriFlowState,
    agent_cls: type,
    **extra_ctx: Any,
) -> dict[str, Any]:
    """Generic stage runner: instantiate agent, execute, return state updates.

    Integrates EventCollector for real-time observability. All LLM events
    are captured and propagated to state.event_stream for UI consumption.

    Args:
        state: Current VeriFlowState.
        agent_cls: Agent class to instantiate and run.
        **extra_ctx: Additional context to pass to agent.execute().

    Returns:
        Partial state update dict for LangGraph.
    """
    agent = agent_cls()
    context = {
        "project_dir": state.get("project_dir", "."),
        "llm_api_key": state.get("llm_api_key", ""),
        "llm_base_url": state.get("llm_base_url", ""),
        "llm_model": state.get("llm_model", ""),
        **extra_ctx,
    }

    # ── Stage start ──
    label = STAGE_LABELS.get(agent.name, agent.name)
    _console.print(f"\n[bold cyan]▶ {label}[/bold cyan]")
    logger.info("[START] %s", agent.name)

    # ── Create EventCollector for this stage ──
    metrics_cb = MetricsAggregationCallback()
    collector = EventCollector(
        stage=agent.name,
        callbacks=[metrics_cb],
        build_trace=True,
        register=True,
    )

    # Inject collector into context so agents can use streaming
    context["_event_collector"] = collector

    # ── Emit stage start event ──
    stage_start_event = {
        "type": "stage_start",
        "stage": agent.name,
        "label": label,
        "timestamp": time.time(),
    }

    # ── Emit stage_start immediately (before LLM call blocks the thread) ──
    # This fires via the veriflow.stream logger → WSLogHandler → _send_to_session,
    # so the UI sees the stage turn "running" before the LLM generates any tokens.
    logging.getLogger("veriflow.stream").info(
        "STAGE_START:" + json.dumps({
            "stage": agent.name,
            "label": label,
            "timestamp": time.time(),
        })
    )

    # ── Execute ──
    t0 = time.perf_counter()
    try:
        result = agent.execute(context)
    except Exception as e:
        import traceback
        from veriflow_agent.agents.base import AgentResult
        tb = traceback.format_exc()
        logger.error("[%s] Unhandled exception:\n%s", agent.name, tb)
        result = AgentResult(
            success=False,
            stage=agent.name,
            errors=[f"Unhandled exception: {e}"],
            metadata={"traceback": tb},
        )
    elapsed = time.perf_counter() - t0

    # ── Collect trace from EventCollector ──
    trace = collector.get_trace()
    collected_events = collector.get_all_events()
    collector.close()

    # ── Post-validate outputs ──
    validation_ok, found, missing = agent.validate_outputs(context)
    if not validation_ok:
        result.warnings.append(f"Output validation: missing {missing}")
        logger.warning(
            "[%s] Output validation failed — missing: %s", agent.name, missing
        )

    # ── Stage complete print ──
    status = "PASS" if result.success else "FAIL"
    color = "green" if result.success else "red"
    _console.print(f"  [{color}]✓ {status}[/{color}] {label} ({elapsed:.1f}s)")

    if not validation_ok:
        _console.print(f"  [yellow]⚠ Missing outputs: {missing}[/yellow]")
    if result.warnings:
        for w in result.warnings[:2]:
            _console.print(f"  [yellow]⚠ {w[:120]}[/yellow]")
    if result.errors:
        for err in result.errors[:2]:
            _console.print(f"  [red]  {err[:120]}[/red]")

    # Key metrics
    for key in ("modules_generated", "quality_score", "num_cells", "module_count"):
        val = result.metrics.get(key)
        if val is not None:
            _console.print(f"  [dim]{key}: {val}[/dim]")

    # Print observability summary
    metrics_summary = metrics_cb.get_summary()
    if metrics_summary["event_count"] > 0:
        _console.print(
            f"  [dim]tokens: {metrics_summary['total_tokens']}  "
            f"cost: ${metrics_summary['total_cost_usd']:.4f}  "
            f"tool_calls: {metrics_summary['tool_calls']}  "
            f"events: {metrics_summary['event_count']}[/dim]"
        )

    logger.info("[%s] %s completed in %.2fs", status, agent.name, elapsed)

    stage_name = agent.name
    updates: dict[str, Any] = {
        "current_stage": stage_name,
    }

    # Record stage completion/failure
    completed = list(state.get("stages_completed", []))
    failed = list(state.get("stages_failed", []))

    if result.success:
        if stage_name not in completed:
            completed.append(stage_name)
        updates["stages_completed"] = completed
    else:
        if stage_name not in failed:
            failed.append(stage_name)
        updates["stages_failed"] = failed

    # Store stage output (include validation info + raw LLM output + trace)
    stage_output = StageOutput(
        success=result.success,
        artifacts=result.artifacts or found,
        metrics=result.metrics,
        errors=result.errors,
        warnings=result.warnings,
        metadata={
            **result.metadata,
            "validation_ok": validation_ok,
            "validation_missing": missing,
        },
        duration_s=round(elapsed, 2),
        raw_output=result.raw_output[:4000] if result.raw_output else "",
        llm_trace=trace,  # NEW: Attach the full LLM trace
    )
    updates[f"{stage_name}_output"] = stage_output

    # Quality gate
    quality_gates = dict(state.get("quality_gates_passed", {}))
    quality_gates[stage_name] = result.success
    updates["quality_gates_passed"] = quality_gates

    # Token tracking
    tokens_used = result.metrics.get("token_usage", 0)
    if tokens_used > 0:
        current_usage = state.get("token_usage", 0)
        usage_by_stage = dict(state.get("token_usage_by_stage", {}))
        usage_by_stage[stage_name] = usage_by_stage.get(stage_name, 0) + tokens_used
        updates["token_usage"] = current_usage + tokens_used
        updates["token_usage_by_stage"] = usage_by_stage

    # ── NEW: Observability state updates ──
    # Build event stream entries for UI consumption
    event_entries = [stage_start_event]
    for evt in collected_events:
        event_entries.append(evt.to_dict())

    # Stage end event
    event_entries.append({
        "type": "stage_end",
        "stage": agent.name,
        "label": label,
        "success": result.success,
        "duration_s": round(elapsed, 2),
        "timestamp": time.time(),
    })

    updates["event_stream"] = event_entries
    updates["event_stream_version"] = state.get("event_stream_version", 0) + 1

    # Update aggregated observability metrics
    updates["active_stage"] = stage_name
    updates["total_tokens_used"] = (
        state.get("total_tokens_used", 0) + metrics_summary["total_tokens"]
    )
    updates["total_cost_usd"] = (
        round(state.get("total_cost_usd", 0.0) + metrics_summary["total_cost_usd"], 6)
    )
    updates["total_llm_calls"] = state.get("total_llm_calls", 0) + 1
    updates["total_tool_calls"] = (
        state.get("total_tool_calls", 0) + metrics_summary["tool_calls"]
    )

    # Stage durations
    stage_durations = dict(state.get("stage_durations", {}))
    stage_durations[stage_name] = round(elapsed, 2)
    updates["stage_durations"] = stage_durations

    return updates


# ── LLM-based stage nodes ─────────────────────────────────────────────


def node_architect(state: VeriFlowState) -> dict[str, Any]:
    """Stage 1: Architecture analysis."""
    return _run_stage(state, ArchitectAgent)


def node_microarch(state: VeriFlowState) -> dict[str, Any]:
    """Stage 1.5: Micro-architecture design."""
    return _run_stage(state, MicroArchAgent)


def node_timing(state: VeriFlowState) -> dict[str, Any]:
    """Stage 2: Timing model generation."""
    return _run_stage(state, TimingAgent)


def node_coder(state: VeriFlowState) -> dict[str, Any]:
    """Stage 3: RTL code generation."""
    return _run_stage(state, CoderAgent)


def node_skill_d(state: VeriFlowState) -> dict[str, Any]:
    """Stage 3.5: Quality gatekeeper — LLM pre-check on RTL code.

    Returns success=False if quality score is below threshold,
    which routes to debugger instead of expensive iverilog/Yosys.
    """
    updates = _run_stage(state, SkillDAgent)
    skill_d_output: StageOutput | None = updates.get("skill_d_output")
    if skill_d_output and not skill_d_output.success:
        # Increment retry counter so _route_skill_d can enforce MAX_RETRIES
        retry_count = dict(state.get("retry_count", {}))
        retry_count["skill_d"] = retry_count.get("skill_d", 0) + 1
        updates["retry_count"] = retry_count
        updates["feedback_source"] = "skill_d"
        updates["target_rollback_stage"] = "coder"
    return updates


# ── EDA check nodes (no LLM) ──────────────────────────────────────────


def node_lint(state: VeriFlowState) -> dict[str, Any]:
    """Lint check: run iverilog on RTL files.

    On failure, increments retry counter, records error history,
    categorizes the error, and determines the rollback target.
    """
    updates = _run_stage(state, LintAgent)

    lint_output: StageOutput | None = updates.get("lint_output")
    if lint_output and not lint_output.success:
        # Increment retry counter
        retry_count = dict(state.get("retry_count", {}))
        retry_count["lint"] = retry_count.get("lint", 0) + 1
        updates["retry_count"] = retry_count

        # Record error history
        error_history = dict(state.get("error_history", {}))
        lint_errors = list(error_history.get("lint", []))
        lint_errors.append("\n".join(lint_output.errors))
        error_history["lint"] = lint_errors
        updates["error_history"] = error_history

        # Classify error and set rollback target
        category = categorize_error(lint_output.errors)
        error_categories = dict(state.get("error_categories", {}))
        error_categories["lint"] = category.value
        updates["error_categories"] = error_categories
        updates["target_rollback_stage"] = get_rollback_target(category, "lint")
        logger.info("Lint error categorized as %s → rollback to %s",
                     category.value, updates["target_rollback_stage"])

    # Set feedback_source in state for debugger routing (C3 fix)
    if lint_output and not lint_output.success:
        updates["feedback_source"] = "lint"

    return updates


def node_sim(state: VeriFlowState) -> dict[str, Any]:
    """Simulation check: run iverilog + vvp on all testbenches.

    On failure, increments retry counter, records error history,
    categorizes the error, and determines the rollback target.
    """
    updates = _run_stage(state, SimAgent)

    sim_output: StageOutput | None = updates.get("sim_output")
    if sim_output and not sim_output.success:
        retry_count = dict(state.get("retry_count", {}))
        retry_count["sim"] = retry_count.get("sim", 0) + 1
        updates["retry_count"] = retry_count

        error_history = dict(state.get("error_history", {}))
        sim_errors = list(error_history.get("sim", []))
        sim_errors.append("\n".join(sim_output.errors))
        error_history["sim"] = sim_errors
        updates["error_history"] = error_history

        # Classify error and set rollback target
        category = categorize_error(sim_output.errors)
        error_categories = dict(state.get("error_categories", {}))
        error_categories["sim"] = category.value
        updates["error_categories"] = error_categories
        updates["target_rollback_stage"] = get_rollback_target(category, "sim")
        logger.info("Sim error categorized as %s → rollback to %s",
                     category.value, updates["target_rollback_stage"])

    # Set feedback_source in state for debugger routing (C3 fix)
    if sim_output and not sim_output.success:
        updates["feedback_source"] = "sim"

    return updates


def node_synth(state: VeriFlowState) -> dict[str, Any]:
    """Synthesis check: run Yosys on RTL files.

    On failure, increments retry counter, records error history,
    categorizes the error, and determines the rollback target.
    """
    updates = _run_stage(state, SynthAgent)

    synth_output: StageOutput | None = updates.get("synth_output")
    if synth_output and not synth_output.success:
        retry_count = dict(state.get("retry_count", {}))
        retry_count["synth"] = retry_count.get("synth", 0) + 1
        updates["retry_count"] = retry_count

        error_history = dict(state.get("error_history", {}))
        synth_errors = list(error_history.get("synth", []))
        synth_errors.append("\n".join(synth_output.errors))
        error_history["synth"] = synth_errors
        updates["error_history"] = error_history

        # Classify error and set rollback target
        category = categorize_error(synth_output.errors)
        error_categories = dict(state.get("error_categories", {}))
        error_categories["synth"] = category.value
        updates["error_categories"] = error_categories
        updates["target_rollback_stage"] = get_rollback_target(category, "synth")
        logger.info("Synth error categorized as %s → rollback to %s",
                     category.value, updates["target_rollback_stage"])

    # Set feedback_source in state for debugger routing (C3 fix)
    if synth_output and not synth_output.success:
        updates["feedback_source"] = "synth"

    return updates


# ── Debugger node ──────────────────────────────────────────────────────


def node_debugger(state: VeriFlowState) -> dict[str, Any]:
    """Debugger: invoke LLM to fix RTL based on accumulated error context.

    Reads feedback_source from state to know which check triggered this.
    Passes error history to LLM for accumulated context.
    """
    feedback_source = state.get("feedback_source", "lint")

    # Gather current error log from the triggering check's output
    error_log = ""
    stage_output_key = f"{feedback_source}_output"
    stage_output = state.get(stage_output_key)
    if stage_output:
        error_log = "\n".join(stage_output.errors) if stage_output.errors else ""

    # Gather error history for this check point
    error_history_map = state.get("error_history", {})
    error_history = list(error_history_map.get(feedback_source, []))

    project_dir = Path(state.get("project_dir", "."))
    timing_yaml = str(project_dir / "workspace" / "docs" / "timing_model.yaml")

    debugger_ctx = {
        "project_dir": str(project_dir),
        "error_type": feedback_source,
        "error_log": error_log[:5000],
        "feedback_source": feedback_source,
        "error_history": error_history,
        "timing_model_yaml": timing_yaml,
    }

    updates = _run_stage(state, DebuggerAgent, **debugger_ctx)
    return updates


# ── Routing helpers ───────────────────────────────────────────────────


def _route_skill_d(state: VeriFlowState) -> str:
    """Route after SkillD quality gate."""
    skill_d_output = state.get("skill_d_output")
    if skill_d_output and skill_d_output.success:
        return "lint"

    within_budget, msg = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at skill_d: %s", msg)
        return END

    retry_count = state.get("retry_count", {})
    skill_d_retries = retry_count.get("skill_d", 0)
    if skill_d_retries < MAX_RETRIES:
        logger.info(
            "SkillD quality gate failed (attempt %d/%d), routing to debugger",
            skill_d_retries, MAX_RETRIES,
        )
        return "debugger"

    logger.warning("SkillD retry limit (%d) reached, terminating pipeline", MAX_RETRIES)
    return END


def _route_lint(state: VeriFlowState) -> str:
    """Route after lint check."""
    lint_output = state.get("lint_output")
    if lint_output and lint_output.success:
        return "sim"

    within_budget, msg = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at lint: %s", msg)
        return END

    retry_count = state.get("retry_count", {})
    lint_retries = retry_count.get("lint", 0)
    if lint_retries < MAX_RETRIES:
        logger.info(
            "Lint failed (attempt %d/%d), routing to debugger",
            lint_retries,
            MAX_RETRIES,
        )
        return "debugger"
    return END


def _route_sim(state: VeriFlowState) -> str:
    """Route after simulation check."""
    sim_output = state.get("sim_output")
    if sim_output and sim_output.success:
        return "synth"

    within_budget, msg = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at sim: %s", msg)
        return END

    retry_count = state.get("retry_count", {})
    sim_retries = retry_count.get("sim", 0)
    if sim_retries < MAX_RETRIES:
        logger.info(
            "Sim failed (attempt %d/%d), routing to debugger",
            sim_retries,
            MAX_RETRIES,
        )
        return "debugger"
    return END


def _route_synth(state: VeriFlowState) -> str:
    """Route after synthesis check."""
    synth_output = state.get("synth_output")
    if synth_output and synth_output.success:
        logger.info("Synthesis passed! Pipeline complete.")
        return END

    within_budget, msg = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at synth: %s", msg)
        return END

    retry_count = state.get("retry_count", {})
    synth_retries = retry_count.get("synth", 0)
    if synth_retries < MAX_RETRIES:
        logger.info(
            "Synth failed (attempt %d/%d), routing to debugger",
            synth_retries,
            MAX_RETRIES,
        )
        return "debugger"
    return END


def _route_debugger(state: VeriFlowState) -> str:
    """Route debugger output to the selected rollback target."""
    target = state.get("target_rollback_stage", "lint")
    logger.info("Debugger routing to rollback target: %s", target)
    return target


# ── Graph builder ─────────────────────────────────────────────────────


def create_veriflow_graph(
    *,
    with_checkpointer: bool = True,
) -> StateGraph:
    """Build the VeriFlow LangGraph pipeline.

    Pipeline flow:
      architect → microarch → timing → coder → skill_d → lint
                                                            ↓
      ┌─────────────────────────────────────────────────────┘
      │
      ├─(pass)→ sim ─(pass)→ synth ─(pass)→ END
      │           │               │
      │           └─(fail)→ debugger ──→ target_rollback_stage
      │                           │
      └───────────────────────────┘

    Multi-level rollback targets:
      SYNTAX  → coder      (code generation fix)
      LOGIC   → microarch  (design/arch fix, from sim) / coder
      TIMING  → timing     (timing model fix, from synth) / coder
      RESOURCE→ timing     (constraint fix, from synth) / coder
      UNKNOWN → lint       (conservative full rollback)

    Args:
        with_checkpointer: Whether to compile with MemorySaver for
                           checkpointing and resume support.

    Returns:
        Compiled StateGraph ready for invoke/stream.
    """
    builder = StateGraph(VeriFlowState)

    # ── Add nodes ──────────────────────────────────────────────────
    builder.add_node("architect", node_architect)
    builder.add_node("microarch", node_microarch)
    builder.add_node("timing", node_timing)
    builder.add_node("coder", node_coder)
    builder.add_node("skill_d", node_skill_d)
    builder.add_node("lint", node_lint)
    builder.add_node("sim", node_sim)
    builder.add_node("synth", node_synth)
    builder.add_node("debugger", node_debugger)

    # ── Linear edges (always-executed stages) ──────────────────────
    builder.add_edge(START, "architect")
    builder.add_edge("architect", "microarch")
    builder.add_edge("microarch", "timing")
    builder.add_edge("timing", "coder")
    builder.add_edge("coder", "skill_d")

    # ── skill_d conditional edge (quality gate) ─────────────────────
    # pass → lint (proceed to EDA checks)
    # fail → debugger (low quality, fix before expensive EDA)
    builder.add_conditional_edges("skill_d", _route_skill_d)

    # ── Conditional edges (quality gates + token budget) ────────────

    # After lint: pass → sim, fail → debugger or END
    builder.add_conditional_edges("lint", _route_lint)

    # After sim: pass → synth, fail → debugger or END
    builder.add_conditional_edges("sim", _route_sim)

    # After synth: pass → END, fail → debugger or END
    builder.add_conditional_edges("synth", _route_synth)

    # Debugger → target_rollback_stage (multi-level rollback)
    builder.add_conditional_edges("debugger", _route_debugger)

    # ── Compile ────────────────────────────────────────────────────
    checkpointer = MemorySaver() if with_checkpointer else None
    graph = builder.compile(
        checkpointer=checkpointer,
        name="veriflow-pipeline",
    )

    return graph


def build_pipeline_graph(
    *,
    with_checkpointer: bool = True,
):
    """Backward-compatible alias for create_veriflow_graph."""
    return create_veriflow_graph(with_checkpointer=with_checkpointer)
