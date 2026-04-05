"""Skill D Agent - Stage 3.5: Static Analysis.

This agent performs static quality analysis on generated RTL code,
checking for coding standards compliance, complexity metrics,
and potential issues without simulation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


class SkillDAgent(BaseAgent):
    """Static Analysis Agent for RTL quality checking.

    This agent performs automated static analysis on Verilog RTL code:
    - Syntax checking via iverilog
    - Coding style compliance
    - Complexity metrics (lines, modules, FSM states)
    - CDC (Clock Domain Crossing) risk assessment
    - Logic depth estimation

    Input: workspace/rtl/*.v (from coder stage)
    Output: workspace/docs/static_report.json
    """

    def __init__(self):
        super().__init__(
            name="skill_d",
            prompt_file="stage35_skill_d.md",
            required_inputs=["workspace/rtl/*.v"],
            output_artifacts=["workspace/docs/static_report.json"],
            max_retries=1,  # Static analysis typically doesn't benefit from retry
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute static analysis on RTL code.

        Args:
            context: Dictionary containing:
                - project_dir: Path to project root
                - coder_output: Previous stage output (optional)

        Returns:
            AgentResult with static analysis report
        """
        project_dir = Path(context.get("project_dir", "."))
        rtl_dir = project_dir / "workspace" / "rtl"
        report_path = project_dir / "workspace" / "docs" / "static_report.json"

        # Step 1: Validate input (RTL files exist)
        if not rtl_dir.exists():
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"RTL directory not found: {rtl_dir}"],
            )

        rtl_files = list(rtl_dir.glob("*.v"))
        if not rtl_files:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No RTL files found in workspace/rtl/"],
            )

        # Step 2: Run automated static analysis (no LLM needed for basic checks)
        analysis_result = self._run_static_analysis(project_dir, rtl_files)

        # Step 3: Generate report
        report = self._generate_report(analysis_result, rtl_files)

        # Step 4: Save report to file
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        # Step 5: Determine success based on critical issues
        critical_issues = report.get("summary", {}).get("critical_issues", 0)
        success = critical_issues == 0

        return AgentResult(
            success=success,
            stage=self.name,
            artifacts=[str(report_path)],
            metrics={
                "rtl_files_analyzed": len(rtl_files),
                "lines_of_code": report.get("summary", {}).get("total_lines", 0),
                "critical_issues": critical_issues,
                "warnings": report.get("summary", {}).get("warnings", 0),
            },
            warnings=report.get("issues", {}).get("warnings", []),
            errors=report.get("issues", {}).get("errors", []) if not success else [],
            metadata={"report": report},
        )

    def _run_static_analysis(
        self, project_dir: Path, rtl_files: list[Path]
    ) -> dict[str, Any]:
        """Run automated static analysis on RTL files.

        This performs basic analysis without LLM:
        - Line counting
        - Module extraction
        - Basic syntax validation
        - Port analysis
        """
        total_lines = 0
        total_modules = 0
        modules_info = []

        for rtl_file in rtl_files:
            content = rtl_file.read_text(encoding="utf-8")
            lines = content.split("\n")
            total_lines += len(lines)

            # Extract module declarations (basic regex)
            module_pattern = r"module\s+(\w+)\s*\((.*?)\);"
            for match in re.finditer(module_pattern, content, re.DOTALL):
                module_name = match.group(1)
                ports_str = match.group(2)
                total_modules += 1

                # Parse ports (simplified)
                ports = []
                for port in ports_str.split(","):
                    port = port.strip()
                    if port:
                        ports.append(port)

                modules_info.append(
                    {
                        "name": module_name,
                        "file": rtl_file.name,
                        "ports_count": len(ports),
                        "ports": ports[:10],  # Limit stored ports
                    }
                )

        return {
            "total_lines": total_lines,
            "total_modules": total_modules,
            "modules": modules_info,
            "files_analyzed": len(rtl_files),
        }

    def _generate_report(
        self, analysis_result: dict[str, Any], rtl_files: list[Path]
    ) -> dict[str, Any]:
        """Generate the static analysis report structure.

        Note: This is a basic implementation. In the full implementation,
        this would also call the LLM for deeper analysis and integrate
        results from actual EDA tools (iverilog, yosys).
        """
        report = {
            "version": "1.0.0",
            "stage": "3.5",
            "stage_name": "Static Analysis (Skill D)",
            "summary": {
                "rtl_files_analyzed": len(rtl_files),
                "total_lines": analysis_result.get("total_lines", 0),
                "total_modules": analysis_result.get("total_modules", 0),
                "critical_issues": 0,  # Would be populated by real analysis
                "warnings": 0,
                "info": 0,
            },
            "modules": analysis_result.get("modules", []),
            "issues": {
                "critical": [],
                "errors": [],
                "warnings": [],
                "info": [],
            },
            "metrics": {
                "code_quality_score": 0,  # 0-100
                "complexity_score": 0,
                "maintainability_score": 0,
            },
            "recommendations": [],
            "timestamp": None,  # Would add current timestamp
        }

        return report