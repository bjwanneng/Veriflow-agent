"""SynthAgent - Stage 5: Synthesis + KPI Comparison.

Runs Yosys synthesis on generated RTL and compares results against
target KPIs from spec.json. This is a purely EDA stage — no LLM needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.tools.synth import YosysTool


class SynthAgent(BaseAgent):
    """Stage 5: Synthesis + KPI Comparison.

    Input: workspace/rtl/*.v, workspace/docs/spec.json
    Output: workspace/docs/synth_report.json
    """

    def __init__(self):
        super().__init__(
            name="synth",
            prompt_file="",  # No LLM prompt needed
            required_inputs=["workspace/docs/spec.json"],
            output_artifacts=["workspace/docs/synth_report.json"],
            max_retries=1,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute synthesis.

        Args:
            context: Must contain project_dir.

        Returns:
            AgentResult with synthesis metrics.
        """
        project_dir = Path(context.get("project_dir", "."))

        # Step 1: Validate inputs
        valid, missing = self.validate_inputs(context)
        if not valid:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"Missing required inputs: {missing}"],
            )

        # Step 2: Read spec for top module and KPIs
        spec_path = project_dir / "workspace" / "docs" / "spec.json"
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"Failed to parse spec.json: {e}"],
            )

        # Find top module
        top_module = ""
        for mod in spec.get("modules", []):
            if mod.get("module_type") == "top":
                top_module = mod.get("module_name", "")
                break

        if not top_module:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No top module found in spec.json"],
            )

        target_kpis = spec.get("target_kpis", {})

        # Step 3: Discover RTL files
        rtl_dir = project_dir / "workspace" / "rtl"
        rtl_files = [
            str(f) for f in rtl_dir.glob("*.v")
            if not f.name.startswith("tb_")
        ]

        if not rtl_files:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No RTL files found in workspace/rtl/"],
            )

        # Step 4: Run Yosys
        tool = YosysTool()
        if not tool.validate_prerequisites():
            # Graceful skip — yosys not installed
            return AgentResult(
                success=True,
                stage=self.name,
                warnings=["Yosys not found, skipping synthesis"],
                metrics={"skipped": True},
            )

        tool_result = tool.run(
            rtl_files=rtl_files,
            top_module=top_module,
            cwd=project_dir,
        )

        # Step 5: Parse output
        synth_result = tool.parse_synth_output(tool_result, top_module=top_module)

        # Step 6: Build report
        report = {
            "top_module": top_module,
            "success": synth_result.success,
            "num_cells": synth_result.num_cells,
            "num_wires": synth_result.num_wires,
            "target_kpis": target_kpis,
            "area_utilization": {},
            "raw_output": synth_result.raw_stats[:2000],
        }

        # Compute area utilization
        max_cells = target_kpis.get("max_cells", 0)
        if max_cells > 0 and synth_result.num_cells > 0:
            report["area_utilization"] = {
                "actual_cells": synth_result.num_cells,
                "max_cells": max_cells,
                "utilization_pct": round(synth_result.num_cells / max_cells * 100, 1),
                "status": "OVER" if synth_result.num_cells > max_cells else "OK",
            }

        # Step 7: Save report
        report_path = project_dir / "workspace" / "docs" / "synth_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        return AgentResult(
            success=synth_result.success,
            stage=self.name,
            artifacts=[str(report_path)],
            metrics={
                "num_cells": synth_result.num_cells,
                "num_wires": synth_result.num_wires,
                "area_status": report.get("area_utilization", {}).get("status", "UNKNOWN"),
            },
            errors=tool_result.errors if not synth_result.success else [],
            warnings=tool_result.warnings,
        )
