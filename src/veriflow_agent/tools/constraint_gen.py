"""Constraint generator for synthesis.

Generates SDC/XDC-style constraint files from timing_model.yaml for use
with Yosys synthesis. This bridges the gap between the Timing stage output
and the Synthesis stage input, ensuring synthesis respects timing targets.

Supported constraint types:
- Clock definitions (create_clock)
- Input/output delays (set_input_delay / set_output_delay)
- Max delay paths (set_max_delay)
- False paths (set_false_path)
- Multicycle paths (set_multicycle_path)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ConstraintResult:
    """Result of constraint generation.

    Attributes:
        success: Whether generation completed without errors.
        constraint_path: Path to the generated constraint file.
        clock_constraints: Number of clock constraints generated.
        io_constraints: Number of I/O delay constraints generated.
        timing_constraints: Number of timing constraints generated.
        warnings: List of non-fatal warnings.
    """
    success: bool
    constraint_path: str = ""
    clock_constraints: int = 0
    io_constraints: int = 0
    timing_constraints: int = 0
    warnings: list[str] = field(default_factory=list)


def generate_constraints(
    timing_model_path: str | Path,
    output_path: str | Path,
    target_kpis: dict[str, Any] | None = None,
) -> ConstraintResult:
    """Generate SDC constraints from timing_model.yaml.

    Args:
        timing_model_path: Path to timing_model.yaml from Stage 2.
        output_path: Path to write the generated .sdc file.
        target_kpis: Optional target KPIs from spec.json (e.g. frequency_mhz).

    Returns:
        ConstraintResult with generation status.
    """
    timing_path = Path(timing_model_path)
    output = Path(output_path)
    warnings = []
    clock_count = 0
    io_count = 0
    timing_count = 0

    # Read timing model
    if not timing_path.exists():
        return ConstraintResult(
            success=False,
            warnings=[f"Timing model not found: {timing_path}"],
        )

    try:
        with open(timing_path, "r", encoding="utf-8") as f:
            timing_model = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        return ConstraintResult(
            success=False,
            warnings=[f"Failed to parse timing model: {e}"],
        )

    if not timing_model or not isinstance(timing_model, dict):
        # Allow empty model if target_kpis provided (generate defaults)
        if target_kpis:
            timing_model = {}
        else:
            return ConstraintResult(
                success=False,
                warnings=["Timing model is empty or invalid"],
            )

    # Build constraint lines
    lines = [
        "# Auto-generated SDC constraints from timing_model.yaml",
        "# VeriFlow-Agent Constraint Generator",
        "# Do not edit manually — regenerate from pipeline",
        "",
    ]

    # ── 1. Clock constraints ────────────────────────────────────────
    clocks = timing_model.get("clocks", [])
    if not clocks and target_kpis:
        # Generate default clock from target frequency
        freq_mhz = target_kpis.get("frequency_mhz", 100)
        period_ns = 1000.0 / freq_mhz if freq_mhz > 0 else 10.0
        lines.append(f"# Default clock from target frequency ({freq_mhz} MHz)")
        lines.append(f"create_clock -name clk -period {period_ns:.3f} [get_ports clk]")
        clock_count = 1
    elif isinstance(clocks, list):
        for clk in clocks:
            if isinstance(clk, dict):
                name = clk.get("name", "clk")
                period = clk.get("period_ns")
                frequency = clk.get("frequency_mhz")
                if period:
                    lines.append(
                        f"create_clock -name {name} -period {period:.3f} "
                        f"[get_ports {name}]"
                    )
                elif frequency:
                    period = 1000.0 / frequency
                    lines.append(
                        f"create_clock -name {name} -period {period:.3f} "
                        f"[get_ports {name}]"
                    )
                else:
                    warnings.append(f"Clock '{name}' has no period or frequency")
                    continue
                clock_count += 1
    elif isinstance(clocks, dict):
        name = clocks.get("name", "clk")
        period = clocks.get("period_ns", 10.0)
        lines.append(
            f"create_clock -name {name} -period {period:.3f} [get_ports {name}]"
        )
        clock_count = 1

    # ── 2. I/O delay constraints ────────────────────────────────────
    io_delays = timing_model.get("io_delays", [])
    if isinstance(io_delays, list):
        for io in io_delays:
            if isinstance(io, dict):
                direction = io.get("direction", "input")
                port = io.get("port", "")
                delay = io.get("delay_ns", 0)
                clock = io.get("clock", "clk")
                if not port:
                    continue
                if direction == "input":
                    lines.append(
                        f"set_input_delay -clock {clock} {delay:.3f} "
                        f"[get_ports {port}]"
                    )
                else:
                    lines.append(
                        f"set_output_delay -clock {clock} {delay:.3f} "
                        f"[get_ports {port}]"
                    )
                io_count += 1

    # ── 3. Timing constraints ───────────────────────────────────────
    # Max delay
    max_delays = timing_model.get("max_delays", [])
    if isinstance(max_delays, list):
        for md in max_delays:
            if isinstance(md, dict):
                delay = md.get("delay_ns", 0)
                from_port = md.get("from", "")
                to_port = md.get("to", "")
                if from_port and to_port:
                    lines.append(
                        f"set_max_delay {delay:.3f} "
                        f"-from [get_ports {from_port}] "
                        f"-to [get_ports {to_port}]"
                    )
                else:
                    lines.append(f"set_max_delay {delay:.3f}")
                timing_count += 1

    # False paths
    false_paths = timing_model.get("false_paths", [])
    if isinstance(false_paths, list):
        for fp in false_paths:
            if isinstance(fp, dict):
                from_port = fp.get("from", "")
                to_port = fp.get("to", "")
                if from_port and to_port:
                    lines.append(
                        f"set_false_path -from [get_ports {from_port}] "
                        f"-to [get_ports {to_port}]"
                    )
                    timing_count += 1

    # Multicycle paths
    multicycle = timing_model.get("multicycle_paths", [])
    if isinstance(multicycle, list):
        for mc in multicycle:
            if isinstance(mc, dict):
                cycles = mc.get("cycles", 2)
                from_port = mc.get("from", "")
                to_port = mc.get("to", "")
                if from_port and to_port:
                    lines.append(
                        f"set_multicycle_path {cycles} "
                        f"-from [get_ports {from_port}] "
                        f"-to [get_ports {to_port}]"
                    )
                    timing_count += 1

    # Add trailing newline
    lines.append("")

    # Write output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")

    return ConstraintResult(
        success=True,
        constraint_path=str(output),
        clock_constraints=clock_count,
        io_constraints=io_count,
        timing_constraints=timing_count,
        warnings=warnings,
    )


def read_constraint_file(path: str | Path) -> list[str]:
    """Read and parse constraint file into individual constraint lines.

    Args:
        path: Path to .sdc constraint file.

    Returns:
        List of non-empty, non-comment constraint lines.
    """
    p = Path(path)
    if not p.exists():
        return []

    content = p.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
