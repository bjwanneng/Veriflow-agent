"""LintAgent - Iverilog syntax check (no LLM).

Runs iverilog lint on RTL files and returns pass/fail.
This is a pure EDA check — no LLM invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.tools.lint import IverilogTool


class LintAgent(BaseAgent):
    """Iverilog lint check agent.

    Input: workspace/rtl/*.v
    Output: AgentResult with pass/fail and structured errors.
    """

    def __init__(self):
        super().__init__(
            name="lint",
            prompt_file="",
            required_inputs=["workspace/rtl/*.v"],
            output_artifacts=[],
            max_retries=1,
            llm_backend="openai",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Run iverilog lint on RTL files.

        Args:
            context: Must contain project_dir.

        Returns:
            AgentResult with lint pass/fail status.
        """
        project_dir = Path(context.get("project_dir", "."))
        rtl_dir = project_dir / "workspace" / "rtl"

        if not rtl_dir.exists():
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"RTL directory not found: {rtl_dir}"],
            )

        rtl_files = list(rtl_dir.glob("*.v"))
        non_tb = IverilogTool.filter_testbench_files(rtl_files)

        if not non_tb:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No non-testbench RTL files found in workspace/rtl/"],
            )

        lint_tool = IverilogTool()
        if not lint_tool.validate_prerequisites():
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["iverilog not found in PATH"],
            )

        lint_result_raw = lint_tool.run(
            mode="lint",
            files=non_tb,
            cwd=project_dir,
        )
        parsed = lint_tool.parse_lint_output(lint_result_raw)

        errors = parsed.errors if not parsed.passed else []
        warnings = parsed.warnings

        return AgentResult(
            success=parsed.passed,
            stage=self.name,
            errors=errors,
            warnings=warnings,
            metrics={
                "error_count": parsed.error_count,
                "warning_count": parsed.warning_count,
                "files_checked": len(non_tb),
            },
            raw_output=(lint_result_raw.stdout or "") + (lint_result_raw.stderr or ""),
        )
