"""SimAgent - Simulation check (no LLM).

Runs iverilog compile + vvp simulation on all testbenches.
Returns pass/fail with combined error logs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.tools.simulate import VvpTool


class SimAgent(BaseAgent):
    """Simulation check agent.

    Input: workspace/rtl/*.v, workspace/tb/tb_*.v
    Output: AgentResult with pass/fail and simulation logs.
    """

    def __init__(self):
        super().__init__(
            name="sim",
            prompt_file="",
            required_inputs=["workspace/rtl/*.v"],
            output_artifacts=[],
            max_retries=1,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Run simulation on all testbenches.

        Args:
            context: Must contain project_dir.

        Returns:
            AgentResult with sim pass/fail status.
        """
        project_dir = Path(context.get("project_dir", "."))
        rtl_dir = project_dir / "workspace" / "rtl"
        tb_dir = project_dir / "workspace" / "tb"

        rtl_files = list(rtl_dir.glob("*.v")) if rtl_dir.exists() else []
        tb_files = list(tb_dir.glob("tb_*.v")) if tb_dir.exists() else []

        if not rtl_files:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No RTL files found in workspace/rtl/"],
            )

        if not tb_files:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No testbench files (tb_*.v) found in workspace/tb/"],
            )

        sim_tool = VvpTool()
        if not sim_tool.validate_prerequisites():
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["iverilog/vvp not found in PATH"],
            )

        # Run all testbenches, collect failures
        failed_logs: list[str] = []
        tb_results: list[dict[str, Any]] = []

        for tb in tb_files:
            sim_result = sim_tool.run(
                testbench=tb,
                rtl_files=rtl_files,
                cwd=project_dir,
            )
            parsed = sim_tool.parse_sim_output(sim_result)
            tb_results.append({
                "testbench": tb.name,
                "passed": parsed.passed,
                "pass_count": parsed.pass_count,
                "fail_count": parsed.fail_count,
            })
            if not parsed.passed:
                failed_logs.append(parsed.output[:5000])

        all_passed = len(failed_logs) == 0
        errors = failed_logs if not all_passed else []

        return AgentResult(
            success=all_passed,
            stage=self.name,
            errors=errors,
            metrics={
                "total_testbenches": len(tb_files),
                "passed_testbenches": len(tb_files) - len(failed_logs),
                "failed_testbenches": len(failed_logs),
                "results": tb_results,
            },
        )
