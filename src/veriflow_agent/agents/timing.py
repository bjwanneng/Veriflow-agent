"""TimingAgent - Stage 2: Virtual Timing Model.

Generates a timing model (YAML) and corresponding Verilog testbench
from the architecture specification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import re

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
            llm_backend="openai",
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

        # Step 4: Invoke LLM (with streaming if EventCollector available)
        try:
            prompt = self.render_prompt(llm_context)

            # Check if EventCollector is available for streaming
            event_collector = context.get("_event_collector")
            if event_collector:
                llm_output = self._consume_streaming(context, prompt, event_collector)
            else:
                # Fall back to blocking call
                llm_output = self.call_llm(context, prompt_override=prompt)
        except Exception as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"LLM invocation failed: {e}"],
            )

        # Step 5: Parse LLM output and write files if not already written
        self._write_timing_artifacts(project_dir, llm_output)

        # Step 6: Post-validate artifacts
        all_found, found_files, missing_patterns = self.validate_outputs(context)

        if not all_found:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"Missing output artifacts: {missing_patterns}"],
                artifacts=found_files,
                raw_output=llm_output[:2000],
            )

        # Step 7: Compute metrics
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

    def _write_timing_artifacts(self, project_dir: Path, llm_output: str) -> None:
        """Parse LLM output and write timing_model.yaml and testbench files.

        If the files are not already written by the LLM, this method extracts
        the YAML and Verilog content from the LLM output and writes them.
        """
        import re

        docs_dir = project_dir / "workspace" / "docs"
        tb_dir = project_dir / "workspace" / "tb"
        docs_dir.mkdir(parents=True, exist_ok=True)
        tb_dir.mkdir(parents=True, exist_ok=True)

        # Check if timing_model.yaml already exists (LLM may have written it)
        timing_path = docs_dir / "timing_model.yaml"
        if not timing_path.exists():
            # Try to extract YAML content from LLM output
            # Look for YAML between ```yaml and ``` markers
            yaml_match = re.search(
                r'```(?:yaml|yml)?\s*\n(.*?)\n```',
                llm_output,
                re.DOTALL | re.IGNORECASE
            )
            if yaml_match:
                yaml_content = yaml_match.group(1).strip()
                timing_path.write_text(yaml_content, encoding="utf-8")

        # Check for testbench files in output
        # Look for Verilog testbench between ```verilog and ``` markers
        tb_matches = re.findall(
            r'```(?:verilog|v)?\s*\n(.*?)\n```',
            llm_output,
            re.DOTALL | re.IGNORECASE
        )

        for i, verilog_content in enumerate(tb_matches):
            content = verilog_content.strip()
            # Only save if it looks like a testbench (contains 'module' and 'testbench' keywords)
            if 'module' in content.lower() and ('test' in content.lower() or 'tb_' in content.lower()):
                # Try to extract module name
                module_match = re.search(r'module\s+(\w+)', content, re.IGNORECASE)
                if module_match:
                    module_name = module_match.group(1)
                    safe_name = re.sub(r'[^a-zA-Z0-9_]', '', module_name) or "unnamed"
                else:
                    safe_name = f"tb_generated_{i}"

                tb_path = tb_dir / f"{safe_name}.v"
                if not tb_path.exists():
                    tb_path.write_text(content, encoding="utf-8")
