"""Iverilog lint/syntax-check tool wrapper.

Wraps the Icarus Verilog compiler (iverilog) for RTL syntax validation.
Two modes:
- lint: `iverilog -Wall -tnull <files>`  — syntax-only, no output file
- compile: `iverilog -o <out> <files>`   — full compilation to .vvp
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veriflow_agent.tools.base import BaseTool, ToolResult, ToolStatus
from veriflow_agent.tools.eda_utils import find_eda_tool, get_eda_env


@dataclass
class LintResult:
    """Parsed result from iverilog lint/compile run.

    Attributes:
        passed: Whether lint/compilation succeeded (no errors).
        error_count: Number of error lines detected.
        warning_count: Number of warning lines detected.
        errors: List of error messages.
        warnings: List of warning messages.
    """

    passed: bool
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class IverilogTool(BaseTool):
    """Icarus Verilog wrapper for lint and compilation.

    Usage:
        tool = IverilogTool()
        if tool.validate_prerequisites():
            result = tool.run(mode="lint", files=["rtl/top.v", "rtl/module_a.v"])
            lint = tool.parse_lint_output(result)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(
            name="iverilog",
            config=config,
            executable=find_eda_tool("iverilog"),
        )

    def validate_prerequisites(self) -> bool:
        """Check that iverilog is available."""
        if self._executable and Path(self._executable).exists():
            return True
        return False

    def run(
        self,
        *,
        mode: str = "lint",
        files: list[str | Path],
        output_file: str | Path | None = None,
        cwd: str | Path | None = None,
        standard: str = "2005",
    ) -> ToolResult:
        """Execute iverilog.

        Args:
            mode: "lint" for syntax-only check (-Wall -tnull),
                  "compile" for full compilation (-g<standard> -o <out>).
            files: Verilog source files to check/compile.
            output_file: Output .vvp path (required for compile mode).
            cwd: Working directory. Defaults to current directory.
            standard: Verilog standard for compile mode (default "2005").

        Returns:
            ToolResult with stdout/stderr from iverilog.
        """
        cmd = [self.executable]

        if mode == "lint":
            # Enable all warnings and additional style checks
            cmd.extend(["-Wall", "-Wimplicit", "-Wportbind", "-Wselect-range", "-tnull"])
        elif mode == "compile":
            if not output_file:
                return ToolResult(
                    status=ToolStatus.FAILURE,
                    errors=["output_file is required for compile mode"],
                )
            cmd.extend([f"-g{standard}", "-Wall", "-o", str(output_file)])
        else:
            return ToolResult(
                status=ToolStatus.FAILURE,
                errors=[f"Unknown mode: {mode}. Use 'lint' or 'compile'."],
            )

        cmd.extend(str(f) for f in files)

        return self._execute(
            command=cmd,
            cwd=Path(cwd) if cwd else None,
            env=get_eda_env(),
            timeout=self.config.get("lint_timeout", 60),
        )

    def parse_lint_output(self, result: ToolResult) -> LintResult:
        """Parse iverilog output into structured LintResult.

        Args:
            result: Raw ToolResult from run().

        Returns:
            Parsed LintResult with error/warning counts.
        """
        output = (result.stdout or "") + (result.stderr or "")
        lines = output.splitlines()

        errors: list[str] = []
        warnings: list[str] = []

        for line in lines:
            line_lower = line.lower()
            if ": error:" in line_lower or ": fatal:" in line_lower:
                errors.append(line.strip())
            elif ": warning:" in line_lower:
                warnings.append(line.strip())

        passed = result.return_code == 0 and len(errors) == 0

        return LintResult(
            passed=passed,
            error_count=len(errors),
            warning_count=len(warnings),
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def filter_testbench_files(files: list[Path]) -> list[Path]:
        """Filter out testbench files (prefixed with tb_).

        Args:
            files: List of file paths.

        Returns:
            Files that are not testbenches.
        """
        return [f for f in files if not f.name.startswith("tb_")]
