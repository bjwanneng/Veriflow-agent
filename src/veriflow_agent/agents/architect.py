"""ArchitectAgent - Stage 1: Architecture Analysis.

Reads requirement.md and produces spec.json via LLM.
In pipeline mode, this is a single-shot call that directly generates the spec.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.agents.output_extractor import StreamingOutputExtractor
from veriflow_agent.context.scanner import scan_context

logger = logging.getLogger("veriflow.agent")


class ArchitectAgent(BaseAgent):
    """Stage 1: Interactive Architecture Analysis.

    Reads requirement.md and project_config.json, invokes the LLM to
    conduct architecture analysis, and produces spec.json with module
    hierarchy, ports, FSMs, KPI targets, and connectivity.

    Input: requirement.md, project_config.json
    Output: workspace/docs/spec.json
    """

    def __init__(self, prompt_file: str | None = None):
        super().__init__(
            name="architect",
            prompt_file=prompt_file or "stage1_architect.md",
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

        # Step 3: Use quick mode prompt if applicable
        prompt_file = self.prompt_file
        if mode == "quick" and prompt_file == "stage1_architect.md":
            prompt_file = "stage1_architect_quick.md"

        # Step 4: Scan context/ directory for reference documents
        context_bundle = scan_context(project_dir)
        context_docs = context_bundle.to_prompt_section()
        if context_docs:
            logger.info(
                "[%s] Injected %d context documents (%d chars) into prompt",
                self.name, len(context_bundle.files), context_bundle.total_chars,
            )

        # Step 5: Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "MODE": mode,
            "STAGE_NAME": "stage1_architect",
            "REQUIREMENT": requirement_text[:8000],
            "PROJECT_CONFIG": config_text[:2000] if config_text else "{}",
            "FREQUENCY_MHZ": str(context.get("frequency_mhz", "100")),
            "CONTEXT_DOCS": context_docs,
            "llm_max_tokens": 16384,  # Architect needs larger output for spec.json
        }

        # Inject retry feedback if present (from architect_retry node)
        retry_feedback = context.get("architect_retry_feedback", "")
        if retry_feedback:
            llm_context["RETRY_FEEDBACK"] = (
                f"Your previous output was invalid. Errors:\n{retry_feedback}\n\n"
                "Please fix these issues and output a valid spec.json."
            )

        # Step 6: Invoke LLM (resolve prompt_file directly without mutating self)
        llm_output = ""
        try:
            prompt_path = self._resolve_prompt_path(prompt_file)
            prompt_content = prompt_path.read_text(encoding="utf-8")
            for key, value in llm_context.items():
                placeholder = "{{" + key + "}}"
                prompt_content = prompt_content.replace(placeholder, str(value))

            # Use streaming if EventCollector is available for observability
            event_collector = context.get("_event_collector")
            extractor = StreamingOutputExtractor(
                fence_types=["json"],
                extract_mode="code_fences",
            )
            if event_collector:
                llm_output = self._consume_streaming(
                    context, prompt_content, event_collector,
                    output_extractor=extractor,
                )
            else:
                # Fall back to blocking call
                llm_output = self.call_llm(
                    context, prompt_override=prompt_content,
                    output_extractor=extractor,
                )
        except Exception as e:
            # Save error info before returning
            logs_dir = project_dir / "workspace" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            error_path = logs_dir / "architect_error.txt"
            import traceback as _tb
            error_content = f"LLM invocation failed: {e}\n\nTraceback:\n{_tb.format_exc()}"
            if llm_output:
                error_content += f"\n\nPartial LLM output ({len(llm_output)} chars):\n{llm_output[:2000]}"
            error_path.write_text(error_content, encoding="utf-8")
            logger.error("[%s] LLM invocation failed, error saved to %s", self.name, error_path)
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"LLM invocation failed: {e}"],
            )

        # Step 7: Save raw LLM output for debugging
        logs_dir = project_dir / "workspace" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        raw_output_path = logs_dir / "architect_raw_output.txt"
        raw_output_path.write_text(llm_output, encoding="utf-8")
        logger.info("[%s] Saved raw LLM output to %s", self.name, raw_output_path)

        # Step 8: Extract and validate spec.json from output
        spec_path = project_dir / "workspace" / "docs" / "spec.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)

        spec_data = self._extract_spec_json(llm_output)
        if spec_data is None:
            # Log raw output for debugging
            logger.warning("[%s] JSON extraction failed. LLM output preview: %s",
                           self.name, llm_output[:500].replace('\n', '\\n'))

            # Save the raw output as a hint for debugging
            error_preview = llm_output[:300].replace('\n', ' ')

            # Fallback: check if LLM wrote the file directly
            if spec_path.exists():
                try:
                    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    return AgentResult(
                        success=False,
                        stage=self.name,
                        errors=[
                            f"JSON 解析失败: {e}",
                            f"原始输出预览: {error_preview}",
                            f"完整输出已保存到: {raw_output_path}",
                        ],
                        raw_output=llm_output,
                    )
            else:
                return AgentResult(
                    success=False,
                    stage=self.name,
                    errors=[
                        "LLM 未输出有效的 JSON 格式 spec.json",
                        f"原始输出预览: {error_preview}",
                        f"完整输出已保存到: {raw_output_path}",
                    ],
                    raw_output=llm_output[:2000],
                )

        # Step 9: Validate spec structure
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

        # Step 10: Write spec.json
        spec_path.write_text(json.dumps(spec_data, indent=2), encoding="utf-8")

        # Step 11: Compute metrics
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

        Attempts multiple strategies with increasing tolerance:
        1. Markdown code fence (```json ... ```)
        2. Greedy code fence (may contain extra text)
        3. Raw brace matching with brace-depth tracking
        4. Trailing-comma repair
        """
        import re

        # Strategy 1: Standard markdown code fence
        json_match = re.search(
            r"```(?:json)?\s*\n([\s\S]*?)\n```", llm_output
        )
        if json_match:
            candidate = json_match.group(1).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # Try repairing trailing commas
                repaired = self._repair_json(candidate)
                if repaired:
                    return repaired

        # Strategy 2: Greedy code fence (capture everything between first and last ```)
        json_match_greedy = re.search(
            r"```(?:json)?\s*\n([\s\S]+)\n```", llm_output
        )
        if json_match_greedy and json_match_greedy.group(1) != (json_match.group(1) if json_match else None):
            candidate = json_match_greedy.group(1).strip()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = self._repair_json(candidate)
                if repaired:
                    return repaired

        # Strategy 3: Find the largest balanced brace block
        brace_start = llm_output.find("{")
        if brace_start == -1:
            return None

        # Track brace depth to find the matching closing brace
        depth = 0
        in_string = False
        escape = False
        best_end = -1

        for i in range(brace_start, len(llm_output)):
            c = llm_output[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    best_end = i
                    break

        if best_end > brace_start:
            candidate = llm_output[brace_start:best_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = self._repair_json(candidate)
                if repaired:
                    return repaired

        # Strategy 4: Fallback — rfind "}" (may over-capture)
        brace_end = llm_output.rfind("}")
        if brace_end > brace_start:
            candidate = llm_output[brace_start:brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = self._repair_json(candidate)
                if repaired:
                    return repaired

        return None

    @staticmethod
    def _repair_json(text: str) -> dict | None:
        """Attempt to repair common JSON issues (trailing commas, comments)."""
        import re as _re
        # Remove trailing commas before } or ]
        cleaned = _re.sub(r',\s*([}\]])', r'\1', text)
        # Remove single-line comments
        cleaned = _re.sub(r'//[^\n]*', '', cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
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
