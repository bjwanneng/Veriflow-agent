"""ArchitectAgent - Stage 1: Interactive Architecture Analysis.

Conducts interactive Q&A with the user to produce spec.json.
In the agent architecture, this runs as a headless LLM call that reads
requirement.md and produces the complete architecture specification.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


class ArchitectAgent(BaseAgent):
    """Stage 1: Interactive Architecture Analysis.

    Reads requirement.md and project_config.json, invokes the LLM to
    conduct architecture analysis, and produces spec.json with module
    hierarchy, ports, FSMs, KPI targets, and connectivity.

    Input: requirement.md, project_config.json
    Output: workspace/docs/spec.json
    """

    def __init__(self):
        super().__init__(
            name="architect",
            prompt_file="stage1_architect.md",
            required_inputs=["requirement.md"],
            output_artifacts=["workspace/docs/spec.json"],
            max_retries=1,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute architecture analysis.

        Args:
            context: Must contain:
                - project_dir: Path to project root
                - mode: Pipeline mode (quick/standard/enterprise)
                - frequency_mhz: Target frequency override (optional)

        Returns:
            AgentResult with spec.json artifact path and metrics.
        """
        project_dir = Path(context.get("project_dir", "."))
        mode = context.get("mode", "standard")

        # Step 1: Validate inputs
        valid, missing = self.validate_inputs(context)
        if not valid:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"Missing required inputs: {missing}"],
            )

        # Step 2: Read requirement and config
        requirement_path = project_dir / "requirement.md"
        config_path = project_dir / ".veriflow" / "project_config.json"

        requirement_text = requirement_path.read_text(encoding="utf-8")

        config_text = ""
        if config_path.exists():
            config_text = config_path.read_text(encoding="utf-8")

        # Step 3: Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "MODE": mode,
            "STAGE_NAME": "stage1_architect",
            "REQUIREMENT": requirement_text[:8000],
            "PROJECT_CONFIG": config_text[:2000] if config_text else "{}",
            "FREQUENCY_MHZ": str(context.get("frequency_mhz", "100")),
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

        # Step 5: Extract and validate spec.json from output
        spec_path = project_dir / "workspace" / "docs" / "spec.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)

        spec_data = self._extract_spec_json(llm_output)
        if spec_data is None:
            # Fallback: check if LLM wrote the file directly
            if spec_path.exists():
                try:
                    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    return AgentResult(
                        success=False,
                        stage=self.name,
                        errors=["Failed to parse spec.json (both from LLM output and file)"],
                        raw_output=llm_output,
                    )
            else:
                return AgentResult(
                    success=False,
                    stage=self.name,
                    errors=["LLM did not produce valid spec.json"],
                    raw_output=llm_output[:2000],
                )

        # Step 6: Validate spec structure
        validation_errors = self._validate_spec(spec_data)
        if validation_errors:
            # Save anyway for debugging, but report failure
            spec_path.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")
            return AgentResult(
                success=False,
                stage=self.name,
                errors=validation_errors,
                artifacts=[str(spec_path)],
                raw_output=llm_output[:2000],
            )

        # Step 7: Write spec.json
        spec_path.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")

        # Step 8: Compute metrics
        modules = spec_data.get("modules", [])
        checksum = hashlib.md5(
            spec_path.read_bytes()
        ).hexdigest()[:8]

        return AgentResult(
            success=True,
            stage=self.name,
            artifacts=[str(spec_path)],
            metrics={
                "module_count": len(modules),
                "checksum": checksum,
                "design_name": spec_data.get("design_name", "unknown"),
                "frequency_mhz": spec_data.get("target_kpis", {}).get("frequency_mhz", 0),
            },
            raw_output=llm_output[:2000],
        )

    def _extract_spec_json(self, llm_output: str) -> dict | None:
        """Try to extract a JSON spec from LLM output.

        Looks for JSON blocks in markdown code fences or raw JSON.
        """
        # Try markdown code fence
        import re
        json_match = re.search(
            r"```(?:json)?\s*\n([\s\S]*?)\n```", llm_output
        )
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding raw JSON object
        brace_start = llm_output.find("{")
        brace_end = llm_output.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(llm_output[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _validate_spec(self, spec: dict) -> list[str]:
        """Validate spec.json structure. Returns list of error messages."""
        errors: list[str] = []

        if "design_name" not in spec:
            errors.append("spec.json missing 'design_name'")

        modules = spec.get("modules", [])
        if not modules:
            errors.append("spec.json has no modules")
            return errors

        for i, mod in enumerate(modules):
            if "module_name" not in mod:
                errors.append(f"Module {i} missing 'module_name'")
            if "ports" not in mod:
                errors.append(f"Module {i} missing 'ports'")

        kpis = spec.get("target_kpis", {})
        if not kpis:
            errors.append("spec.json missing 'target_kpis'")

        return errors
