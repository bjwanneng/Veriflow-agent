"""Yosys synthesis tool wrapper.

Wraps the Yosys synthesis tool for RTL synthesis and area/stat reporting.
Generates a Yosys script dynamically and executes it via `yosys -p <script>`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veriflow_agent.tools.base import BaseTool, ToolResult, ToolStatus
from veriflow_agent.tools.eda_utils import find_eda_tool, get_eda_env


@dataclass
class SynthResult:
    """Parsed synthesis result.

    Attributes:
        success: Whether synthesis completed without errors.
        num_cells: Number of cells in synthesized design.
        num_wires: Number of wires.
        top_module: Name of the top module.
        stats_json: Parsed JSON from yosys stat -json (if available).
        raw_stats: Raw stat output text.
    """

    success: bool
    num_cells: int = 0
    num_wires: int = 0
    top_module: str = ""
    stats_json: dict[str, Any] | None = None
    raw_stats: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "num_cells": self.num_cells,
            "num_wires": self.num_wires,
            "top_module": self.top_module,
            "stats_json": self.stats_json,
            "raw_stats": self.raw_stats,
        }


class YosysTool(BaseTool):
    """Yosys synthesis wrapper.

    Generates a yosys script with read_verilog + synth + stat commands,
    then executes it and parses the output.

    Usage:
        tool = YosysTool()
        if tool.validate_prerequisites():
            result = tool.run(
                rtl_files=["rtl/top.v", "rtl/module_a.v"],
                top_module="top",
            )
            synth = tool.parse_synth_output(result, top_module="top")
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            name="yosys",
            config=config,
            executable=find_eda_tool("yosys"),
        )

    def validate_prerequisites(self) -> bool:
        """Check that yosys is available."""
        if self._executable and Path(self._executable).exists():
            return True
        return False

    def run(
        self,
        *,
        rtl_files: list[str | Path],
        top_module: str,
        cwd: str | Path | None = None,
    ) -> ToolResult:
        """Execute Yosys synthesis.

        Args:
            rtl_files: RTL source files to synthesize.
            top_module: Name of the top module.
            cwd: Working directory (defaults to current dir).

        Returns:
            ToolResult with yosys stdout/stderr.
        """
        # Filter out testbench files
        filtered_files = [
            f for f in rtl_files
            if not Path(f).name.startswith("tb_")
        ]

        if not filtered_files:
            return ToolResult(
                status=ToolStatus.FAILURE,
                errors=["No non-testbench RTL files provided"],
            )

        # Build yosys script
        read_cmds = "\n".join(f"read_verilog {Path(f).resolve()}" for f in filtered_files)
        script_content = f"{read_cmds}\nsynth -top {top_module}\nstat -json\n"

        cwd_path = Path(cwd) if cwd else Path(".")

        # Write script to temp file
        script_file = cwd_path / ".veriflow" / "synth.ys"
        script_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            script_file.write_text(script_content, encoding="utf-8")

            cmd = [self.executable, "-p", str(script_file)]

            result = self._execute(
                command=cmd,
                cwd=cwd_path,
                env=get_eda_env(),
                timeout=self.config.get("synth_timeout", 300),
            )

            return result

        finally:
            if script_file.exists():
                script_file.unlink()

    def parse_synth_output(
        self, result: ToolResult, top_module: str = ""
    ) -> SynthResult:
        """Parse Yosys output for synthesis statistics.

        Attempts JSON extraction first (from stat -json), falls back to
        regex-based parsing.

        Args:
            result: Raw ToolResult from run().
            top_module: Top module name for the report.

        Returns:
            Parsed SynthResult.
        """
        output = (result.stdout or "") + (result.stderr or "")
        success = result.return_code == 0

        # Try JSON extraction from stat -json output
        stats_json = None
        for line in output.splitlines():
            line = line.strip()
            if '"modules"' in line:
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and "modules" in parsed:
                        stats_json = parsed
                        break
                except json.JSONDecodeError:
                    continue

        # Extract cell/wire counts
        num_cells = 0
        num_wires = 0

        if stats_json:
            # Navigate JSON structure: modules -> top_module -> stats
            modules = stats_json.get("modules", {})
            if top_module in modules:
                mod_stats = modules[top_module]
                num_cells = mod_stats.get("num_cells", 0)
                num_wires = mod_stats.get("num_wires", 0)
            else:
                # Try first module
                for mod_data in modules.values():
                    num_cells = max(num_cells, mod_data.get("num_cells", 0))
                    num_wires = max(num_wires, mod_data.get("num_wires", 0))

        if num_cells == 0:
            # Fallback: regex extraction
            cell_match = re.search(r"Number of cells:\s*(\d+)", output)
            if cell_match:
                num_cells = int(cell_match.group(1))

        if num_wires == 0:
            wire_match = re.search(r"Number of wires:\s*(\d+)", output)
            if wire_match:
                num_wires = int(wire_match.group(1))

        return SynthResult(
            success=success,
            num_cells=num_cells,
            num_wires=num_wires,
            top_module=top_module,
            stats_json=stats_json,
            raw_stats=output,
        )
