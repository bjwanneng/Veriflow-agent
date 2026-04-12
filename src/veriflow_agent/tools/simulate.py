"""Simulation tool wrapper (iverilog compile + vvp run).

Wraps the iverilog/vvp two-step simulation flow:
1. Compile: iverilog -o <out.vvp> <testbench> <rtl files>
2. Simulate: vvp <out.vvp>

Parses PASS/FAIL from simulation output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veriflow_agent.tools.base import BaseTool, ToolResult, ToolStatus
from veriflow_agent.tools.eda_utils import find_eda_tool, get_eda_env


@dataclass
class SimResult:
    """Parsed simulation result.

    Attributes:
        passed: Whether simulation passed.
        pass_count: Number of PASS lines in output.
        fail_count: Number of FAIL lines in output.
        all_passed: Whether "ALL TESTS PASSED" appeared in output.
        output: Raw simulation output text.
    """

    passed: bool
    pass_count: int = 0
    fail_count: int = 0
    all_passed: bool = False
    output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "all_passed": self.all_passed,
            "output": self.output,
        }


class VvpTool(BaseTool):
    """Iverilog + Vvp simulation wrapper.

    Performs two-step simulation:
    1. Compile testbench + RTL with iverilog into .vvp
    2. Run .vvp with vvp

    Usage:
        tool = VvpTool()
        if tool.validate_prerequisites():
            result = tool.run(testbench="rtl/tb_top.v", rtl_files=["rtl/top.v"])
            sim = tool.parse_sim_output(result)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            name="vvp",
            config=config,
        )
        self._iverilog_path = find_eda_tool("iverilog")
        self._vvp_path = find_eda_tool("vvp")

    def validate_prerequisites(self) -> bool:
        """Check that both iverilog and vvp are available."""
        return self._iverilog_path is not None and self._vvp_path is not None

    def run(
        self,
        *,
        testbench: str | Path,
        rtl_files: list[str | Path],
        cwd: str | Path | None = None,
        compile_timeout: int | None = None,
        sim_timeout: int | None = None,
    ) -> ToolResult:
        """Execute compile + simulate.

        Args:
            testbench: Path to testbench file.
            rtl_files: Paths to RTL source files.
            cwd: Working directory.
            compile_timeout: Compile timeout in seconds (default 60).
            sim_timeout: Simulation timeout in seconds (default 120).

        Returns:
            ToolResult with combined compile + sim output.
        """
        if not self._iverilog_path or not self._vvp_path:
            return ToolResult(
                status=ToolStatus.NOT_FOUND,
                errors=["iverilog or vvp not found"],
            )

        cwd_path = Path(cwd).resolve() if cwd else Path(".").resolve()
        env = get_eda_env()

        # Create temp .vvp file
        vvp_file = cwd_path / ".veriflow" / "sim_output.vvp"
        vvp_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: Compile
            compile_cmd = [
                self._iverilog_path,
                "-o", str(vvp_file),
                str(testbench),
                *[str(f) for f in rtl_files],
            ]

            compile_result = self._execute(
                command=compile_cmd,
                cwd=cwd_path,
                env=env,
                timeout=compile_timeout or 60,
            )

            if compile_result.status != ToolStatus.SUCCESS:
                return compile_result

            # Step 2: Simulate
            sim_cmd = [self._vvp_path, str(vvp_file)]
            sim_result = self._execute(
                command=sim_cmd,
                cwd=cwd_path,
                env=env,
                timeout=sim_timeout or 120,
            )

            # Combine outputs: compile warnings + sim output
            combined_stdout = (
                (compile_result.stdout or "") + "\n" + (sim_result.stdout or "")
            ).strip()

            return ToolResult(
                status=sim_result.status,
                return_code=sim_result.return_code,
                stdout=combined_stdout,
                stderr=sim_result.stderr,
                errors=sim_result.errors,
                duration_ms=compile_result.duration_ms + sim_result.duration_ms,
            )

        finally:
            # Cleanup temp .vvp
            if vvp_file.exists():
                vvp_file.unlink()

    def parse_sim_output(self, result: ToolResult) -> SimResult:
        """Parse simulation output for PASS/FAIL indicators.

        Args:
            result: Raw ToolResult from run().

        Returns:
            Parsed SimResult.
        """
        output = (result.stdout or "") + (result.stderr or "")

        pass_count = sum(
            1 for line in output.splitlines()
            if re.search(r'\bpass\b', line, re.IGNORECASE)
            and not re.search(r'\bfail\b', line, re.IGNORECASE)
        )
        fail_count = sum(
            1 for line in output.splitlines()
            if re.search(r'\bfail\b', line, re.IGNORECASE)
        )
        all_passed = "all tests passed" in output.lower()

        passed = result.return_code == 0 and (
            all_passed or ("PASS" in output and fail_count == 0)
        )

        return SimResult(
            passed=passed,
            pass_count=pass_count,
            fail_count=fail_count,
            all_passed=all_passed,
            output=output,
        )
