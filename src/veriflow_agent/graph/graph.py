"""LangGraph graph assembly for VeriFlow-Agent.

Defines the complete RTL design pipeline as a LangGraph StateGraph with
LLM-driven intelligent routing via a Supervisor node:

  architect → microarch → timing → coder → skill_d → lint → sim → synth → END
                                                      ↓       ↓       ↓
                                                   (fail)   (fail)   (fail)
                                                      ↓       ↓       ↓
                                                   supervisor(LLM analysis)
                                                      │
                                                      ├─ retry_stage(debugger) → debugger → supervisor
                                                      ├─ retry_stage(coder, hint) → coder → skill_d → lint
                                                      ├─ escalate_stage(microarch) → microarch → ...
                                                      └─ abort → END

The Supervisor replaces mechanical regex-based routing with LLM-driven
root cause analysis and intelligent stage targeting. Falls back to
mechanical categorization when the Supervisor LLM call fails.
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
from veriflow_agent.agents.supervisor import SupervisorAgent
from veriflow_agent.agents.synth import SynthAgent
from veriflow_agent.agents.timing import TimingAgent
from veriflow_agent.graph.state import (
    BUDGET_MODE_CRITICAL,
    BUDGET_MODE_ECONOMY,
    MAX_RETRIES,
    MAX_SUPERVISOR_CALLS,
    MAX_TOTAL_RETRIES,
    RETRY_TIERS,
    TIER_ESCALATION_ORDER,
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


def _get_stage_labels() -> dict[str, str]:
    """Lazy import to avoid circular dependency (graph → chat.formatters → chat → graph)."""
    from veriflow_agent.chat.formatters import STAGE_LABELS
    return STAGE_LABELS

# Human-readable progress messages for agent lifecycle
_AGENT_PROGRESS: dict[str, dict[str, str]] = {
    "architect":  {"input": "读取需求文档，解析设计规格…", "llm": "调用 LLM 分析架构，提取接口定义…", "output": "解析输出，生成 spec.json…"},
    "microarch":  {"input": "读取 spec.json 架构规格…",   "llm": "调用 LLM 设计微架构…",           "output": "生成 micro_arch.md…"},
    "timing":     {"input": "读取架构和微架构文档…",       "llm": "调用 LLM 建立时序模型…",         "output": "生成 timing_model.yaml 和 testbench…"},
    "coder":      {"input": "读取时序模型和设计规格…",     "llm": "调用 LLM 生成 Verilog RTL 代码…","output": "写入 .v 文件…"},
    "skill_d":    {"input": "读取 RTL 源文件…",           "llm": "执行代码质量分析…",              "output": "生成质量报告…"},
    "lint":       {"input": "准备 lint 检查…",            "llm": "运行 iverilog 语法检查…",        "output": "分析 lint 结果…"},
    "sim":        {"input": "编译 testbench…",            "llm": "运行功能仿真…",                  "output": "分析仿真波形…"},
    "synth":      {"input": "读取 RTL 文件…",             "llm": "运行 Yosys 逻辑综合…",           "output": "生成综合报告…"},
    "debugger":   {"input": "读取错误信息…",              "llm": "分析错误原因，生成修复方案…",     "output": "应用修复…"},
}


def _emit_progress(stage: str, message: str) -> None:
    """Emit a progress message via veriflow.stream logger for TUI consumption."""
    stream_logger = logging.getLogger("veriflow.stream")
    stream_logger.info("PROGRESS:" + json.dumps({"stage": stage, "message": message}))


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

    # Override agent's llm_backend from state config (user may have set openai/langchain)
    state_backend = state.get("llm_backend", "")
    if state_backend and state_backend != agent.llm_backend:
        logger.debug(
            "[%s] Overriding llm_backend: %s → %s (from state config)",
            agent.name, agent.llm_backend, state_backend,
        )
        agent.llm_backend = state_backend

    context = {
        "project_dir": state.get("project_dir", "."),
        "llm_api_key": state.get("llm_api_key", ""),
        "llm_base_url": state.get("llm_base_url", ""),
        "llm_model": state.get("llm_model", ""),
        "budget_mode": state.get("budget_mode", "normal"),
        **extra_ctx,
    }

    # ── Stage start ──
    label = _get_stage_labels().get(agent.name, agent.name)
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

    # ── Emit progress: reading inputs ──
    _emit_progress(agent.name, f"{label}: 读取输入文件…")

    # ── Execute ──
    progress = _AGENT_PROGRESS.get(agent.name, {})
    _emit_progress(agent.name, progress.get("llm", f"{label}: 调用 LLM…"))
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

    _emit_progress(agent.name, progress.get("output", f"{label}: 处理输出…"))

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
    # Safety: serialize llm_trace to avoid MemorySaver pickle failures
    safe_trace = None
    if trace:
        try:
            if hasattr(trace, "to_dict"):
                safe_trace = json.loads(json.dumps(trace.to_dict()))
            else:
                safe_trace = json.loads(json.dumps(trace))
        except (TypeError, ValueError, AttributeError) as e:
            logger.debug("llm_trace serialization failed for %s: %s", stage_name, e)
            safe_trace = None

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
        llm_trace=safe_trace,
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
    """Stage 3: RTL code generation.

    On failure, increments retry counter and sets feedback_source/target
    so the routing function can redirect to the debugger.
    """
    extra_ctx: dict[str, Any] = {}
    # Unified hint injection: supervisor_hint is the primary source of guidance.
    # strategy_override may also contain per-stage instructions from escalation.
    supervisor_hint = state.get("supervisor_hint", "")
    strategy_override = state.get("strategy_override", {})

    # Pass supervisor guidance to CoderAgent
    # Prefer strategy_override (explicit per-stage instruction) if available,
    # otherwise use supervisor_hint as fallback
    if "coder" in strategy_override:
        extra_ctx["supervisor_hint"] = strategy_override["coder"]
    elif supervisor_hint:
        extra_ctx["supervisor_hint"] = supervisor_hint

    retry_tier = state.get("retry_tier", {})
    if retry_tier.get("coder", "simple_retry") != "simple_retry":
        extra_ctx["retry_tier"] = retry_tier["coder"]

    updates = _run_stage(state, CoderAgent, **extra_ctx)
    coder_output: StageOutput | None = updates.get("coder_output")
    if coder_output and not coder_output.success:
        # Increment retry counter
        retry_count = dict(state.get("retry_count", {}))
        retry_count["coder"] = retry_count.get("coder", 0) + 1
        updates["retry_count"] = retry_count

        # Increment total retries
        total_retries = dict(state.get("total_retries", {}))
        total_retries["coder"] = total_retries.get("coder", 0) + 1
        updates["total_retries"] = total_retries

        # Record error history
        error_history = dict(state.get("error_history", {}))
        coder_errors = list(error_history.get("coder", []))
        coder_errors.append("\n".join(coder_output.errors))
        error_history["coder"] = coder_errors
        updates["error_history"] = error_history

        # Routing decision delegated to Supervisor LLM — node only records the failure
        updates["feedback_source"] = "coder"
    return updates


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

        # Increment total retries
        total_retries = dict(state.get("total_retries", {}))
        total_retries["skill_d"] = total_retries.get("skill_d", 0) + 1
        updates["total_retries"] = total_retries

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

        # Increment total retries
        total_retries = dict(state.get("total_retries", {}))
        total_retries["lint"] = total_retries.get("lint", 0) + 1
        updates["total_retries"] = total_retries

        # Record error history
        error_history = dict(state.get("error_history", {}))
        lint_errors = list(error_history.get("lint", []))
        lint_errors.append("\n".join(lint_output.errors))
        error_history["lint"] = lint_errors
        updates["error_history"] = error_history

        # Routing decision delegated to Supervisor LLM — node only records the failure

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

        # Increment total retries
        total_retries = dict(state.get("total_retries", {}))
        total_retries["sim"] = total_retries.get("sim", 0) + 1
        updates["total_retries"] = total_retries

        error_history = dict(state.get("error_history", {}))
        sim_errors = list(error_history.get("sim", []))
        sim_errors.append("\n".join(sim_output.errors))
        error_history["sim"] = sim_errors
        updates["error_history"] = error_history

        # Routing decision delegated to Supervisor LLM — node only records the failure

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

        # Increment total retries
        total_retries = dict(state.get("total_retries", {}))
        total_retries["synth"] = total_retries.get("synth", 0) + 1
        updates["total_retries"] = total_retries

        error_history = dict(state.get("error_history", {}))
        synth_errors = list(error_history.get("synth", []))
        synth_errors.append("\n".join(synth_output.errors))
        error_history["synth"] = synth_errors
        updates["error_history"] = error_history

        # Routing decision delegated to Supervisor LLM — node only records the failure

    # Set feedback_source in state for debugger routing (C3 fix)
    if synth_output and not synth_output.success:
        updates["feedback_source"] = "synth"

    return updates


# ── Debugger node ──────────────────────────────────────────────────────


def node_debugger(state: VeriFlowState) -> dict[str, Any]:
    """Debugger: invoke LLM to fix RTL based on accumulated error context.

    Reads feedback_source from state to know which check triggered this.
    Passes error history to LLM for accumulated context.

    After the fix, uses LLM error analysis (from DebuggerAgent) to
    override the mechanical rollback target with an LLM-determined one.
    Falls back to the mechanical target if LLM analysis fails.
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
        "llm_api_key": state.get("llm_api_key", ""),
        "llm_base_url": state.get("llm_base_url", ""),
        "llm_model": state.get("llm_model", ""),
        "strategy_override": state.get("strategy_override", {}),
        "retry_tier": state.get("retry_tier", {}),
        "budget_mode": state.get("budget_mode", "normal"),
        "supervisor_hint": state.get("supervisor_hint", ""),
    }

    updates = _run_stage(state, DebuggerAgent, **debugger_ctx)

    # ── Override rollback target with LLM analysis if available ──
    # When supervisor has already decided a target (supervisor_hint is set),
    # the debugger's internal LLM analysis should NOT override it.
    # Supervisor is the "brain" — debugger is the "scalpel".
    debugger_output: StageOutput | None = updates.get("debugger_output")
    has_supervisor_decision = bool(state.get("supervisor_hint", ""))
    if debugger_output and debugger_output.metrics:
        llm_rollback_target = debugger_output.metrics.get("llm_rollback_target", "")
        if llm_rollback_target and not has_supervisor_decision:
            # Only override when supervisor has NOT already decided
            mechanical_target = updates.get("target_rollback_stage", "lint")
            updates["target_rollback_stage"] = llm_rollback_target
            logger.info(
                "Rollback target: mechanical=%s → LLM=%s (reasoning: %s)",
                mechanical_target,
                llm_rollback_target,
                debugger_output.metrics.get("llm_error_reasoning", "")[:100],
            )
        elif has_supervisor_decision:
            logger.info(
                "Debugger analysis skipped target override — "
                "supervisor already decided: %s",
                state.get("target_rollback_stage", "unknown"),
            )

    return updates


# ── Routing helpers ───────────────────────────────────────────────────


def _check_retry_budget(state: VeriFlowState, checkpoint: str) -> str:
    """Determine retry action for a checkpoint using tiered retry system.

    Returns one of:
      "debugger"  — still have retries in current tier → fix and retry
      "escalate"  — current tier exhausted, escalate to next strategy
      END         — all tiers and absolute cap exhausted

    Args:
        state: Current pipeline state.
        checkpoint: Checkpoint name (e.g. "lint", "sim", "synth", "coder").

    Returns:
        Action string: "debugger", "escalate", or END.
    """
    retry_count = state.get("retry_count", {})
    total_retries = state.get("total_retries", {})
    tier_map = state.get("retry_tier", {})

    current_tier = tier_map.get(checkpoint, "simple_retry")
    tier_retries = retry_count.get(checkpoint, 0)
    abs_retries = total_retries.get(checkpoint, 0)
    tier_limit = RETRY_TIERS.get(current_tier, MAX_RETRIES)

    # Absolute cap — no more retries under any circumstance
    if abs_retries >= MAX_TOTAL_RETRIES:
        logger.warning(
            "Retry budget exhausted for %s: %d/%d total attempts",
            checkpoint, abs_retries, MAX_TOTAL_RETRIES,
        )
        return END

    # Still have retries in current tier
    if tier_retries < tier_limit:
        return "debugger"

    # Current tier exhausted — try to escalate to next tier
    try:
        tier_idx = TIER_ESCALATION_ORDER.index(current_tier)
    except ValueError:
        tier_idx = 0

    if tier_idx + 1 < len(TIER_ESCALATION_ORDER):
        next_tier = TIER_ESCALATION_ORDER[tier_idx + 1]
        logger.info(
            "Tier '%s' exhausted for %s, escalating to '%s'",
            current_tier, checkpoint, next_tier,
        )
        return "escalate"

    # Already at the highest tier and it's exhausted
    return END


def _escalate_tier(state: VeriFlowState, checkpoint: str) -> dict[str, Any]:
    """Advance the retry tier for a checkpoint and reset per-tier counter.

    Returns partial state updates.
    """
    tier_map = dict(state.get("retry_tier", {}))
    retry_count = dict(state.get("retry_count", {}))
    total_retries = dict(state.get("total_retries", {}))
    escalation_history = list(state.get("escalation_history", []))

    current_tier = tier_map.get(checkpoint, "simple_retry")
    try:
        tier_idx = TIER_ESCALATION_ORDER.index(current_tier)
        next_tier = TIER_ESCALATION_ORDER[tier_idx + 1]
    except (ValueError, IndexError):
        next_tier = current_tier  # stay at highest tier

    tier_map[checkpoint] = next_tier
    retry_count[checkpoint] = 0  # reset per-tier counter for new tier

    # Record escalation
    escalation_history.append({
        "stage": checkpoint,
        "from_tier": current_tier,
        "to_tier": next_tier,
        "timestamp": time.time(),
    })
    # Cap history at 20 entries
    if len(escalation_history) > 20:
        escalation_history = escalation_history[-20:]

    logger.info("Escalated %s: %s → %s", checkpoint, current_tier, next_tier)

    return {
        "retry_tier": tier_map,
        "retry_count": retry_count,
        "escalation_history": escalation_history,
    }


def _route_skill_d(state: VeriFlowState) -> str:
    """Route after SkillD quality gate.

    Pass → tool_check → lint. Fail → supervisor for intelligent routing.
    """
    skill_d_output = state.get("skill_d_output")
    if skill_d_output and skill_d_output.success:
        # Check if EDA stages should be skipped due to missing tools
        skip_stages = state.get("eda_skip_stages", [])
        if "lint" in skip_stages and "sim" in skip_stages and "synth" in skip_stages:
            logger.warning("No EDA tools available, pipeline completing with LLM quality checks only")
            return END
        # Route through tool_check which handles skipping logic
        return "tool_check"

    # Check supervisor call budget
    if state.get("supervisor_call_count", 0) >= MAX_SUPERVISOR_CALLS:
        logger.warning("Supervisor call cap reached at skill_d, ending pipeline")
        return END

    within_budget, msg, _ = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at skill_d: %s", msg)
        return END

    logger.info("SkillD quality gate failed, routing to supervisor for analysis")
    return "supervisor"


def _route_architect(state: VeriFlowState) -> str:
    """Route after architect stage.

    Pass → continue to microarch
    Fail → architect_retry (self-repair) or END if retries exhausted
    """
    architect_output = state.get("architect_output")
    if architect_output and architect_output.success:
        return "microarch"

    # Check retry budget for architect
    retry_count = state.get("retry_count", {})
    architect_retries = retry_count.get("architect", 0)
    total_retries = state.get("total_retries", {})
    abs_retries = total_retries.get("architect", 0)

    if abs_retries < MAX_TOTAL_RETRIES and architect_retries < MAX_RETRIES:
        logger.info(
            "Architect failed (attempt %d/%d, total %d/%d), routing to architect_retry",
            architect_retries, MAX_RETRIES, abs_retries, MAX_TOTAL_RETRIES,
        )
        return "architect_retry"

    logger.error(
        "Architect retry budget exhausted (%d total attempts), cannot proceed without spec.json",
        abs_retries,
    )
    return END


def _route_coder(state: VeriFlowState) -> str:
    """Route after coder stage.

    Pass → skill_d quality gate. Fail → supervisor for intelligent routing.
    """
    coder_output = state.get("coder_output")
    if coder_output and coder_output.success:
        return "skill_d"

    # Check supervisor call budget
    if state.get("supervisor_call_count", 0) >= MAX_SUPERVISOR_CALLS:
        logger.warning("Supervisor call cap reached at coder, ending pipeline")
        return END

    within_budget, msg, _ = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at coder: %s", msg)
        return END

    logger.info("Coder failed, routing to supervisor for analysis")
    return "supervisor"


def _route_lint(state: VeriFlowState) -> str:
    """Route after lint check.

    Pass → sim. Fail → supervisor for intelligent routing.
    """
    lint_output = state.get("lint_output")
    if lint_output and lint_output.success:
        return "sim"

    # Check if sim should be skipped
    if "sim" in state.get("eda_skip_stages", []):
        if "synth" not in state.get("eda_skip_stages", []):
            return "synth"
        logger.warning("Lint failed and no further EDA tools available")

    # Check supervisor call budget
    if state.get("supervisor_call_count", 0) >= MAX_SUPERVISOR_CALLS:
        logger.warning("Supervisor call cap reached at lint, ending pipeline")
        return END

    within_budget, msg, _ = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at lint: %s", msg)
        return END

    logger.info("Lint failed, routing to supervisor for analysis")
    return "supervisor"


def _route_sim(state: VeriFlowState) -> str:
    """Route after simulation check.

    Pass → synth. Fail → supervisor for intelligent routing.
    """
    sim_output = state.get("sim_output")
    if sim_output and sim_output.success:
        # Check if synth should be skipped
        if "synth" in state.get("eda_skip_stages", []):
            logger.info("Simulation passed! Synthesis skipped (yosys not available)")
            return END
        return "synth"

    # Check supervisor call budget
    if state.get("supervisor_call_count", 0) >= MAX_SUPERVISOR_CALLS:
        logger.warning("Supervisor call cap reached at sim, ending pipeline")
        return END

    within_budget, msg, _ = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at sim: %s", msg)
        return END

    logger.info("Sim failed, routing to supervisor for analysis")
    return "supervisor"


def _route_synth(state: VeriFlowState) -> str:
    """Route after synthesis check.

    Pass → END. Fail → supervisor for intelligent routing.
    """
    synth_output = state.get("synth_output")
    if synth_output and synth_output.success:
        logger.info("Synthesis passed! Pipeline complete.")
        return END

    # Check supervisor call budget
    if state.get("supervisor_call_count", 0) >= MAX_SUPERVISOR_CALLS:
        logger.warning("Supervisor call cap reached at synth, ending pipeline")
        return END

    within_budget, msg, _ = check_token_budget(state)
    if not within_budget:
        logger.error("Token budget exceeded at synth: %s", msg)
        return END

    logger.info("Synth failed, routing to supervisor for analysis")
    return "supervisor"


def _route_debugger(state: VeriFlowState) -> str:
    """Route debugger output back to supervisor for re-evaluation.

    After the debugger fixes RTL, the supervisor re-evaluates whether
    the fix is sufficient or further action is needed.
    """
    # Check supervisor call budget
    if state.get("supervisor_call_count", 0) >= MAX_SUPERVISOR_CALLS:
        # Fallback: route to the mechanical target (the check that failed)
        target = state.get("target_rollback_stage", "lint")
        logger.info(
            "Supervisor cap reached, debugger routing directly to: %s", target
        )
        return target

    logger.info("Debugger complete, returning to supervisor for re-evaluation")
    return "supervisor"


def _route_tool_check(state: VeriFlowState) -> str:
    """Route after tool_check: skip to first available EDA stage."""
    skip_stages = state.get("eda_skip_stages", [])
    if "lint" not in skip_stages:
        return "lint"
    if "sim" not in skip_stages:
        return "sim"
    if "synth" not in skip_stages:
        return "synth"
    # No EDA tools available at all
    logger.warning("No EDA tools available, pipeline completing with LLM quality checks only")
    return END


# ── Supervisor node (LLM-driven intelligent routing) ──────────────────


def _identify_failing_stage(state: VeriFlowState) -> str:
    """Identify which stage just failed by checking stage outputs.

    Returns the name of the most recent stage that has success=False.
    Falls back to feedback_source or current_stage.
    """
    # Check feedback_source first (set by check nodes)
    feedback = state.get("feedback_source", "")
    if feedback:
        return feedback

    # Check recent stage outputs for failure
    for stage_name in ("synth", "sim", "lint", "skill_d", "coder", "timing", "microarch", "architect"):
        output = state.get(f"{stage_name}_output")
        if output and hasattr(output, "success") and not output.success:
            return stage_name

    return state.get("current_stage", "unknown")


def _gather_error_log(state: VeriFlowState, failing_stage: str) -> str:
    """Collect error log from the failing stage's output.

    When re-evaluating after a debugger fix, also includes debugger context
    so the supervisor can see what was attempted.
    """
    parts: list[str] = []

    # Original error from the failing stage
    stage_output = state.get(f"{failing_stage}_output")
    if stage_output:
        errors = getattr(stage_output, "errors", [])
        if errors:
            parts.append("## Original Error\n" + "\n".join(str(e) for e in errors))
        else:
            raw = getattr(stage_output, "raw_output", "")
            if raw:
                parts.append("## Original Error\n" + raw[:3000])

    # If debugger was invoked since the last failure, include its context
    debugger_output = state.get("debugger_output")
    supervisor_history = state.get("supervisor_history", [])
    if debugger_output and supervisor_history:
        # Debugger was invoked — include what it did
        dbg_errors = getattr(debugger_output, "errors", [])
        dbg_artifacts = getattr(debugger_output, "artifacts", [])
        dbg_success = getattr(debugger_output, "success", False)
        dbg_raw = getattr(debugger_output, "raw_output", "")

        parts.append(
            f"\n## Debugger Result (after fix attempt)\n"
            f"- Status: {'SUCCESS' if dbg_success else 'FAILED'}\n"
            f"- Files modified: {', '.join(dbg_artifacts) if dbg_artifacts else 'none'}\n"
        )
        if dbg_errors:
            parts.append("- Debugger errors: " + "; ".join(str(e)[:200] for e in dbg_errors[:3]))
        if dbg_raw:
            # Include a preview of what the debugger changed
            parts.append("- Fix preview:\n" + dbg_raw[:1500])

    return "\n\n".join(parts) if parts else ""


def _gather_spec_summary(state: VeriFlowState) -> str:
    """Read spec.json summary for supervisor context."""
    project_dir = Path(state.get("project_dir", "."))
    spec_path = project_dir / "workspace" / "docs" / "spec.json"
    if spec_path.exists():
        try:
            return spec_path.read_text(encoding="utf-8")[:2000]
        except Exception:
            pass
    return "(spec.json not available)"


def _probe_environment(state: VeriFlowState) -> str:
    """Probe environment: EDA tool availability, file system state.

    Returns a structured text summary for the supervisor to diagnose
    environment-level issues (missing tools, missing files, etc.).
    """
    project_dir = Path(state.get("project_dir", "."))

    # 1. EDA tool availability
    eda_available = state.get("eda_tools_available", {})
    eda_lines = []
    for tool in ("iverilog", "vvp", "yosys"):
        available = eda_available.get(tool, False)
        status = "INSTALLED" if available else "NOT FOUND"
        eda_lines.append(f"  {tool}: {status}")

    # 2. Workspace file system state
    ws = project_dir / "workspace"
    file_lines = []
    for subdir_name, pattern in [
        ("docs", "*.json"), ("docs", "*.md"), ("docs", "*.yaml"),
        ("rtl", "*.v"), ("tb", "tb_*.v"), ("logs", "*.txt"),
    ]:
        subdir = ws / subdir_name
        if subdir.exists():
            files = list(subdir.glob(pattern))
            names = [f.name for f in files[:10]]  # Cap at 10
            if names:
                file_lines.append(f"  {subdir_name}/{pattern}: {', '.join(names)}")
            else:
                file_lines.append(f"  {subdir_name}/{pattern}: (empty)")
        else:
            file_lines.append(f"  {subdir_name}/{pattern}: (directory does not exist)")

    # 3. Pipeline progress
    completed = state.get("stages_completed", [])
    failed = state.get("stages_failed", [])
    progress = f"  completed: {', '.join(completed) or '(none)'}\n  failed: {', '.join(failed) or '(none)'}"

    # 4. Token budget
    budget = state.get("token_budget", 0)
    usage = state.get("token_usage", 0)
    budget_str = f"  {usage}/{budget} tokens used" if budget > 0 else "  (unlimited)"

    return (
        f"## EDA Tools\n" + "\n".join(eda_lines) + "\n\n"
        f"## Workspace Files\n" + "\n".join(file_lines) + "\n\n"
        f"## Pipeline Progress\n{progress}\n\n"
        f"## Token Budget\n{budget_str}"
    )


def _validate_rtl_files(state: VeriFlowState) -> str:
    """Independent RTL validation — supervisor's 'eyes'.

    Reads actual RTL files and checks key structural properties.
    This gives the supervisor ground-truth to verify upstream error
    messages (e.g., skill_d claiming 'code incomplete' when it's not).

    Returns a structured text summary for the supervisor prompt.
    """
    project_dir = Path(state.get("project_dir", "."))
    rtl_dir = project_dir / "workspace" / "rtl"

    if not rtl_dir.exists():
        return "## RTL Validation\n  No workspace/rtl/ directory found"

    rtl_files = list(rtl_dir.glob("*.v"))
    if not rtl_files:
        return "## RTL Validation\n  No .v files in workspace/rtl/"

    lines = [f"## RTL Validation ({len(rtl_files)} files)"]

    for f in sorted(rtl_files):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            n_lines = len(content.splitlines())
            has_module = "module " in content
            has_endmodule = "endmodule" in content
            # Count module/endmodule pairs
            module_count = content.count("endmodule")
            # Check for empty file
            is_empty = len(content.strip()) < 10

            status = "OK" if (has_module and has_endmodule and not is_empty) else "ISSUE"
            detail = f"{n_lines} lines, {module_count} module(s)"

            if is_empty:
                detail += " [EMPTY FILE]"
            elif has_module and not has_endmodule:
                detail += " [MISSING ENDMODULE]"
            elif not has_module:
                detail += " [NO MODULE DECLARATION]"

            lines.append(f"  {f.name}: {status} — {detail}")
        except Exception as e:
            lines.append(f"  {f.name}: ERROR — {e}")

    # Overall verdict
    all_ok = all(
        "OK" in line for line in lines[1:] if "ERROR" not in line
    )
    if all_ok:
        lines.append("  Overall: All RTL files structurally valid (module + endmodule present)")
    else:
        issues = [line for line in lines[1:] if "ISSUE" in line or "ERROR" in line]
        lines.append(f"  Overall: {len(issues)} file(s) with structural issues")

    return "\n".join(lines)



def _load_shared_context(state: VeriFlowState) -> dict[str, Any]:
    """Load shared context saved by Orchestrator during architect clarification.

    This enables Supervisor to understand:
    - Original user requirements
    - Clarification Q&A history
    - Key design decisions made
    - Extracted technical parameters

    Returns empty dict if no shared context file exists.
    """
    project_dir = Path(state.get("project_dir", "."))
    context_path = project_dir / ".veriflow" / "shared_context.json"

    if not context_path.exists():
        return {}

    try:
        content = context_path.read_text(encoding="utf-8")
        return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Failed to load shared context: %s", e)
        return {}


def _gather_full_project_context(
    state: VeriFlowState, failing_stage: str
) -> str:
    """Walk the entire project directory and read ALL files.

    Supervisor has the highest authority — it sees everything.
    Does NOT filter by failing stage. The supervisor decides
    what's relevant, not us.

    Returns a structured text with every file's path and content.
    """
    project_dir = Path(state.get("project_dir", "."))

    # Directories and file patterns to skip (internal/temporary)
    skip_dirs = {".veriflow", "__pycache__", ".git", "node_modules", ".mypy_cache"}
    skip_suffixes = {".pyc", ".pyo", ".git", ".bin", ".o", ".vcd", ".fst"}

    all_files: list[Path] = []
    for f in project_dir.rglob("*"):
        if not f.is_file():
            continue
        # Skip internal directories
        if any(part in skip_dirs for part in f.relative_to(project_dir).parts):
            continue
        # Skip binary/temporary files
        if f.suffix in skip_suffixes:
            continue
        all_files.append(f)

    all_files.sort()

    if not all_files:
        return "(No files found in project directory)"

    sections: list[str] = []
    total_chars = 0
    max_total = 15000  # Prompt budget for file content

    for f in all_files:
        rel_path = f.relative_to(project_dir)
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            size = len(content)
        except Exception:
            sections.append(f"## {rel_path} (binary/unreadable)")
            continue

        # Determine per-file limit based on type
        if f.suffix == ".v":
            per_file_limit = 3000  # RTL code — important, show more
        elif f.suffix in (".log", ".txt"):
            per_file_limit = 2000
        elif f.suffix in (".json", ".yaml", ".yml", ".md"):
            per_file_limit = 2000
        else:
            per_file_limit = 500  # Unknown types — show preview only

        if total_chars + 200 > max_total:
            sections.append(f"\n... ({len(all_files) - len(sections)} more files omitted due to context limit)")
            break

        n_lines = len(content.splitlines())
        if size > per_file_limit:
            if f.suffix == ".v" and n_lines > 60:
                # RTL: show head + tail so supervisor sees module decl + endmodule
                lines = content.splitlines()
                head = 30
                tail = 30
                shown = "\n".join(lines[:head])
                shown += f"\n... ({n_lines - head - tail} lines omitted) ...\n"
                shown += "\n".join(lines[-tail:])
            else:
                shown = content[:per_file_limit] + f"\n... (truncated, {size} total chars)"
        else:
            shown = content

        total_chars += len(shown)

        # Choose code fence language
        lang_map = {
            ".v": "verilog", ".sv": "systemverilog",
            ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".md": "markdown", ".py": "python",
            ".log": "", ".txt": "",
        }
        lang = lang_map.get(f.suffix, "")
        sections.append(f"## {rel_path} ({n_lines} lines, {size} chars)\n```{lang}\n{shown}\n```")

    return "\n\n".join(sections)


def node_supervisor(state: VeriFlowState) -> dict[str, Any]:
    """Supervisor: LLM-based intelligent diagnosis and routing.

    Not just a router — the supervisor diagnoses failure root causes:
    - Environment: EDA tools missing → suggest install or degrade mode
    - Files: Missing artifacts → trace to source stage
    - LLM: API issues → retry or switch backend
    - EDA: Code errors → route to debugger with guidance
    - Quality: Low score → targeted improvement

    Falls back to mechanical categorization if LLM fails.
    """
    # Hard cap check
    call_count = state.get("supervisor_call_count", 0)
    if call_count >= MAX_SUPERVISOR_CALLS:
        logger.warning(
            "Supervisor call cap reached (%d/%d), aborting pipeline",
            call_count, MAX_SUPERVISOR_CALLS,
        )
        return {
            "supervisor_call_count": call_count,
            "supervisor_decision": {
                "action": "abort",
                "target_stage": "",
                "hint": "",
                "root_cause": f"Supervisor call cap reached ({call_count}/{MAX_SUPERVISOR_CALLS})",
                "severity": "high",
                "modules": [],
            },
        }

    # Identify the failing stage
    failing_stage = _identify_failing_stage(state)

    # Gather context — supervisor sees EVERYTHING
    error_log = _gather_error_log(state, failing_stage)
    rtl_validation = _validate_rtl_files(state)
    spec_summary = _gather_spec_summary(state)
    full_project_context = _gather_full_project_context(state, failing_stage)
    error_history_map = state.get("error_history", {})
    error_history = list(error_history_map.get(failing_stage, []))
    supervisor_history = list(state.get("supervisor_history", []))
    env_probe = _probe_environment(state)

    # Determine recovery context
    recovery_context = "re-evaluation" if supervisor_history else "initial failure"

    # ── NEW: Load shared context from Orchestrator ────────────────────────
    shared_context = _load_shared_context(state)

    # ── NEW: Evaluate current strategy effectiveness ──────────────────────
    strategy_effectiveness = _evaluate_supervisor_strategy_effectiveness(state)

    supervisor_ctx = {
        "project_dir": state.get("project_dir", "."),
        "failing_stage": failing_stage,
        "error_log": error_log[:5000],
        "error_history": error_history,
        "spec_summary": spec_summary,
        "full_project_context": full_project_context,
        "supervisor_history_json": json.dumps(supervisor_history[-5:]),
        "recovery_context": recovery_context,
        "environment_probe": env_probe,
        "rtl_validation": rtl_validation,
        "llm_api_key": state.get("llm_api_key", ""),
        "llm_base_url": state.get("llm_base_url", ""),
        "llm_model": state.get("llm_model", ""),
        "budget_mode": state.get("budget_mode", "normal"),
        # Cross-stage context from Orchestrator
        "user_requirement_summary": shared_context.get("user_requirement_summary", ""),
        "clarification_history": json.dumps(shared_context.get("clarification_history", [])),
        "key_design_decisions": "\n".join(shared_context.get("key_design_decisions", [])),
        "extracted_parameters": json.dumps(shared_context.get("extracted_parameters", {})),
        # Strategy effectiveness for adaptive decision making
        "strategy_effectiveness": json.dumps(strategy_effectiveness),
        # If debugger already tried and failed, pass that note so supervisor avoids routing to debugger again
        "debugger_failure_note": state.get("debugger_failure_note", ""),
    }

    # Run SupervisorAgent
    updates = _run_stage(state, SupervisorAgent, **supervisor_ctx)

    # Extract decision from supervisor_output
    supervisor_output: StageOutput | None = updates.get("supervisor_output")
    decision: dict[str, Any] = {}
    if supervisor_output and supervisor_output.metrics:
        decision = {
            "action": supervisor_output.metrics.get("action", "retry_stage"),
            "target_stage": supervisor_output.metrics.get("target_stage", "debugger"),
            "modules": supervisor_output.metrics.get("modules", []),
            "hint": supervisor_output.metrics.get("hint", ""),
            "root_cause": supervisor_output.metrics.get("root_cause", ""),
            "severity": supervisor_output.metrics.get("severity", "medium"),
        }
    else:
        # Supervisor LLM unavailable — do not guess mechanically.
        # Signal abort so the pipeline pauses and notifies the user.
        _sv_err = ""
        if supervisor_output and supervisor_output.errors:
            _sv_err = "; ".join(supervisor_output.errors[:2])
        decision = {
            "action": "abort",
            "target_stage": "",
            "modules": [],
            "hint": "",
            "root_cause": (
                f"Supervisor LLM 不可用（返回空输出）。{_sv_err} "
                "请检查 API Key / 网络连接，然后重试。"
            ),
            "severity": "high",
        }

    # Update state
    updates["supervisor_call_count"] = call_count + 1
    updates["supervisor_decision"] = decision
    updates["supervisor_hint"] = decision.get("hint", "")
    # Only set target_rollback_stage for real pipeline stages.
    # "debugger" is an intermediary fix step, not a stage to roll back to.
    _target = decision.get("target_stage", "")
    if _target and _target != "debugger":
        updates["target_rollback_stage"] = _target
    # No fallback default — Supervisor LLM is responsible for all routing decisions
    updates["feedback_source"] = failing_stage

    # Inject hint into strategy_override for the target stage
    hint = decision.get("hint", "")
    target = decision.get("target_stage", "")
    if hint and target:
        strategy_override = dict(state.get("strategy_override", {}))
        strategy_override[target] = hint
        updates["strategy_override"] = strategy_override

    # Record in history
    history = list(state.get("supervisor_history", []))
    history.append({
        **decision,
        "failing_stage": failing_stage,
        "call_number": call_count + 1,
        "timestamp": time.time(),
    })
    updates["supervisor_history"] = history[-20:]  # Cap at 20

    logger.info(
        "Supervisor #%d: action=%s, target=%s, root_cause=%s",
        call_count + 1,
        decision.get("action", "?"),
        decision.get("target_stage", "?"),
        decision.get("root_cause", "")[:80],
    )

    return updates


def _track_supervisor_decision_outcome(
    state: VeriFlowState,
    decision: dict[str, Any],
    execution_result: dict[str, Any],
) -> None:
    """Track the actual outcome of a Supervisor decision for learning.

    This function records whether the Supervisor's decision was effective,
    enabling future improvements to the routing strategy.
    """
    # Get or initialize outcome tracker
    tracker = state.get("supervisor_outcome_tracker", {})
    current_decision_id = decision.get("timestamp", str(time.time()))

    if tracker.get("current_decision_id") != current_decision_id:
        # New decision - initialize tracker
        tracker = {
            "current_decision_id": current_decision_id,
            "decision": decision,
            "failing_stage": state.get("feedback_source", "unknown"),
            "stages_executed": [],
            "errors_encountered": [],
            "resolved": False,
            "start_time": time.time(),
            "token_usage_at_start": state.get("token_usage", 0),
        }

    # Update with execution result
    if execution_result.get("stage"):
        tracker["stages_executed"].append(execution_result["stage"])
    if execution_result.get("error"):
        tracker["errors_encountered"].append(execution_result["error"])
    if execution_result.get("success"):
        tracker["resolved"] = True
        tracker["resolution_time"] = time.time() - tracker["start_time"]
        tracker["tokens_consumed"] = (
            state.get("token_usage", 0) - tracker["token_usage_at_start"]
        )

    # Save back to state
    state["supervisor_outcome_tracker"] = tracker

    # Log for analysis
    if tracker["resolved"]:
        logger.info(
            "Supervisor decision #%s outcome: RESOLVED in %.1fs, %d tokens, "
            "path: %s",
            state.get("supervisor_call_count", 0),
            tracker["resolution_time"],
            tracker["tokens_consumed"],
            " -> ".join(tracker["stages_executed"]),
        )


def _evaluate_supervisor_strategy_effectiveness(
    state: VeriFlowState,
) -> dict[str, Any]:
    """Evaluate whether the current strategy is working.

    Returns effectiveness metrics and suggests strategy adjustment if needed.
    """
    tracker = state.get("supervisor_outcome_tracker", {})
    history = state.get("supervisor_history", [])

    if not tracker or not history:
        return {"effectiveness": "unknown", "suggestion": None}

    stages_executed = tracker.get("stages_executed", [])
    errors = tracker.get("errors_encountered", [])

    # Heuristics for effectiveness
    effectiveness = "unknown"
    suggestion = None

    # Pattern 1: Ping-pong between stages (strategy not converging)
    if len(stages_executed) >= 4:
        unique_stages = set(stages_executed[-4:])
        if len(unique_stages) >= 3:
            effectiveness = "poor"
            suggestion = (
                "Strategy oscillating between stages. Consider escalating "
                "to earlier stage with simpler approach, or aborting if "
                "fundamental design issue."
            )

    # Pattern 2: Multiple consecutive failures
    elif len(errors) >= 3:
        effectiveness = "poor"
        suggestion = (
            "Multiple consecutive errors. Current approach not working. "
            "Recommend escalating to architect stage for fundamental redesign."
        )

    # Pattern 3: Making progress
    elif len(stages_executed) >= 2 and len(errors) == 0:
        effectiveness = "good"

    # Pattern 4: Same error recurring
    elif len(errors) >= 2 and errors[-1] == errors[-2]:
        effectiveness = "poor"
        suggestion = (
            "Same error recurring despite fix attempts. "
            "Consider escalating to earlier stage with different strategy."
        )

    return {
        "effectiveness": effectiveness,
        "suggestion": suggestion,
        "stages_count": len(stages_executed),
        "errors_count": len(errors),
        "time_elapsed": time.time() - tracker.get("start_time", time.time()),
    }


def _route_supervisor(state: VeriFlowState) -> str:
    """Route based on Supervisor's LLM decision.

    Implements the supervisor's routing decision:
    - abort → END
    - continue → next normal stage after the failing stage
    - degrade → skip EDA stages, route to target or END
    - retry_stage / escalate_stage → target_stage

    Enhanced with outcome tracking and strategy effectiveness evaluation.
    """
    decision = state.get("supervisor_decision") or {}
    action = decision.get("action", "abort")
    target = decision.get("target_stage", "debugger")

    # Evaluate current strategy effectiveness before routing
    effectiveness = _evaluate_supervisor_strategy_effectiveness(state)
    if effectiveness["effectiveness"] == "poor" and effectiveness["suggestion"]:
        logger.warning(
            "Supervisor strategy effectiveness poor: %s", effectiveness["suggestion"]
        )
        # If strategy is failing and we're about to retry, escalate instead
        if action == "retry_stage" and len(state.get("supervisor_history", [])) >= 2:
            logger.info("Auto-escalating due to ineffective retry strategy")
            action = "escalate_stage"
            # Escalate to earlier stage
            escalation_map = {
                "debugger": "coder",
                "coder": "timing",
                "timing": "microarch",
                "microarch": "architect",
            }
            target = escalation_map.get(target, "architect")

    # Record this routing decision
    _track_supervisor_decision_outcome(
        state,
        decision,
        {"stage": target, "action": action, "effectiveness_eval": effectiveness},
    )

    if action == "abort":
        logger.warning("Supervisor decided to abort: %s",
                        decision.get("root_cause", ""))
        # Record final outcome before ending
        _track_supervisor_decision_outcome(
            state, decision, {"success": False, "aborted": True}
        )
        return END

    if action == "continue":
        # Determine next stage in normal flow
        failing = state.get("feedback_source", "lint")
        return _next_normal_stage(failing)

    if action == "degrade":
        # Use intelligent degrade strategy
        return _intelligent_degrade_route(state, target)

    # retry_stage / escalate_stage → route to the target
    logger.info("Supervisor routing to: %s (action=%s)", target, action)
    return target


def _intelligent_degrade_route(state: VeriFlowState, target: str) -> str:
    """Intelligent degrade routing with LLM-enhanced validation.

    Instead of simply skipping stages, this function:
    1. Determines which stages can be effectively replaced by LLM validation
    2. Injects enhanced validation instructions to remaining stages
    3. Provides clear user notifications about degraded mode
    """
    skip_stages = state.get("eda_skip_stages", [])

    # Count available tools
    eda_available = state.get("eda_tools_available", {})
    available_count = sum(1 for v in eda_available.values() if v)

    logger.info(
        "Intelligent degrade: available_tools=%d, skip_stages=%s",
        available_count, skip_stages,
    )

    # Strategy: If only synth is missing, skip just synth
    if "yosys" not in skip_stages and not eda_available.get("yosys"):
        if target and target not in skip_stages:
            logger.info("Degrade: skipping synth only, routing to %s", target)
            return target
        if "lint" not in skip_stages:
            return "lint"
        if "sim" not in skip_stages:
            return "sim"
        return END

    # Strategy: If iverilog missing but yosys available, use yosys for basic lint
    if not eda_available.get("iverilog") and eda_available.get("yosys"):
        logger.info("Degrade: using yosys for basic lint (iverilog missing)")
        # Inject hint to use yosys read_verilog for basic syntax check
        strategy_override = dict(state.get("strategy_override", {}))
        strategy_override["synth"] = (
            "iverilog not available. Use 'read_verilog' in yosys to perform "
            "basic syntax checking before synthesis. Report any parse errors."
        )
        state["strategy_override"] = strategy_override
        if "synth" not in skip_stages:
            return "synth"
        return END

    # Strategy: All EDA tools missing - complete with LLM validation only
    if available_count == 0:
        logger.warning("Degrade: No EDA tools available, completing with LLM validation")
        # Mark pipeline as complete with caveats
        caveats = state.get("pipeline_complete_with_caveats", [])
        caveats.append(
            "No EDA tools available. Design completed with LLM quality checks only. "
            "Install iverilog/yosys for full verification."
        )
        state["pipeline_complete_with_caveats"] = caveats
        return END

    # Default: route to target or first available stage
    if target and target not in skip_stages:
        return target
    for stage in ("lint", "sim", "synth"):
        if stage not in skip_stages:
            return stage
    return END


def _next_normal_stage(current_stage: str) -> str:
    """Return the next stage in normal pipeline flow after a check stage."""
    flow = ["lint", "sim", "synth"]
    try:
        idx = flow.index(current_stage)
    except ValueError:
        return END
    if idx + 1 < len(flow):
        return flow[idx + 1]
    return END


# ── New self-healing nodes ────────────────────────────────────────────


def node_architect_retry(state: VeriFlowState) -> dict[str, Any]:
    """Architect self-repair node: re-runs architect with error feedback.

    Instead of giving up, injects the architect's own errors as feedback
    so the LLM can correct its output.
    """
    # Increment retry counters
    retry_count = dict(state.get("retry_count", {}))
    total_retries = dict(state.get("total_retries", {}))
    retry_count["architect"] = retry_count.get("architect", 0) + 1
    total_retries["architect"] = total_retries.get("architect", 0) + 1

    # Gather error feedback from previous attempt
    architect_output = state.get("architect_output")
    error_feedback = ""
    if architect_output:
        error_feedback = "\n".join(architect_output.errors)

    # Also check error history
    error_history = state.get("error_history", {})
    arch_errors = error_history.get("architect", [])
    if arch_errors:
        error_feedback = f"{error_feedback}\n\nPrevious attempts:\n" + "\n---\n".join(arch_errors[-3:])

    # Record error history
    error_history = dict(error_history)
    arch_errors = list(error_history.get("architect", []))
    arch_errors.append(error_feedback[:2000])
    error_history["architect"] = arch_errors

    logger.info(
        "Architect self-repair (attempt %d, total %d)",
        retry_count["architect"], total_retries["architect"],
    )

    # Re-run architect with error feedback
    return {
        **_run_stage(state, ArchitectAgent, architect_retry_feedback=error_feedback[:3000]),
        "retry_count": retry_count,
        "total_retries": total_retries,
        "error_history": error_history,
    }


def node_microarch_retry(state: VeriFlowState) -> dict[str, Any]:
    """Microarch self-repair node: re-runs microarch with quality feedback."""
    retry_count = dict(state.get("retry_count", {}))
    total_retries = dict(state.get("total_retries", {}))
    retry_count["microarch"] = retry_count.get("microarch", 0) + 1
    total_retries["microarch"] = total_retries.get("microarch", 0) + 1

    microarch_output = state.get("microarch_output")
    error_feedback = ""
    if microarch_output:
        error_feedback = "\n".join(microarch_output.errors or [])
        if not error_feedback and microarch_output.warnings:
            error_feedback = "Output quality insufficient:\n" + "\n".join(microarch_output.warnings)

    logger.info(
        "Microarch self-repair (attempt %d, total %d)",
        retry_count["microarch"], total_retries["microarch"],
    )

    return {
        **_run_stage(state, MicroArchAgent, microarch_retry_feedback=error_feedback[:2000]),
        "retry_count": retry_count,
        "total_retries": total_retries,
    }


def node_timing_retry(state: VeriFlowState) -> dict[str, Any]:
    """Timing self-repair node: re-runs timing with quality feedback."""
    retry_count = dict(state.get("retry_count", {}))
    total_retries = dict(state.get("total_retries", {}))
    retry_count["timing"] = retry_count.get("timing", 0) + 1
    total_retries["timing"] = total_retries.get("timing", 0) + 1

    timing_output = state.get("timing_output")
    error_feedback = ""
    if timing_output:
        error_feedback = "\n".join(timing_output.errors or [])
        if not error_feedback and timing_output.warnings:
            error_feedback = "Output quality insufficient:\n" + "\n".join(timing_output.warnings)

    logger.info(
        "Timing self-repair (attempt %d, total %d)",
        retry_count["timing"], total_retries["timing"],
    )

    return {
        **_run_stage(state, TimingAgent, timing_retry_feedback=error_feedback[:2000]),
        "retry_count": retry_count,
        "total_retries": total_retries,
    }


def _route_microarch(state: VeriFlowState) -> str:
    """Route after microarch stage.

    Pass (with quality) → timing
    Fail or poor quality → microarch_retry
    Retries exhausted → timing anyway (better than stopping)
    """
    microarch_output = state.get("microarch_output")
    if microarch_output and microarch_output.success:
        # Quality check: output must be substantial
        doc_size = len(microarch_output.raw_output) if microarch_output.raw_output else 0
        if doc_size > 100:
            return "timing"
        # Output too small — try retry
        logger.warning("Microarch output too small (%d bytes), retrying", doc_size)

    retry_count = state.get("retry_count", {})
    microarch_retries = retry_count.get("microarch", 0)
    if microarch_retries < MAX_RETRIES:
        return "microarch_retry"

    # Proceed anyway with a warning — better than stopping
    logger.warning("microarch quality low after %d retries, proceeding to timing", microarch_retries)
    return "timing"


def _route_timing(state: VeriFlowState) -> str:
    """Route after timing stage.

    Pass (with quality) → coder
    Fail or poor quality → timing_retry
    Retries exhausted → coder anyway (better than stopping)
    """
    timing_output = state.get("timing_output")
    if timing_output and timing_output.success:
        # Check that output is substantial
        doc_size = len(timing_output.raw_output) if timing_output.raw_output else 0
        if doc_size > 50:
            return "coder"
        logger.warning("Timing output too small (%d bytes), retrying", doc_size)

    retry_count = state.get("retry_count", {})
    timing_retries = retry_count.get("timing", 0)
    if timing_retries < MAX_RETRIES:
        return "timing_retry"

    logger.warning("timing quality low after %d retries, proceeding to coder", timing_retries)
    return "coder"


def node_tool_check(state: VeriFlowState) -> dict[str, Any]:
    """Probe EDA tool availability and populate eda_skip_stages.

    Runs before lint stage. If tools are missing, marks stages for skipping
    so the pipeline can continue with degraded (LLM-only) validation.
    """
    from veriflow_agent.tools.eda_utils import find_eda_tool

    available = {}
    skip_stages: list[str] = []
    caveats: list[str] = []

    for tool_name in ("iverilog", "vvp", "yosys"):
        path = find_eda_tool(tool_name)
        available[tool_name] = path is not None
        if path is None:
            logger.warning("EDA tool '%s' not found in PATH", tool_name)

    # Determine which stages to skip based on missing tools
    if not available.get("iverilog", False):
        skip_stages.extend(["lint", "sim"])
        caveats.append("iverilog not found: lint and simulation stages skipped")

    if not available.get("vvp", False):
        if "sim" not in skip_stages:
            skip_stages.append("sim")
        if "vvp not found" not in " ".join(caveats):
            caveats.append("vvp not found: simulation stage skipped")

    if not available.get("yosys", False):
        skip_stages.append("synth")
        caveats.append("yosys not found: synthesis stage skipped")

    for caveat in caveats:
        logger.warning(caveat)

    return {
        "eda_tools_available": available,
        "eda_skip_stages": skip_stages,
        "pipeline_complete_with_caveats": caveats,
    }


def node_escalator(state: VeriFlowState) -> dict[str, Any]:
    """Escalation node: when tier retries exhausted, try a new strategy.

    Uses LLM analysis to choose a recovery strategy from accumulated errors.
    Falls back to mechanical rollback escalation if LLM analysis fails.
    """
    feedback_source = state.get("feedback_source", "lint")

    # Escalate the retry tier for the failing checkpoint
    tier_updates = _escalate_tier(state, feedback_source)

    # Gather all accumulated context for LLM analysis
    error_history = state.get("error_history", {})
    all_errors = []
    for checkpoint, errors in error_history.items():
        if errors:
            all_errors.append(f"[{checkpoint}] " + "\n".join(errors[-3:]))

    escalation_history = state.get("escalation_history", [])

    # Determine mechanical fallback: escalate rollback target one stage earlier
    current_target = state.get("target_rollback_stage", "coder")
    rollback_escalation_map = {
        "coder": "microarch",
        "microarch": "architect",
        "timing": "microarch",
        "lint": "coder",
    }
    mechanical_target = rollback_escalation_map.get(current_target, current_target)

    # Try LLM-based strategy analysis
    strategy_override = dict(state.get("strategy_override", {}))
    llm_target = ""

    try:
        from veriflow_agent.agents.base import BaseAgent

        agent = DebuggerAgent()
        analysis_prompt = (
            "You are analyzing accumulated failures in an RTL design pipeline.\n"
            "Based on the error history below, recommend:\n"
            "1. rollback_target: which stage to restart from "
            "(architect/microarch/timing/coder)\n"
            "2. strategy: a one-line strategy instruction for that stage\n\n"
            f"Failing at: {feedback_source}\n"
            f"Current rollback target: {current_target}\n"
            f"Previous escalation attempts: {len(escalation_history)}\n\n"
            f"Error history:\n{chr(10).join(all_errors[-10:])}\n\n"
            "Respond in this exact format:\n"
            "rollback_target: <stage_name>\n"
            "strategy: <one-line strategy>"
        )

        context = {
            "project_dir": state.get("project_dir", "."),
            "llm_api_key": state.get("llm_api_key", ""),
            "llm_base_url": state.get("llm_base_url", ""),
            "llm_model": state.get("llm_model", ""),
            "error_type": feedback_source,
            "error_log": "\n".join(all_errors[-5:])[:3000],
            "feedback_source": feedback_source,
            "error_history": [],
            "timing_model_yaml": "",
        }

        # Use the debugger's LLM analysis capability
        result = agent.execute(context)
        if result.success and result.raw_output:
            import re
            target_match = re.search(
                r"rollback_target:\s*(\w+)", result.raw_output, re.IGNORECASE
            )
            strategy_match = re.search(
                r"strategy:\s*(.+)", result.raw_output, re.IGNORECASE
            )
            if target_match:
                llm_target = target_match.group(1).lower()
                valid_targets = {"architect", "microarch", "timing", "coder", "lint"}
                if llm_target not in valid_targets:
                    llm_target = ""
            if strategy_match:
                target_stage = llm_target or mechanical_target
                strategy_override[target_stage] = strategy_match.group(1).strip()

    except Exception as e:
        logger.warning("Escalator LLM analysis failed: %s, using mechanical fallback", e)

    # Use LLM target if valid, otherwise mechanical
    final_target = llm_target if llm_target else mechanical_target

    logger.info(
        "Escalator: %s → rollback target '%s' → '%s' (tier: %s)",
        feedback_source, current_target, final_target,
        tier_updates.get("retry_tier", {}).get(feedback_source, "unknown"),
    )

    return {
        **tier_updates,
        "target_rollback_stage": final_target,
        "strategy_override": strategy_override,
    }


# ── Graph builder ─────────────────────────────────────────────────────


def create_veriflow_graph(
    *,
    with_checkpointer: bool = True,
) -> StateGraph:
    """Build the VeriFlow LangGraph pipeline.

    Pipeline flow with LLM-driven Supervisor:
      architect → microarch → timing → coder → skill_d → lint → sim → synth → END
                                                            ↓       ↓       ↓
                                                         (fail)   (fail)   (fail)
                                                            ↓       ↓       ↓
                                                         supervisor(LLM analysis)
                                                            │
                                                            ├─ retry_stage(debugger) → debugger → supervisor
                                                            ├─ retry_stage(coder, hint) → coder → skill_d → lint
                                                            ├─ escalate_stage(microarch) → microarch → ...
                                                            └─ abort → END

    Args:
        with_checkpointer: Whether to compile with MemorySaver for
                           checkpointing and resume support.

    Returns:
        Compiled StateGraph ready for invoke/stream.
    """
    builder = StateGraph(VeriFlowState)

    # ── Add nodes ──────────────────────────────────────────────────
    builder.add_node("architect", node_architect)
    builder.add_node("architect_retry", node_architect_retry)
    builder.add_node("microarch", node_microarch)
    builder.add_node("microarch_retry", node_microarch_retry)
    builder.add_node("timing", node_timing)
    builder.add_node("timing_retry", node_timing_retry)
    builder.add_node("coder", node_coder)
    builder.add_node("skill_d", node_skill_d)
    builder.add_node("tool_check", node_tool_check)
    builder.add_node("lint", node_lint)
    builder.add_node("sim", node_sim)
    builder.add_node("synth", node_synth)
    builder.add_node("debugger", node_debugger)
    builder.add_node("supervisor", node_supervisor)

    # ── Linear edges (always-executed stages) ──────────────────────
    builder.add_edge(START, "architect")
    builder.add_conditional_edges("architect", _route_architect)
    builder.add_edge("architect_retry", "architect")
    builder.add_conditional_edges("microarch", _route_microarch)
    builder.add_edge("microarch_retry", "microarch")
    builder.add_conditional_edges("timing", _route_timing)
    builder.add_edge("timing_retry", "timing")

    # ── coder conditional edge ─────────────────────────────────────
    # pass → skill_d, fail → supervisor
    builder.add_conditional_edges("coder", _route_coder)

    # ── skill_d conditional edge ───────────────────────────────────
    # pass → tool_check, fail → supervisor
    builder.add_conditional_edges("skill_d", _route_skill_d)

    # tool_check → conditional routing (skip unavailable EDA stages)
    builder.add_conditional_edges("tool_check", _route_tool_check)

    # ── EDA check edges ────────────────────────────────────────────
    # pass → next, fail → supervisor
    builder.add_conditional_edges("lint", _route_lint)
    builder.add_conditional_edges("sim", _route_sim)
    builder.add_conditional_edges("synth", _route_synth)

    # ── Debugger → supervisor (re-evaluate after fix) ──────────────
    builder.add_conditional_edges("debugger", _route_debugger)

    # ── Supervisor → target stage (LLM-driven routing) ─────────────
    builder.add_conditional_edges("supervisor", _route_supervisor)

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
