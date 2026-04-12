"""MicroArchAgent - Stage 1.5: Micro-Architecture Design.

Translates spec.json into a detailed micro-architecture document (micro_arch.md)
for every module, covering internal signals, pipelines, FSMs, control logic,
memory, and timing budgets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.agents.output_extractor import StreamingOutputExtractor


class MicroArchAgent(BaseAgent):
    """Stage 1.5: Micro-Architecture Design.

    Input: workspace/docs/spec.json, requirement.md
    Output: workspace/docs/micro_arch.md
    """

    def __init__(self):
        super().__init__(
            name="microarch",
            prompt_file="stage15_microarch.md",
            required_inputs=["workspace/docs/spec.json"],
            output_artifacts=["workspace/docs/micro_arch.md"],
            max_retries=1,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute micro-architecture design.

        Args:
            context: Must contain project_dir.

        Returns:
            AgentResult with micro_arch.md artifact path.
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

        # Step 3: Read requirement (optional reference)
        requirement_path = project_dir / "requirement.md"
        requirement_text = ""
        if requirement_path.exists():
            requirement_text = requirement_path.read_text(encoding="utf-8")

        # Step 4: Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "MODE": context.get("mode", "standard"),
            "STAGE_NAME": "stage15_microarch",
            "SPEC_JSON": spec_text[:12000],
            "REQUIREMENT": requirement_text[:4000],
        }

        # Step 5: Invoke LLM (with streaming if EventCollector available)
        # Use markdown_after_heading extractor to separate thinking from
        # the actual micro_arch.md content (which starts with a # heading).
        extractor = StreamingOutputExtractor(extract_mode="markdown_after_heading")
        try:
            prompt = self.render_prompt(llm_context)

            # Check if EventCollector is available for streaming
            event_collector = context.get("_event_collector")
            if event_collector:
                llm_output = self._consume_streaming(
                    context, prompt, event_collector,
                    output_extractor=extractor,
                )
            else:
                # Fall back to blocking call
                llm_output = self.call_llm(
                    context, prompt_override=prompt,
                    output_extractor=extractor,
                )
        except Exception as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"LLM invocation failed: {e}"],
            )

        # Step 6: Write output to file (always overwrite with clean extracted content)
        output_path = project_dir / "workspace" / "docs" / "micro_arch.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(llm_output, encoding="utf-8")

        # Step 7: Validate output
        content = output_path.read_text(encoding="utf-8")
        if len(content.strip()) < 50:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["micro_arch.md is too short (< 50 chars)"],
                artifacts=[str(output_path)],
            )

        return AgentResult(
            success=True,
            stage=self.name,
            artifacts=[str(output_path)],
            metrics={
                "doc_size_bytes": len(content),
                "module_sections": content.count("## "),
            },
            raw_output=llm_output[:2000],
        )
