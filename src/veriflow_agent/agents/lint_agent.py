"""LintAgent - Iverilog syntax check (no LLM).

Runs iverilog lint on RTL files and returns pass/fail.
This is a pure EDA check — no LLM invocation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.tools.lint import IverilogTool

logger = logging.getLogger("veriflow")


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
            llm_backend="claude_cli",
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
            # Build helpful error message with diagnostics
            error_msg = self._build_not_found_message()
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[error_msg],
            )

        lint_result_raw = lint_tool.run(
            mode="lint",
            files=non_tb,
            cwd=project_dir,
        )
        parsed = lint_tool.parse_lint_output(lint_result_raw)

        errors = parsed.errors if not parsed.passed else []
        warnings = parsed.warnings

        # Build raw output from stderr/stdout
        raw_output = ""
        if lint_result_raw.stderr:
            raw_output = lint_result_raw.stderr
        if lint_result_raw.stdout:
            raw_output += "\n" + lint_result_raw.stdout
        raw_output = raw_output.strip()

        # Write log file for observability
        logs_dir = project_dir / "workspace" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        import time as _time
        log_path = logs_dir / f"lint_{int(_time.time())}.log"
        log_content = (
            f"=== Lint Check ===\n"
            f"Files checked: {[f.name for f in non_tb]}\n"
            f"Passed: {parsed.passed}\n"
            f"Errors: {parsed.error_count}\n"
            f"Warnings: {parsed.warning_count}\n"
            f"\n=== iverilog output ===\n"
            f"{raw_output}\n"
        )
        try:
            log_path.write_text(log_content, encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write lint log: %s", e)

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
            raw_output=raw_output,
            artifacts=[str(log_path)] if log_path.exists() else [],
        )

    @staticmethod
    def _build_not_found_message() -> str:
        """Build a helpful error message when iverilog is not found."""
        try:
            from veriflow_agent.tools.eda_utils import find_eda_tool_diagnostics
            diag = find_eda_tool_diagnostics("iverilog")
        except Exception:
            diag = {"searched": [], "not_found": [], "env_vars": []}

        lines = ["iverilog not found. Searched:"]
        for entry in diag.get("not_found", []):
            lines.append(f"  - {entry}")
        for entry in diag.get("searched", []):
            lines.append(f"  - {entry} (not found)")
        for entry in diag.get("env_vars", []):
            lines.append(f"  - {entry}")

        lines.append("")
        lines.append("Install options:")
        lines.append("  1. Windows: download from https://bleyer.org/icarus/")
        lines.append("  2. All platforms: https://github.com/YosysHQ/oss-cad-suite-build")
        lines.append("  3. Or set iverilog_path in ~/.veriflow/gui_config.json")
        lines.append("  4. Or set IVERILOG_HOME environment variable")

        return "\n".join(lines)
