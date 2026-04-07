"""Markdown formatters for chat pipeline progress.

Pure functions that translate pipeline state deltas into formatted markdown
strings suitable for streaming display in Gradio ChatInterface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

STAGE_ORDER = [
    "architect", "microarch", "timing", "coder",
    "skill_d", "lint", "sim", "synth",
]

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

STATUS_ICONS = {
    "pending": "\u2b1c",    # white square
    "running": "\u23f3",    # hourglass
    "pass": "\u2705",       # check mark
    "fail": "\u274c",       # cross mark
    "retry": "\U0001f504",  # retry arrow
}


def format_pipeline_start(requirement_text: str) -> str:
    """Format the pipeline start header."""
    preview = requirement_text.strip().split("\n")[0][:120]
    return (
        f"### RTL Design Pipeline\n"
        f"> {preview}\n\n"
        f"{_format_progress_bar([], set(), set(), {})}\n\n"
        f"Initializing pipeline...\n"
    )


def format_stage_progress(
    stage_name: str,
    stage_output: dict | Any,
    all_completed: list[str],
    all_failed: list[str],
    retry_counts: dict[str, int],
    stage_num: int = 0,
    total_stages: int = 8,
) -> str:
    """Format a single stage completion update."""
    label = STAGE_LABELS.get(stage_name, stage_name)

    # Extract fields from StageOutput-like object
    success = getattr(stage_output, "success", None)
    if success is None and isinstance(stage_output, dict):
        success = stage_output.get("success", False)

    duration = getattr(stage_output, "duration_s", 0)
    errors = getattr(stage_output, "errors", [])
    artifacts = getattr(stage_output, "artifacts", [])
    metrics = getattr(stage_output, "metrics", {})
    if isinstance(stage_output, dict):
        duration = stage_output.get("duration_s", 0)
        errors = stage_output.get("errors", [])
        artifacts = stage_output.get("artifacts", [])
        metrics = stage_output.get("metrics", {})

    icon = STATUS_ICONS["pass"] if success else STATUS_ICONS["fail"]
    status = "PASSED" if success else "FAILED"

    # Build progress bar
    progress = _format_progress_bar(
        all_completed, set(all_failed), set(), retry_counts,
    )

    lines = [
        f"### RTL Design Pipeline\n",
        progress,
        f"\n---\n",
        f"**{icon} Stage {stage_num}/{total_stages}: {label}** — {status} ({duration:.1f}s)\n",
    ]

    # Artifacts
    if artifacts:
        artifact_names = [Path(a).name for a in artifacts[:5]]
        lines.append(f"- Artifacts: `{', '.join(artifact_names)}`")

    # Key metrics
    if metrics:
        for key, value in list(metrics.items())[:4]:
            if key not in ("token_usage",) and value is not None:
                formatted = f"{value:.1f}" if isinstance(value, float) else str(value)
                lines.append(f"- {key}: {formatted}")

    # Errors
    if errors:
        lines.append(f"\n<details><summary>Errors ({len(errors)})</summary>\n")
        for err in errors[:5]:
            lines.append(f"```\n{err[:500]}\n```")
        lines.append("</details>")

    return "\n".join(lines) + "\n"


def format_debugger_event(
    feedback_source: str,
    retry_count: int,
    max_retries: int,
    rollback_target: str,
    error_category: str = "",
    all_completed: list[str] | None = None,
    all_failed: list[str] | None = None,
    retry_counts: dict[str, int] | None = None,
) -> str:
    """Format a debugger feedback loop notification."""
    source_label = STAGE_LABELS.get(feedback_source, feedback_source)
    target_label = STAGE_LABELS.get(rollback_target, rollback_target)

    progress = _format_progress_bar(
        all_completed or [],
        set(all_failed or []),
        set(),
        retry_counts or {},
    )

    return (
        f"### RTL Design Pipeline\n"
        f"{progress}\n"
        f"\n---\n"
        f"**{STATUS_ICONS['retry']} Feedback Loop Active**\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| Trigger | {source_label} failed |\n"
        f"| Attempt | {retry_count}/{max_retries} |\n"
        f"| Error type | {error_category or 'analyzing...'} |\n"
        f"| Rollback target | {target_label} |\n\n"
        f"Debugger is analyzing errors and applying fixes...\n"
    )


def format_final_summary(
    final_state: dict,
    project_dir: str | Path,
) -> str:
    """Format end-of-pipeline summary."""
    completed = final_state.get("stages_completed", [])
    failed = final_state.get("stages_failed", [])
    token_usage = final_state.get("token_usage", 0)
    token_budget = final_state.get("token_budget", 0)

    success = len(failed) == 0 or (
        # Check if synth passed (final stage)
        "synth" in completed
    )

    icon = STATUS_ICONS["pass"] if success else STATUS_ICONS["fail"]
    title = "Pipeline Complete!" if success else "Pipeline Failed"

    lines = [
        f"\n---\n",
        f"## {icon} {title}\n",
        f"\n| Metric | Value |",
        f"|--------|-------|",
        f"| Stages completed | {len(completed)}/{len(STAGE_ORDER)} |",
        f"| Stages failed | {len(failed)} |",
        f"| Token usage | {token_usage:,} / {token_budget:,} |",
    ]

    # Show generated files
    rtl_dir = Path(project_dir) / "workspace" / "rtl"
    if rtl_dir.exists():
        rtl_files = list(rtl_dir.glob("*.v"))
        if rtl_files:
            lines.append(f"\n### Generated RTL Files\n")
            for f in rtl_files:
                size = f.stat().st_size
                lines.append(f"- `{f.name}` ({size:,} bytes)")

    return "\n".join(lines) + "\n"


def format_rtl_code_display(project_dir: str | Path) -> str:
    """Read and format generated RTL files as code blocks."""
    rtl_dir = Path(project_dir) / "workspace" / "rtl"
    if not rtl_dir.exists():
        return ""

    rtl_files = sorted(rtl_dir.glob("*.v"))
    if not rtl_files:
        return ""

    lines = ["\n### Generated Verilog Code\n"]
    for f in rtl_files:
        try:
            content = f.read_text(encoding="utf-8")
            # Truncate very long files
            if len(content) > 3000:
                content = content[:3000] + "\n// ... truncated ..."
            lines.append(f"**`{f.name}`**\n```verilog\n{content}\n```\n")
        except OSError:
            pass

    return "\n".join(lines)


def format_inspection_response(
    message: str,
    project_dir: str | Path,
) -> str:
    """Format a response for file inspection queries."""
    pdir = Path(project_dir)
    msg_lower = message.lower()

    # Check what the user wants to see
    if "rtl" in msg_lower or "code" in msg_lower or "verilog" in msg_lower:
        return format_rtl_code_display(project_dir)

    if "spec" in msg_lower:
        spec_path = pdir / "workspace" / "docs" / "spec.json"
        if spec_path.exists():
            content = spec_path.read_text(encoding="utf-8")
            return f"### spec.json\n```json\n{content[:3000]}\n```"
        return "No spec.json found. Run the pipeline first."

    if "synth" in msg_lower or "report" in msg_lower:
        report_path = pdir / "workspace" / "docs" / "synth_report.json"
        if report_path.exists():
            content = report_path.read_text(encoding="utf-8")
            return f"### Synthesis Report\n```json\n{content[:3000]}\n```"
        return "No synthesis report found. Run the pipeline first."

    if "timing" in msg_lower:
        timing_path = pdir / "workspace" / "docs" / "timing_model.yaml"
        if timing_path.exists():
            content = timing_path.read_text(encoding="utf-8")
            return f"### Timing Model\n```yaml\n{content[:3000]}\n```"
        return "No timing model found. Run the pipeline first."

    if "quality" in msg_lower or "skill" in msg_lower:
        report_path = pdir / "workspace" / "docs" / "quality_report.json"
        if report_path.exists():
            content = report_path.read_text(encoding="utf-8")
            return f"### Quality Report\n```json\n{content[:3000]}\n```"
        return "No quality report found."

    # Default: show all available files
    return _format_file_listing(project_dir)


def _format_progress_bar(
    completed: list[str],
    failed: set[str],
    running: set[str],
    retry_counts: dict[str, int],
) -> str:
    """Build inline progress bar with stage status icons."""
    cells = []
    labels = []

    for stage in STAGE_ORDER:
        if stage in completed:
            icon = STATUS_ICONS["pass"]
        elif stage in failed:
            retries = retry_counts.get(stage, 0)
            icon = STATUS_ICONS["retry"] if retries > 0 else STATUS_ICONS["fail"]
        elif stage in running:
            icon = STATUS_ICONS["running"]
        else:
            icon = STATUS_ICONS["pending"]

        short = stage[:4]
        cells.append(f"{icon}")
        labels.append(f"{short}")

    # Single-line compact progress
    cell_line = " ".join(f"[{c}]" for c in cells)
    label_line = " ".join(f" {l:>4}" for l in labels)

    return f"`{cell_line}`\n`{label_line}`"


def _format_file_listing(project_dir: str | Path) -> str:
    """List all generated files in the project."""
    pdir = Path(project_dir)
    lines = ["### Project Files\n"]

    for subdir in ["workspace/docs", "workspace/rtl", "workspace/tb"]:
        d = pdir / subdir
        if d.exists():
            files = sorted(d.glob("*"))
            if files:
                lines.append(f"**{subdir}/**")
                for f in files:
                    if f.is_file():
                        lines.append(f"- `{f.name}` ({f.stat().st_size:,} bytes)")
                lines.append("")

    return "\n".join(lines)
