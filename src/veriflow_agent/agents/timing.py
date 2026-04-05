"""TimingAgent - Stage 2: Virtual Timing Model.

Generates a timing model (YAML) and corresponding Verilog testbench
from the architecture specification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


class TimingAgent(BaseAgent):
    """Stage 2: Virtual Timing Model.

    Input: workspace/docs/spec.json
    Output: workspace/docs/timing_model.yaml, workspace/tb/tb_*.v
    """

    def __init__(self):
        super().__init__(
            name="timing",
            prompt_file="stage2_timing.md",
            required_inputs=["workspace/docs/spec.json"],
            output_artifacts=[
                "workspace/docs/timing_model.yaml",
                "workspace/tb/tb_*.v",
            ],
            max_retries=1,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute timing model generation.

        Args:
            context: Must contain project_dir.

        Returns:
            AgentResult with timing_model.yaml and testbench artifacts.
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

        # Step 2: Read spec
        spec_path = project_dir / "workspace" / "docs" / "spec.json"
        spec_text = spec_path.read_text(encoding="utf-8")

        # Step 3: Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "MODE": context.get("mode", "standard"),
            "STAGE_NAME": "stage2_timing",
            "SPEC_JSON": spec_text[:12000],
        }

        # Step 4: Invoke LLM
        try:
            prompt = self.render_prompt(llm_context)
            llm_output = self.call_llm(context, prompt_override=prompt)
        except Exception as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"LLM invocation failed: {e}"],
            )

        # Step 5: Post-validate artifacts
        all_found, found_files, missing_patterns = self.validate_outputs(context)

        if not all_found:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"Missing output artifacts: {missing_patterns}"],
                artifacts=found_files,
                raw_output=llm_output[:2000],
            )

        # Step 6: Compute metrics
        timing_path = project_dir / "workspace" / "docs" / "timing_model.yaml"
        timing_content = ""
        if timing_path.exists():
            timing_content = timing_path.read_text(encoding="utf-8")

        return AgentResult(
            success=True,
            stage=self.name,
            artifacts=found_files,
            metrics={
                "timing_model_size": len(timing_content),
                "scenario_count": timing_content.count("scenario:") if timing_content else 0,
                "testbench_count": len([f for f in found_files if "tb_" in f]),
            },
            raw_output=llm_output[:2000],
        )
