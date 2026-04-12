"""CoderAgent - Stage 3: RTL Code Generation.

Generates complete synthesizable Verilog RTL code for all modules
from the architecture specification. Supports parallel per-module generation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

logger = logging.getLogger("veriflow")

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.agents.output_extractor import StreamingOutputExtractor


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
            llm_backend="claude_cli",
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

        Tries multiple strategies in order:
        1. Standard code fences: ```verilog ... ```
        2. Relaxed code fences: ``` ... ``` containing 'module'
        3. Raw module/endmodule extraction
        4. Fallback: return as-is if it looks like Verilog

        All strategies apply artifact cleanup as post-processing.
        """
        if not text or not text.strip():
            return ""

        # Pre-processing: normalize duplicate opening fences.
        # Some LLMs output ```verilog\n```verilog\n — remove the duplicates.
        cleaned = re.sub(r'^(```(?:verilog|v)?\s*\n)+', r'\1', text.lstrip(), count=1)

        # Strategy 1: Standard ```verilog or ```v fences
        match = re.search(r"```(?:verilog|v)\s*\n(.*?)```", cleaned, re.DOTALL)
        if match and match.group(1).strip():
            return CoderAgent._clean_verilog_artifacts(match.group(1).strip())

        # Strategy 2: Any code fence containing 'module'
        match = re.search(r"```\w*\s*\n(.*?)```", cleaned, re.DOTALL)
        if match and match.group(1).strip():
            content = match.group(1).strip()
            if "module " in content or "module\t" in content:
                return CoderAgent._clean_verilog_artifacts(content)

        # Strategy 3: Extract module/endmodule blocks
        # Use find() for the first endmodule to avoid picking up artifacts
        if "module " in cleaned and "endmodule" in cleaned:
            start = cleaned.find("module ")
            end = cleaned.find("endmodule", start) + len("endmodule")
            extracted = cleaned[start:end].strip()
            if extracted:
                return CoderAgent._clean_verilog_artifacts(extracted)

        # Strategy 4: Check if the text IS verilog (no fences, just code)
        text_stripped = cleaned.strip()
        verilog_indicators = ["module ", "endmodule", "always @", "assign ", "reg ", "wire "]
        indicator_count = sum(1 for ind in verilog_indicators if ind in text_stripped)
        if indicator_count >= 3 and "module " in text_stripped:
            start = text_stripped.find("module ")
            end = text_stripped.find("endmodule", start)
            if end >= 0:
                return CoderAgent._clean_verilog_artifacts(
                    text_stripped[start:end + len("endmodule")]
                )

        return ""

    @staticmethod
    def _clean_verilog_artifacts(code: str) -> str:
        """Remove markdown fence artifacts from extracted Verilog.

        LLMs sometimes produce trailing ``` markers or duplicate code
        after endmodule. This method strips those artifacts.
        """
        # 1. Remove any lines that are purely backtick fences
        code = re.sub(r'^```[`]*\s*$', '', code, flags=re.MULTILINE)
        # 2. Remove trailing lines that look like artifacts (bare 'end' or
        #    'endmodule' after the real content has ended)
        lines = code.split('\n')
        # Walk backwards and strip artifact lines
        while lines:
            last = lines[-1].strip()
            if not last:
                lines.pop()
            elif last.startswith('```'):
                lines.pop()
            elif last == 'end' or last == 'end\n':
                # Bare 'end' after endmodule is an artifact
                # But only if we already have endmodule
                if 'endmodule' in '\n'.join(lines):
                    lines.pop()
                else:
                    break
            else:
                break
        code = '\n'.join(lines)
        # 3. Final trim: if content after last endmodule is junk, cut it
        last_endmodule = code.rfind("endmodule")
        if last_endmodule >= 0:
            after = code[last_endmodule + len("endmodule"):].strip()
            if after:
                # Check if it's valid Verilog (comments or new module definitions)
                if not (after.startswith("//") or after.startswith("module ")):
                    code = code[:last_endmodule + len("endmodule")]
        return code.strip()

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
        extractor = StreamingOutputExtractor(
            fence_types=["verilog", "v"],
            extract_mode="code_fences",
        )

        try:
            prompt = self.render_prompt(llm_context)

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
                errors=[f"LLM invocation failed for {module_name}: {e}"],
            )

        # Save raw LLM output for debugging
        try:
            log_dir = project_dir / "workspace" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"coder_{module_name}_raw.txt"
            log_path.write_text(llm_output, encoding="utf-8")
            logger.debug(f"[coder] Saved raw output for {module_name}: {len(llm_output)} chars")
        except Exception:
            pass

        # Extract Verilog from LLM output and write to file
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '', module_name) or "unnamed"
        output_path = project_dir / "workspace" / "rtl" / f"{safe_name}.v"
        verilog_code = self._extract_verilog(llm_output)

        if not verilog_code and llm_output:
            # Extraction failed despite non-empty LLM output — self-retry once
            logger.warning(
                f"[coder] Verilog extraction failed for {module_name}: "
                f"llm_output={len(llm_output)} chars, "
                f"has_fences={'```' in llm_output}, "
                f"has_module={'module ' in llm_output}, "
                f"has_endmodule={'endmodule' in llm_output}"
            )
            verilog_code = self._self_retry_extraction(
                project_dir=project_dir,
                module_name=module_name,
                module_spec=module_spec,
                microarch_text=microarch_text,
                peer_summary=peer_summary,
                original_output=llm_output,
                context=context,
            )
            if verilog_code:
                logger.info(f"[coder] Self-retry succeeded for {module_name}")

        if verilog_code:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(verilog_code, encoding="utf-8")

            # Post-write validation: quick syntax sanity check
            warnings = self._quick_validate_verilog(verilog_code, module_name)
            if warnings:
                # Try to fix by re-cleaning artifacts
                cleaned = self._clean_verilog_artifacts(verilog_code)
                if cleaned != verilog_code and cleaned:
                    output_path.write_text(cleaned, encoding="utf-8")
                    logger.info(f"[coder] Cleaned artifacts in {safe_name}.v")
                    verilog_code = cleaned

            # If iverilog is available, do a quick lint check
            try:
                from veriflow_agent.tools.eda_utils import find_eda_tool
                iverilog_path = find_eda_tool("iverilog")
                if iverilog_path:
                    self._quick_lint_check(iverilog_path, output_path, module_name)
            except Exception:
                pass  # Non-critical: lint stage will catch real issues

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

    def _self_retry_extraction(
        self,
        project_dir: Path,
        module_name: str,
        module_spec: str,
        microarch_text: str,
        peer_summary: str,
        original_output: str,
        context: dict[str, Any],
    ) -> str:
        """Retry LLM generation once when Verilog extraction fails.

        Provides error feedback to the LLM about the extraction failure
        so it can produce properly formatted output on the second attempt.

        Returns:
            Extracted Verilog code, or empty string if retry also fails.
        """
        # Build diagnostic feedback about what went wrong
        has_fences = "```" in original_output
        has_module = "module " in original_output
        has_endmodule = "endmodule" in original_output
        diag_parts = [
            f"Output length: {len(original_output)} chars",
            f"Has code fences (```): {has_fences}",
            f"Has 'module' keyword: {has_module}",
            f"Has 'endmodule' keyword: {has_endmodule}",
        ]
        if has_fences:
            fence_count = original_output.count("```")
            diag_parts.append(f"Fence count: {fence_count}")
        if has_module and not has_endmodule:
            diag_parts.append("Issue: 'module' found but 'endmodule' is missing")
        if has_fences and not has_module:
            diag_parts.append("Issue: code fences present but no Verilog module inside")

        error_feedback = (
            "CRITICAL: Your previous output could not be parsed into valid Verilog. "
            "Diagnostic info:\n"
            + "\n".join(f"- {p}" for p in diag_parts)
            + "\n\nPlease output ONLY the Verilog code inside a single ```verilog code fence. "
            "Do NOT include duplicate fences, extra text, or artifacts after endmodule."
        )

        retry_context = {
            "PROJECT_DIR": str(project_dir),
            "MODE": context.get("mode", "standard"),
            "STAGE_NAME": f"stage3_{module_name}_retry",
            "MODULE_NAME": module_name,
            "MODULE_SPEC": module_spec[:8000],
            "MICRO_ARCH": microarch_text[:6000],
            "PEER_INTERFACES": peer_summary[:4000],
            "USER_FEEDBACK": error_feedback,
            "EXPERIENCE_HINT": context.get("experience_hint", ""),
            "SUPERVISOR_HINT": context.get("supervisor_hint", ""),
        }

        event_collector = context.get("_event_collector")
        extractor = StreamingOutputExtractor(
            fence_types=["verilog", "v"],
            extract_mode="code_fences",
        )

        try:
            prompt = self.render_prompt(retry_context)
            if event_collector:
                llm_output = self._consume_streaming(
                    context, prompt, event_collector,
                    output_extractor=extractor,
                )
            else:
                llm_output = self.call_llm(
                    context, prompt_override=prompt,
                    output_extractor=extractor,
                )
        except Exception as e:
            logger.warning(f"[coder] Self-retry LLM call failed for {module_name}: {e}")
            return ""

        # Save retry output for debugging
        try:
            log_dir = project_dir / "workspace" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"coder_{module_name}_retry_raw.txt"
            log_path.write_text(llm_output, encoding="utf-8")
        except Exception:
            pass

        return self._extract_verilog(llm_output)

    @staticmethod
    def _quick_validate_verilog(code: str, module_name: str) -> list[str]:
        """Quick regex-based validation of extracted Verilog code.

        Returns a list of warning strings (empty if code looks clean).
        """
        warnings: list[str] = []

        # Check for markdown fence artifacts
        if '```' in code:
            warnings.append(f"{module_name}: contains markdown fence artifacts (```)")

        # Check for balanced begin/end
        begin_count = len(re.findall(r'\bbegin\b', code))
        end_count = len(re.findall(r'\bend\b', code))
        # endmodule also contains 'end', but we don't want to count it
        endmodule_count = len(re.findall(r'\bendmodule\b', code))
        effective_end = end_count - endmodule_count
        if begin_count != effective_end and begin_count > 0:
            warnings.append(
                f"{module_name}: unbalanced begin/end "
                f"(begin={begin_count}, end={effective_end})"
            )

        # Check that file starts with 'module'
        if not code.lstrip().startswith("module"):
            warnings.append(f"{module_name}: file does not start with 'module'")

        return warnings

    @staticmethod
    def _quick_lint_check(
        iverilog_path: str, file_path: Path, module_name: str
    ) -> None:
        """Run iverilog -tnull on a single file as a quick syntax check.

        Logs warnings but does not fail the stage.
        """
        import subprocess
        from veriflow_agent.tools.eda_utils import get_eda_env

        try:
            result = subprocess.run(
                [iverilog_path, "-Wall", "-tnull", str(file_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=get_eda_env(),
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                # Show first 5 error lines
                error_lines = [l for l in stderr.splitlines()
                               if ": error:" in l.lower() or ": fatal:" in l.lower()][:5]
                if error_lines:
                    logger.warning(
                        f"[coder] Quick lint found errors in {module_name}:\n"
                        + "\n".join(f"  {l}" for l in error_lines)
                    )
        except Exception as e:
            logger.debug(f"[coder] Quick lint check failed for {module_name}: {e}")
