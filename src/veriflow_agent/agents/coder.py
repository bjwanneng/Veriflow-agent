"""CoderAgent - Stage 3: RTL Code Generation.

Generates complete synthesizable Verilog RTL code for all modules
from the architecture specification. Supports parallel per-module generation.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


class CoderAgent(BaseAgent):
    """Stage 3: RTL Code Generation.

    Input: workspace/docs/spec.json, workspace/docs/micro_arch.md
    Output: workspace/rtl/*.v (one file per module)
    """

    def __init__(self):
        super().__init__(
            name="coder",
            prompt_file="stage3_module.md",
            required_inputs=["workspace/docs/spec.json"],
            output_artifacts=["workspace/rtl/*.v"],
            max_retries=1,
            llm_backend="openai",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute RTL code generation.

        Generates modules in two phases:
        1. Leaf modules (non-top) in parallel
        2. Top module(s) serially (so they see all peer interfaces)

        Args:
            context: Must contain project_dir.

        Returns:
            AgentResult with generated RTL file paths.
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

        # Step 2: Parse spec to get module list
        spec_path = project_dir / "workspace" / "docs" / "spec.json"
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"Failed to parse spec.json: {e}"],
            )

        modules = spec.get("modules", [])
        if not modules:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No modules found in spec.json"],
            )

        # Step 3: Read micro_arch if available
        microarch_path = project_dir / "workspace" / "docs" / "micro_arch.md"
        microarch_text = ""
        if microarch_path.exists():
            microarch_text = microarch_path.read_text(encoding="utf-8")

        # Step 4: Build peer interface summary
        peer_summary = self._build_peer_summary(modules)

        # Step 5: Split into leaf and top modules
        leaf_modules = [m for m in modules if m.get("module_type") != "top"]
        top_modules = [m for m in modules if m.get("module_type") == "top"]

        # Ensure output directory exists
        rtl_dir = project_dir / "workspace" / "rtl"
        rtl_dir.mkdir(parents=True, exist_ok=True)

        # Step 6: Phase 1 - Generate leaf modules in parallel
        # Concurrency is capped to avoid rate-limit errors with API backends.
        # Override with VERIFLOW_CODER_MAX_WORKERS (set to 1 for sequential).
        generated_files: list[str] = []
        errors: list[str] = []
        _cap = int(os.environ.get("VERIFLOW_CODER_MAX_WORKERS", 4))
        max_workers = min(len(leaf_modules), _cap) if leaf_modules else 1

        if leaf_modules:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        self._generate_module,
                        project_dir=project_dir,
                        module=mod,
                        spec=spec,
                        microarch_text=microarch_text,
                        peer_summary=peer_summary,
                        context=context,
                    ): mod
                    for mod in leaf_modules
                }

                for future in as_completed(futures):
                    mod = futures[future]
                    try:
                        result = future.result()
                        if result.success:
                            generated_files.extend(result.artifacts)
                        else:
                            errors.extend(result.errors)
                    except Exception as e:
                        errors.append(f"Module {mod.get('module_name', '?')} failed: {e}")

        if errors and not generated_files:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=errors,
            )

        # Step 7: Phase 2 - Generate top modules serially
        for mod in top_modules:
            result = self._generate_module(
                project_dir=project_dir,
                module=mod,
                spec=spec,
                microarch_text=microarch_text,
                peer_summary=peer_summary,
                context=context,
            )
            if result.success:
                generated_files.extend(result.artifacts)
            else:
                errors.extend(result.errors)

        # Step 8: Final validation
        # Partial success: some modules generated but others failed.
        # Treat as failure so the pipeline doesn't silently proceed with
        # an incomplete RTL set, but surface skipped modules as warnings.
        success = len(generated_files) > 0 and len(errors) == 0
        partial = len(generated_files) > 0 and len(errors) > 0
        return AgentResult(
            success=success,
            stage=self.name,
            artifacts=generated_files,
            errors=errors if not success else [],
            warnings=(
                [f"Partial generation: {len(errors)} module(s) failed — "
                 f"{len(generated_files)}/{len(modules)} generated. "
                 f"Failures: {'; '.join(errors)}"]
                if partial else []
            ),
            metadata={"partial_generation": partial},
            metrics={
                "modules_generated": len(generated_files),
                "modules_total": len(modules),
                "leaf_count": len(leaf_modules),
                "top_count": len(top_modules),
            },
        )

    @staticmethod
    def _build_peer_summary(modules: list[dict]) -> str:
        """Build a text summary of all module ports for cross-reference."""
        lines: list[str] = []
        for mod in modules:
            name = mod.get("module_name", "unknown")
            ports = mod.get("ports", [])
            port_strs = []
            for p in ports:
                direction = p.get("direction", "?")
                width = max(1, int(p.get("width", 1)))
                pname = p.get("name", "?")
                port_strs.append(f"{direction} [{width-1}:0] {pname}" if width > 1 else f"{direction} {pname}")
            lines.append(f"module {name}({', '.join(port_strs)});")
        return "\n".join(lines)

    @staticmethod
    def _extract_verilog(text: str) -> str:
        """Extract Verilog code from LLM output.

        Looks for ```verilog ... ``` or ``` ... ``` blocks.
        Falls back to returning the whole text if no fences found.
        """
        import re

        # Try ```verilog ... ``` first
        match = re.search(r"```(?:verilog|v)?\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # If the text itself looks like Verilog, return as-is
        if "module " in text and "endmodule" in text:
            # Extract from first module to last endmodule
            start = text.find("module ")
            end = text.rfind("endmodule") + len("endmodule")
            return text[start:end].strip()

        return text.strip()

    def _generate_module(
        self,
        project_dir: Path,
        module: dict,
        spec: dict,
        microarch_text: str,
        peer_summary: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate RTL for a single module via LLM."""
        module_name = module.get("module_name", "unknown")
        module_spec = json.dumps(module, indent=2)

        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "MODE": context.get("mode", "standard"),
            "STAGE_NAME": f"stage3_{module_name}",
            "MODULE_NAME": module_name,
            "MODULE_SPEC": module_spec[:8000],
            "MICRO_ARCH": microarch_text[:6000],
            "PEER_INTERFACES": peer_summary[:4000],
            "USER_FEEDBACK": context.get("user_feedback", ""),
            "EXPERIENCE_HINT": context.get("experience_hint", ""),
            "SUPERVISOR_HINT": context.get("supervisor_hint", ""),
        }

        # Check if EventCollector is available for streaming
        event_collector = context.get("_event_collector")

        try:
            prompt = self.render_prompt(llm_context)

            if event_collector:
                llm_output = self._consume_streaming(context, prompt, event_collector)
            else:
                # Fall back to blocking call
                llm_output = self.call_llm(context, prompt_override=prompt)

        except Exception as e:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"LLM invocation failed for {module_name}: {e}"],
            )

        # Extract Verilog from LLM output and write to file
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '', module_name) or "unnamed"
        output_path = project_dir / "workspace" / "rtl" / f"{safe_name}.v"
        verilog_code = self._extract_verilog(llm_output)

        if verilog_code:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(verilog_code, encoding="utf-8")
            return AgentResult(
                success=True,
                stage=self.name,
                artifacts=[str(output_path)],
                metrics={"size_bytes": len(verilog_code)},
                raw_output=llm_output[:1000],
            )

        # Fallback: check if file was written by LLM
        if output_path.exists():
            content = output_path.read_text(encoding="utf-8")
            return AgentResult(
                success=True,
                stage=self.name,
                artifacts=[str(output_path)],
                metrics={"size_bytes": len(content)},
                raw_output=llm_output[:1000],
            )

        return AgentResult(
            success=False,
            stage=self.name,
            errors=[f"Module {module_name}: output file not created"],
            raw_output=llm_output[:1000],
        )
