"""LangGraph graph assembly for VeriFlow-Agent.

Defines the complete RTL design pipeline as a LangGraph StateGraph:
  architect → microarch → [timing] → coder → skill_d → [sim_loop] → [synth] → END

Supports three modes:
  - quick:      architect → microarch → coder → skill_d
  - standard:   all stages
  - enterprise: all stages (with stricter quality gates)

The graph uses:
  - Conditional edges for mode-based routing and quality gates
  - MemorySaver checkpointing for resume capability
  - VeriFlowState as the shared state between nodes
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from veriflow_agent.graph.state import (
    VeriFlowState,
    StageOutput,
    create_initial_state,
    get_mode_stages,
)
from veriflow_agent.agents.architect import ArchitectAgent
from veriflow_agent.agents.microarch import MicroArchAgent
from veriflow_agent.agents.timing import TimingAgent
from veriflow_agent.agents.coder import CoderAgent
from veriflow_agent.agents.skill_d import SkillDAgent
from veriflow_agent.agents.debugger import DebuggerAgent
from veriflow_agent.agents.synth import SynthAgent
from veriflow_agent.tools.lint import IverilogTool
from veriflow_agent.tools.simulate import VvpTool

logger = logging.getLogger("veriflow")


# ── Node wrapper functions ────────────────────────────────────────────


def _run_stage(
    state: VeriFlowState,
    agent_cls: type,
    **extra_ctx: Any,
) -> dict[str, Any]:
    """Generic stage runner: instantiate agent, execute, return state updates.

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
        "mode": state.get("mode", "standard"),
        **extra_ctx,
    }

    t0 = time.perf_counter()
    try:
        result = agent.execute(context)
    except Exception as e:
        from veriflow_agent.agents.base import AgentResult
        result = AgentResult(
            success=False,
            stage=agent.name,
            errors=[f"Unhandled exception: {e}"],
        )
    elapsed = time.perf_counter() - t0
    status = "PASS" if result.success else "FAIL"
    logger.info("[%s] %s completed in %.2fs", status, agent.name, elapsed)

    stage_name = agent.name
    updates: dict[str, Any] = {
        "current_stage": stage_name,
    }

    # Record stage completion/failure
    completed = list(state.get("stages_completed", []))
    failed = list(state.get("stages_failed", []))
    retry_count = dict(state.get("retry_count", {}))

    if result.success:
        if stage_name not in completed:
            completed.append(stage_name)
        updates["stages_completed"] = completed
    else:
        if stage_name not in failed:
            failed.append(stage_name)
        updates["stages_failed"] = failed
        # Increment retry counter
        retry_count[stage_name] = retry_count.get(stage_name, 0) + 1
        updates["retry_count"] = retry_count

    # Store stage output
    stage_output = StageOutput(
        success=result.success,
        artifacts=result.artifacts,
        metrics=result.metrics,
        errors=result.errors,
        warnings=result.warnings,
        metadata=result.metadata,
        duration_s=round(elapsed, 2),
    )
    updates[f"{stage_name}_output"] = stage_output

    # Quality gate
    quality_gates = dict(state.get("quality_gates_passed", {}))
    quality_gates[stage_name] = result.success
    updates["quality_gates_passed"] = quality_gates

    return updates


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
    """Stage 3.5: Static analysis + internal lint loop.

    Runs iverilog lint. On failure, invokes the Debugger and retries,
    up to max_retries iterations.
    """
    project_dir = Path(state.get("project_dir", "."))
    max_retries = 5

    # Run static analysis first
    updates = _run_stage(state, SkillDAgent)

    # Also run iverilog lint with retry loop
    lint_tool = IverilogTool()
    if lint_tool.validate_prerequisites():
        rtl_dir = project_dir / "workspace" / "rtl"
        if rtl_dir.exists():
            rtl_files = list(rtl_dir.glob("*.v"))
            non_tb = IverilogTool.filter_testbench_files(rtl_files)

            if non_tb:
                for attempt in range(max_retries):
                    lint_result = lint_tool.run(
                        mode="lint",
                        files=non_tb,
                        cwd=project_dir,
                    )
                    parsed = lint_tool.parse_lint_output(lint_result)

                    if parsed.passed:
                        # Update skill_d output to reflect lint pass
                        quality_gates = dict(state.get("quality_gates_passed", {}))
                        quality_gates["skill_d_lint"] = True
                        updates["quality_gates_passed"] = quality_gates
                        break

                    # Lint failed → invoke debugger
                    debugger = DebuggerAgent()
                    dbg_ctx = {
                        "project_dir": str(project_dir),
                        "mode": state.get("mode", "standard"),
                        "error_type": "lint",
                        "error_log": (lint_result.stdout or "") + (lint_result.stderr or ""),
                        "rtl_files": [str(f) for f in non_tb],
                    }
                    try:
                        debugger.execute(dbg_ctx)
                    except Exception:
                        pass

                    # Refresh file list (debugger may have changed files)
                    non_tb = IverilogTool.filter_testbench_files(list(rtl_dir.glob("*.v")))

                    # If this was the last attempt, record lint failure
                    if attempt == max_retries - 1:
                        quality_gates = dict(state.get("quality_gates_passed", {}))
                        quality_gates["skill_d_lint"] = False
                        updates["quality_gates_passed"] = quality_gates

    return updates


def node_sim_loop(state: VeriFlowState) -> dict[str, Any]:
    """Stage 4: Simulation verification loop.

    Compiles and runs testbench, retries with debugger on failure.
    """
    project_dir = Path(state.get("project_dir", "."))
    max_retries = 5

    sim_tool = VvpTool()
    if not sim_tool.validate_prerequisites():
        return {"current_stage": "sim_loop"}

    # Discover files
    rtl_dir = project_dir / "workspace" / "rtl"
    tb_dir = project_dir / "workspace" / "tb"

    rtl_files = list(rtl_dir.glob("*.v")) if rtl_dir.exists() else []
    tb_files = list(tb_dir.glob("tb_*.v")) if tb_dir.exists() else []

    if not rtl_files or not tb_files:
        return {
            "current_stage": "sim_loop",
            "stages_failed": list(state.get("stages_failed", [])) + ["sim_loop"],
        }

    for attempt in range(max_retries):
        for tb in tb_files:
            sim_result = sim_tool.run(
                testbench=tb,
                rtl_files=rtl_files,
                cwd=project_dir,
            )
            parsed = sim_tool.parse_sim_output(sim_result)

            if parsed.passed:
                completed = list(state.get("stages_completed", []))
                if "sim_loop" not in completed:
                    completed.append("sim_loop")
                return {
                    "current_stage": "sim_loop",
                    "stages_completed": completed,
                    "quality_gates_passed": {
                        **state.get("quality_gates_passed", {}),
                        "sim_loop": True,
                    },
                }

            # Sim failed → invoke debugger
            debugger = DebuggerAgent()
            dbg_ctx = {
                "project_dir": str(project_dir),
                "mode": state.get("mode", "standard"),
                "error_type": "sim",
                "error_log": parsed.output[:5000],
                "rtl_files": [str(f) for f in rtl_files],
                "timing_model_yaml": str(project_dir / "workspace" / "docs" / "timing_model.yaml"),
            }
            try:
                debugger.execute(dbg_ctx)
            except Exception:
                pass

            # Refresh file list
            rtl_files = list(rtl_dir.glob("*.v"))

    # Exhausted retries
    return {
        "current_stage": "sim_loop",
        "stages_failed": list(state.get("stages_failed", [])) + ["sim_loop"],
        "quality_gates_passed": {
            **state.get("quality_gates_passed", {}),
            "sim_loop": False,
        },
    }


def node_synth(state: VeriFlowState) -> dict[str, Any]:
    """Stage 5: Synthesis + KPI comparison."""
    return _run_stage(state, SynthAgent)


# ── Routing functions ─────────────────────────────────────────────────


def _route_after_microarch(
    state: VeriFlowState,
) -> Literal["timing", "coder"]:
    """Route after microarch: timing only in standard/enterprise mode."""
    mode = state.get("mode", "standard")
    stages = get_mode_stages(mode)

    if "timing" in stages:
        return "timing"
    return "coder"


def _route_after_skill_d(
    state: VeriFlowState,
) -> Literal["sim_loop", "__end__"]:
    """Route after skill_d: sim_loop in standard/enterprise, else END."""
    mode = state.get("mode", "standard")
    stages = get_mode_stages(mode)

    if "sim_loop" in stages or "debugger" in stages:
        return "sim_loop"
    return END


def _route_after_synth(
    state: VeriFlowState,
) -> Literal["__end__"]:
    """Synth is always the last stage."""
    return END


# ── Graph builder ─────────────────────────────────────────────────────


def create_veriflow_graph(
    *,
    with_checkpointer: bool = True,
) -> StateGraph:
    """Build the VeriFlow LangGraph pipeline.

    Creates a StateGraph with all 7 pipeline stages as nodes,
    connected by conditional edges for mode-based routing.

    Args:
        with_checkpointer: Whether to compile with MemorySaver for
                           checkpointing and resume support.

    Returns:
        Compiled StateGraph ready for invoke/stream.

    Example:
        graph = create_veriflow_graph()
        result = graph.invoke(
            create_initial_state(project_dir="/path/to/project", mode="standard")
        )
    """
    builder = StateGraph(VeriFlowState)

    # Add nodes
    builder.add_node("architect", node_architect)
    builder.add_node("microarch", node_microarch)
    builder.add_node("timing", node_timing)
    builder.add_node("coder", node_coder)
    builder.add_node("skill_d", node_skill_d)
    builder.add_node("sim_loop", node_sim_loop)
    builder.add_node("synth", node_synth)

    # Fixed edges: linear pipeline segments
    builder.add_edge(START, "architect")
    builder.add_edge("architect", "microarch")
    builder.add_edge("timing", "coder")
    builder.add_edge("coder", "skill_d")
    builder.add_edge("sim_loop", "synth")
    builder.add_edge("synth", END)

    # Conditional edges
    builder.add_conditional_edges("microarch", _route_after_microarch)
    builder.add_conditional_edges("skill_d", _route_after_skill_d)

    # Compile
    checkpointer = MemorySaver() if with_checkpointer else None
    graph = builder.compile(
        checkpointer=checkpointer,
        name="veriflow-pipeline",
    )

    return graph
