"""VeriFlow-Agent CLI entry point.

Provides the command-line interface for the RTL design pipeline.

Usage:
    veriflow-agent run --project-dir /path/to/project
    veriflow-agent validate --stage 1 --project-dir /path/to/project
    veriflow-agent complete --stage 1 --project-dir /path/to/project
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from veriflow_agent.graph import create_initial_state, create_veriflow_graph

console = Console()
logger = logging.getLogger("veriflow")


@click.group()
def cli():
    """VeriFlow-Agent: Agent-based RTL design pipeline."""
    pass


@cli.command()
@click.option("--project-dir", required=True, help="Path to the project directory.")
@click.option("--resume", is_flag=True, default=False, help="Resume from last checkpoint.")
def run(project_dir: str, resume: bool):
    """Run the full RTL design pipeline."""
    project_dir = Path(project_dir).resolve()

    # Validate project directory
    if not project_dir.exists():
        console.print(f"[red]Project directory not found: {project_dir}")
        sys.exit(1)

    requirement_md = project_dir / "requirement.md"
    if not requirement_md.exists() and not resume:
        console.print(f"[red]requirement.md not found in {project_dir}")
        sys.exit(1)

    # Show startup info
    console.print(Panel(
            f"[bold]VeriFlow-Agent[/] v0.1.0\n"
            f"Project: {project_dir.name}",
            title="RTL Design Pipeline",
        ))

    # Load LLM config from config.json
    from veriflow_agent.gateway.config import VeriFlowConfig
    _cfg = VeriFlowConfig.load()
    _llm = _cfg.to_llm_config()

    # Load or create initial state
    if resume:
        state = _load_checkpoint(project_dir)
        if state is None:
            console.print("[yellow]No checkpoint found, starting fresh.")
            state = create_initial_state(
                str(project_dir),
                llm_backend=_cfg.llm_backend,
                llm_api_key=_llm.api_key,
                llm_base_url=_llm.base_url,
                llm_model=_llm.model,
            )
        else:
            console.print("[green]Resuming from checkpoint.")
    else:
        state = create_initial_state(
            str(project_dir),
            llm_backend=_cfg.llm_backend,
            llm_api_key=_llm.api_key,
            llm_base_url=_llm.base_url,
            llm_model=_llm.model,
        )

    # Build and run the graph
    graph = create_veriflow_graph(with_checkpointer=True)

    config = {"configurable": {"thread_id": f"veriflow-{project_dir.name}"}}

    console.print("[bold blue]Running pipeline...[/bold blue]")
    result = dict(state)
    t_pipeline_start = time.perf_counter()
    try:
        for event in graph.stream(state, config):
            for node_name, updates in event.items():
                result.update(updates)
                # Print real-time stage transitions from event_stream
                new_events = updates.get("event_stream", [])
                for evt in new_events:
                    evt_type = evt.get("event_type", evt.get("type", ""))
                    if evt_type == "stage_start":
                        console.print(f"  [cyan]>> {evt.get('stage', node_name)} started[/cyan]")
                    elif evt_type == "stage_end":
                        ok = "PASS" if evt.get("payload", {}).get("success", True) else "FAIL"
                        dur = evt.get("payload", {}).get("duration_s", 0)
                        color = "green" if ok == "PASS" else "red"
                        console.print(f"  [{color}]<< {evt.get('stage', node_name)} {ok} ({dur:.1f}s)[/{color}]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrupted by user.")
        _save_checkpoint(project_dir, result or state)
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Pipeline failed: {e}")
        _save_checkpoint(project_dir, result or state)
        sys.exit(1)

    # Display results
    total_time = time.perf_counter() - t_pipeline_start
    _display_results(result)
    console.print(f"\n[bold]Total pipeline time:[/bold] {total_time:.1f}s")

    # Print observability summary
    _display_observability_summary(result)


@cli.command()
@click.option("--stage", required=True, type=int, help="Stage number to validate.")
@click.option("--project-dir", required=True, help="Path to the project directory.")
def lint_stage(stage: int, project_dir: str):
    """Validate stage output (deterministic, no LLM)."""
    project_dir = Path(project_dir).resolve()
    console.print(f"[bold]Validating Stage {stage}...")

    errors = _validate_stage(stage, project_dir)

    if errors:
        console.print(f"[red]Stage {stage} validation FAILED:")
        for e in errors:
            console.print(f"  ✗ {e}")
        sys.exit(1)
    else:
        console.print(f"[green]Stage {stage} validation PASSED.")


@cli.command()
@click.option("--stage", required=True, type=int, help="Stage number to complete.")
@click.option("--project-dir", required=True, help="Path to the project directory.")
def mark_complete(stage: int, project_dir: str):
    """Mark stage as complete."""
    project_dir = Path(project_dir).resolve()

    state = _load_checkpoint(project_dir) or {}
    completed = list(state.get("stages_completed", []))

    stage_name = _stage_number_to_name(stage)
    if stage_name and stage_name not in completed:
        completed.append(stage_name)

    state["stages_completed"] = completed
    state["current_stage"] = stage_name

    _save_checkpoint(project_dir, state)
    console.print(f"[green]Stage {stage} ({stage_name}) marked complete.")


# ── Helper functions ──────────────────────────────────────────────────


def _stage_number_to_name(stage: int) -> str | None:
    """Convert stage number to internal name."""
    mapping = {
        1: "architect", 15: "microarch", 2: "timing",
        3: "coder", 35: "skill_d", 4: "sim", 5: "synth",
    }
    return mapping.get(stage)


def _load_checkpoint(project_dir: Path) -> dict | None:
    """Load pipeline state from checkpoint file."""
    checkpoint = project_dir / ".veriflow" / "checkpoint.json"
    if not checkpoint.exists():
        return None
    try:
        return json.loads(checkpoint.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        console.print(f"[yellow]Warning: Checkpoint file is corrupted ({e}). Starting fresh.")
        return None


def _save_checkpoint(project_dir: Path, state: dict) -> None:
    """Save pipeline state to checkpoint file."""
    checkpoint = project_dir / ".veriflow" / "checkpoint.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _validate_stage(stage: int, project_dir: Path) -> list[str]:
    """Validate stage output without LLM."""
    errors = []

    if stage == 1:
        spec_path = project_dir / "workspace" / "docs" / "spec.json"
        if not spec_path.exists():
            errors.append("spec.json not found")
        else:
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                if "design_name" not in spec:
                    errors.append("spec.json missing 'design_name'")
                if not spec.get("modules"):
                    errors.append("spec.json has no modules")
            except json.JSONDecodeError as e:
                errors.append(f"spec.json parse error: {e}")

    elif stage in (15, 2):
        docs_dir = project_dir / "workspace" / "docs"
        if stage == 15:
            if not (docs_dir / "micro_arch.md").exists():
                errors.append("micro_arch.md not found")
        if stage == 2:
            if not (docs_dir / "timing_model.yaml").exists():
                errors.append("timing_model.yaml not found")
            tb_dir = project_dir / "workspace" / "tb"
            if not list(tb_dir.glob("tb_*.v")):
                errors.append("No testbench files found")

    elif stage == 3:
        rtl_dir = project_dir / "workspace" / "rtl"
        rtl_files = list(rtl_dir.glob("*.v")) if rtl_dir.exists() else []
        if not rtl_files:
            errors.append("No RTL files found")
        else:
            # Run lint
            from veriflow_agent.tools.lint import IverilogTool
            tool = IverilogTool()
            if tool.validate_prerequisites():
                non_tb = IverilogTool.filter_testbench_files(rtl_files)
                result = tool.run(mode="lint", files=non_tb, cwd=project_dir)
                lint = tool.parse_lint_output(result)
                if not lint.passed:
                    errors.append(f"Lint failed: {len(lint.errors)} errors")

    elif stage in (35, 4):
        rtl_dir = project_dir / "workspace" / "rtl"
        tb_dir = project_dir / "workspace" / "tb"
        rtl_files = list(rtl_dir.glob("*.v")) if rtl_dir.exists() else []
        tb_files = list(tb_dir.glob("tb_*.v")) if tb_dir.exists() else []

        if not rtl_files:
            errors.append("No RTL files found")
        if not tb_files:
            errors.append("No testbench files found")

        if stage == 35 and not errors:
            # Lint check
            from veriflow_agent.tools.lint import IverilogTool
            tool = IverilogTool()
            if tool.validate_prerequisites():
                non_tb = IverilogTool.filter_testbench_files(rtl_files)
                result = tool.run(mode="lint", files=non_tb, cwd=project_dir)
                lint = tool.parse_lint_output(result)
                if not lint.passed:
                    errors.append(f"Lint: {len(lint.errors)} errors")

        if stage == 4 and not errors:
            # Simulation check
            from veriflow_agent.tools.simulate import VvpTool
            tool = VvpTool()
            if tool.validate_prerequisites():
                for tb in tb_files:
                    result = tool.run(testbench=tb, rtl_files=rtl_files, cwd=project_dir)
                    sim = tool.parse_sim_output(result)
                    if not sim.passed:
                        errors.append(f"Sim ({tb.name}): {sim.fail_count} failures")

    elif stage == 5:
        report_path = project_dir / "workspace" / "docs" / "synth_report.json"
        if not report_path.exists():
            errors.append("synth_report.json not found")

    return errors


def _safe_get(obj, key, default=None):
    """Safely access attribute or dict key from stage output."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _display_results(result: dict) -> None:
    """Display pipeline results using Rich."""
    completed = result.get("stages_completed", [])
    failed = result.get("stages_failed", [])

    if not failed:
        console.print("\n[bold green]Pipeline completed successfully![/bold green]")
    else:
        console.print(f"\n[bold red]Pipeline failed at stages: {failed}")

    # Results table
    table = Table(title="Pipeline Results")
    table.add_column("Stage", style="bold")
    table.add_column("Status", style="bold")
    table.add_column("Artifacts")
    table.add_column("Time", justify="right")

    for stage_name in ["architect", "microarch", "timing", "coder", "skill_d", "lint", "sim", "synth", "debugger"]:
        output = result.get(f"{stage_name}_output")
        if output:
            success = _safe_get(output, "success", False)
            status = "[green]PASS" if success else "[red]FAIL"
            artifacts_list = _safe_get(output, "artifacts", []) or []
            artifacts = ", ".join(artifacts_list) if artifacts_list else "-"
            dur_s = _safe_get(output, "duration_s", 0.0)
            time_str = f"{dur_s:.1f}s" if dur_s > 0 else "-"
            table.add_row(stage_name, status, artifacts, time_str)

        else:
            table.add_row(stage_name, "[dim]-", "-", "-")

    console.print(table)

    # Show key metrics
    metrics_panel_parts = []
    for stage_name in ["architect", "coder", "synth"]:
        output = result.get(f"{stage_name}_output")
        if output:
            metrics = _safe_get(output, "metrics", None)
            if metrics:
                metrics_panel_parts.append(f"[bold]{stage_name}:[/] {metrics}")

    if metrics_panel_parts:
        console.print("\n".join(metrics_panel_parts))

    # Show timing summary
    total_duration = 0.0
    stage_times = []
    for stage_name in ["architect", "microarch", "timing", "coder", "skill_d", "lint", "sim", "synth", "debugger"]:
        output = result.get(f"{stage_name}_output")
        if output:
            dur = _safe_get(output, "duration_s", 0.0)
            if dur > 0:
                stage_times.append((stage_name, dur))

                total_duration += dur

    if stage_times:
        timing_table = Table(title="Timing Summary")
        timing_table.add_column("Stage", style="bold")
        timing_table.add_column("Duration", justify="right")
        for name, dur in stage_times:
            timing_table.add_row(name, f"{dur:.2f}s")
        timing_table.add_row("[bold]Total[/]", f"{total_duration:.2f}s")
        console.print(timing_table)


def _display_observability_summary(result: dict) -> None:
    """Display observability metrics from pipeline execution."""
    total_tokens = result.get("total_tokens_used", 0)
    total_cost = result.get("total_cost_usd", 0.0)
    total_llm_calls = result.get("total_llm_calls", 0)
    total_tool_calls = result.get("total_tool_calls", 0)
    stage_durations = result.get("stage_durations", {})
    event_stream = result.get("event_stream", [])

    if not event_stream and not total_tokens:
        return  # No observability data

    obs_table = Table(title="Observability Summary")
    obs_table.add_column("Metric", style="bold")
    obs_table.add_column("Value", justify="right")

    obs_table.add_row("Total Tokens", f"{total_tokens:,}")
    obs_table.add_row("Estimated Cost", f"${total_cost:.4f}")
    obs_table.add_row("LLM Calls", str(total_llm_calls))
    obs_table.add_row("Tool Calls", str(total_tool_calls))
    obs_table.add_row("Events Collected", str(len(event_stream)))

    console.print(obs_table)

    # Print per-stage LLM trace summaries if available
    for stage_name in ["architect", "microarch", "timing", "coder", "skill_d", "debugger"]:
        output = result.get(f"{stage_name}_output")
        if output and hasattr(output, "llm_trace") and output.llm_trace:
            trace = output.llm_trace
            console.print(
                f"  [dim]{stage_name}: "
                f"in={trace.input_tokens} out={trace.output_tokens} "
                f"${trace.cost_usd:.4f} "
                f"{trace.latency_ms}ms "
                f"steps={len(trace.steps)}[/dim]"
            )


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind the chat server to.")
@click.option("--port", default=7860, help="Port to run the chat server on.")
@click.option("--share", is_flag=True, default=False, help="Create a public Gradio share URL.")
def chat(host: str, port: int, share: bool):
    """Launch the interactive Chat UI for VeriFlow-Agent."""
    from veriflow_agent.chat import launch_chat

    console.print("[green]Starting VeriFlow-Agent Chat UI...")
    console.print(f"[dim]URL: http://localhost:{port}")
    if share:
        console.print("[dim]Public share URL will be generated.")
    console.print("")

    launch_chat(host=host, port=port, share=share)


@cli.command()
@click.option("--host", default="127.0.0.1", help="Gateway bind host.")
@click.option("--port", default=18789, help="Gateway bind port.")
@click.option("--workspace", "-w", default=None, help="Default workspace directory for pipeline output.")
@click.option("--telegram", is_flag=True, default=False, help="Also start Telegram bot channel.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="DEBUG level logging (show all chunks, messages).")
@click.option("--quiet", "-q", is_flag=True, default=False, help="WARNING level logging (errors only).")
def gateway(host: str, port: int, workspace: str | None, telegram: bool, verbose: bool, quiet: bool):
    """Launch the VeriFlow-Agent Gateway daemon (WebSocket + WebChat + optional Telegram)."""
    from veriflow_agent.gateway import launch_gateway

    launch_gateway(host=host, port=port, enable_telegram=telegram, verbose=verbose, quiet=quiet, workspace=workspace)


@cli.command()
@click.option("--project-dir", "-p", default=None, help="Path to project directory (optional).")
@click.option("--mode", default="standard", type=click.Choice(["quick", "standard", "enterprise"]), help="Pipeline mode.")
def tui(project_dir: str | None, mode: str):
    """Launch the terminal UI (self-contained, no gateway required).

    Usage:
        veriflow-agent tui
        veriflow-agent tui --project-dir ./my_alu
        veriflow-agent tui --project-dir ./my_alu --mode quick
    """
    from veriflow_agent.tui import launch_tui

    project_path = Path(project_dir) if project_dir else None
    launch_tui(project_dir=project_path, mode=mode)


def main():
    cli()
