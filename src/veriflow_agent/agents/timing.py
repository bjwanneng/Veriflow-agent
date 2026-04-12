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

        # Step 4: Invoke LLM (with streaming if EventCollector available)
        # NOTE: Timing agent generates BOTH yaml and verilog, so we need the FULL
        # markdown output (with code fences) to extract both files correctly.
        # Using output_extractor would merge yaml+verilog content, breaking extraction.
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
        """Parse LLM output and write timing_model.yaml and testbench files."""
        import logging

        logger = logging.getLogger("veriflow")

        docs_dir = project_dir / "workspace" / "docs"
        tb_dir = project_dir / "workspace" / "tb"
        docs_dir.mkdir(parents=True, exist_ok=True)
        tb_dir.mkdir(parents=True, exist_ok=True)

        # ── Extract timing_model.yaml ─────────────────────────────────────────
        timing_path = docs_dir / "timing_model.yaml"
        yaml_content = None

        # Pattern 1: Standard ```yaml or ```yml blocks
        yaml_match = re.search(
            r'```(?:yaml|yml)\s*\n(.*?)\n```',
            llm_output,
            re.DOTALL | re.IGNORECASE
        )
        if yaml_match:
            yaml_content = yaml_match.group(1).strip()
            logger.debug("Extracted YAML from ```yaml block")

        # Pattern 2: Content starting with "design:" (inline YAML)
        if not yaml_content:
            yaml_inline = re.search(
                r'(design:\s*\w+.*?)(?=\n```|\n\n\n|```\w+|$)',
                llm_output,
                re.DOTALL | re.IGNORECASE
            )
            if yaml_inline:
                yaml_content = yaml_inline.group(1).strip()
                logger.debug("Extracted inline YAML")

        # Write YAML (always overwrite)
        if yaml_content and len(yaml_content) > 50:
            timing_path.write_text(yaml_content, encoding="utf-8")
            logger.info(f"Written timing_model.yaml ({len(yaml_content)} chars)")
        else:
            logger.warning(f"Could not extract valid YAML content")
            timing_path.write_text(f"# Error: Could not parse timing_model.yaml\n", encoding="utf-8")

        # ── Extract testbench files ────────────────────────────────────────────
        tb_matches = re.findall(
            r'```(?:verilog|v)\s*\n(.*?)\n```',
            llm_output,
            re.DOTALL | re.IGNORECASE
        )

        tb_written = 0
        for i, verilog_content in enumerate(tb_matches):
            content = verilog_content.strip()
            if not content:
                continue

            # Check if it looks like a testbench
            is_testbench = (
                'module' in content.lower() and
                ('initial' in content.lower() or
                 'tb_' in content.lower() or
                 '$display' in content or
                 '$finish' in content)
            )

            if not is_testbench:
                continue

            # Extract module name
            module_match = re.search(r'module\s+(\w+)', content, re.IGNORECASE)
            if module_match:
                module_name = module_match.group(1)
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '', module_name) or f"tb_{i}"
            else:
                safe_name = f"tb_generated_{i}"

            if not safe_name.startswith("tb_"):
                safe_name = f"tb_{safe_name}"

            tb_path = tb_dir / f"{safe_name}.v"
            tb_path.write_text(content, encoding="utf-8")
            tb_written += 1
            logger.info(f"Written testbench: {safe_name}.v")

        if tb_written == 0:
            logger.warning(f"No testbench found in LLM output")
