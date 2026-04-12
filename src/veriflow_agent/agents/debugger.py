"""DebuggerAgent - Error Correction (LLM-based).

Analyzes lint/sim/synth error logs and applies fixes to RTL code.
Reads error history from state to accumulate context across retries.
Testbench files are strictly read-only.

LLM-Enhanced: After fixing RTL, the debugger also performs a structured
error analysis to determine the optimal rollback target, replacing the
regex-based categorize_error() with LLM-driven root cause analysis.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any
import logging
import re

from veriflow_agent.agents.base import AgentResult, BaseAgent
from veriflow_agent.agents.output_extractor import StreamingOutputExtractor


logger = logging.getLogger("veriflow.agent")


class DebuggerAgent(BaseAgent):
    """Error Correction Agent.

    Input: error_type, error_log, rtl_files, error_history (from state)
    Output: Fixed RTL files in workspace/rtl/
            + LLM error analysis (category, rollback_target, reasoning)
    """

    def __init__(self):
        super().__init__(
            name="debugger",
            prompt_file="stage4_debugger.md",
            required_inputs=[],
            output_artifacts=["workspace/rtl/*.v"],
            max_retries=3,
            llm_backend="claude_cli",
        )

    def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute error correction.

        Args:
            context: Must contain:
                - project_dir: Path to project root
                - error_type: "lint" or "sim" or "synth"
                - error_log: Error text from the check
                - feedback_source: Which check triggered this ("lint"/"sim"/"synth")
                Optional:
                - error_history: List of previous errors from state
                - timing_model_yaml: Path to timing model YAML

        Returns:
            AgentResult with metrics containing:
            - error_type, files_fixed
            - llm_error_category: LLM-determined error category
            - llm_rollback_target: LLM-recommended rollback stage
            - llm_error_reasoning: LLM's analysis of the root cause
        """
        project_dir = Path(context.get("project_dir", "."))
        error_type = context.get("error_type", "lint")
        error_log = context.get("error_log", "")
        feedback_source = context.get("feedback_source", error_type)
        error_history = context.get("error_history", [])

        # Discover RTL files
        rtl_dir = project_dir / "workspace" / "rtl"
        rtl_paths = []
        if rtl_dir.exists():
            rtl_paths = [str(f) for f in rtl_dir.glob("*.v") if not f.name.startswith("tb_")]

        if not rtl_paths:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["No RTL files found to debug"],
            )

        # Snapshot testbench directory (tamper protection)
        tb_dir = project_dir / "workspace" / "tb"
        tb_snapshot = self._snapshot_directory(tb_dir)

        # Read timing model if available
        timing_model_text = ""
        timing_path = context.get("timing_model_yaml", "")
        if timing_path:
            tp = Path(timing_path)
            if not tp.is_absolute():
                tp = project_dir / tp
            if tp.exists():
                timing_model_text = tp.read_text(encoding="utf-8")[:3000]

        # Build error history context
        history_text = ""
        if error_history:
            history_lines = []
            for i, entry in enumerate(error_history[-5:], 1):  # Last 5 entries
                history_lines.append(f"--- Attempt {i} ---\n{entry[:2000]}")
            history_text = "\n\n".join(history_lines)

        # Read RTL file contents for inline prompt
        rtl_content_parts = []
        for rtl_path_str in rtl_paths:
            rp = Path(rtl_path_str)
            try:
                content = rp.read_text(encoding="utf-8", errors="replace")
                rtl_content_parts.append(f"### {rp.name}\n```verilog\n{content}\n```")
            except Exception:
                rtl_content_parts.append(f"### {rp.name}\n(Could not read file)")
        rtl_content = "\n\n".join(rtl_content_parts)

        # Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "ERROR_TYPE": error_type,
            "ERROR_LOG": error_log[:5000],
            "RTL_FILES": ", ".join(rtl_paths),
            "RTL_CONTENT": rtl_content,
            "TIMING_MODEL_YAML": timing_model_text,
            "ERROR_HISTORY": history_text,
            "FEEDBACK_SOURCE": feedback_source,
            "SUPERVISOR_HINT": context.get("supervisor_hint", ""),
        }

        # Apply strategy override if available (from supervisor or escalation)
        # Supervisor sets strategy_override["debugger"] when targeting debugger
        # Escalation may set strategy_override[feedback_source] (e.g. "lint")
        strategy_override = context.get("strategy_override", {})
        strategy_for_source = (
            strategy_override.get("debugger", "")
            or strategy_override.get(feedback_source, "")
        )
        if strategy_for_source:
            llm_context["STRATEGY_OVERRIDE"] = strategy_for_source

        # Apply retry tier context for tier-aware prompting
        retry_tier = context.get("retry_tier", {})
        tier = retry_tier.get(feedback_source, "simple_retry")
        if tier == "simplified":
            llm_context["STRATEGY_OVERRIDE"] = (
                "Generate the simplest possible implementation. "
                "Prefer behavioral modeling over structural. "
                "Minimize module complexity."
            )
        elif tier == "strategy_change" and not strategy_for_source:
            llm_context["STRATEGY_OVERRIDE"] = (
                "Previous fix attempts failed. Try a fundamentally different approach."
            )

        try:
            prompt = self.render_prompt(llm_context)

            # Check if EventCollector is available for streaming
            event_collector = context.get("_event_collector")
            extractor = StreamingOutputExtractor(
                fence_types=["verilog", "v"],
                extract_mode="code_fences",
            )
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
            self._restore_snapshot(tb_dir, tb_snapshot)
            return AgentResult(
                success=False,
                stage=self.name,
                errors=[f"LLM invocation failed: {e}"],
            )

        # Restore testbench if it was modified
        self._restore_snapshot(tb_dir, tb_snapshot)

        # Parse LLM output and write fixed RTL files
        files_written = self._write_fixed_rtl(rtl_dir, llm_output)

        # ── LLM Error Analysis: determine rollback target ──
        error_analysis = self._analyze_error_with_llm(
            context, error_log, feedback_source, error_history, llm_output,
        )

        # Refresh file list after debug
        updated_paths = [str(f) for f in rtl_dir.glob("*.v") if not f.name.startswith("tb_")]

        if files_written == 0:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["LLM output contained no valid Verilog modules to write"],
                metrics={
                    "error_type": error_type,
                    "files_fixed": 0,
                    **error_analysis,
                },
                raw_output=llm_output[:2000],
            )

        return AgentResult(
            success=True,
            stage=self.name,
            artifacts=updated_paths,
            metrics={
                "error_type": error_type,
                "files_fixed": len(updated_paths),
                **error_analysis,
            },
            raw_output=llm_output[:2000],
        )

    def _analyze_error_with_llm(
        self,
        context: dict[str, Any],
        error_log: str,
        feedback_source: str,
        error_history: list[str],
        fix_output: str,
    ) -> dict[str, str]:
        """Use LLM to analyze the error and determine rollback target.

        Returns dict with keys:
            llm_error_category: "syntax" | "logic" | "timing" | "resource" | "unknown"
            llm_rollback_target: "coder" | "microarch" | "timing" | "lint"
            llm_error_reasoning: Human-readable analysis
            llm_fix_strategy: What the fix does
        """
        # Skip LLM analysis in economy mode to save tokens
        budget_mode = context.get("budget_mode", "normal")
        if budget_mode == "economy":
            logger.info("Skipping LLM error analysis in economy budget mode")
            return {
                "llm_error_category": "",
                "llm_rollback_target": "",
                "llm_error_reasoning": "Skipped in economy mode",
                "llm_fix_strategy": "",
            }
        analysis_prompt = f"""你是 RTL 错误分析专家。分析以下错误日志，判断根因并推荐最佳回滚目标。

## 错误来源
检查阶段: {feedback_source}

## 错误日志
{error_log[:3000]}

## 错误历史 (前几次尝试)
{chr(10).join(error_history[-3:]) if error_history else "(无历史)"}

## 已应用的修复 (debugger 输出摘要)
{fix_output[:1500]}

## 管道阶段
architect → microarch → timing → coder → skill_d → lint → sim → synth

## 回滚目标规则
- SYNTAX (语法错误): → coder (代码生成问题)
- LOGIC (功能错误): → microarch (sim失败,设计问题) / coder (lint/synth失败,代码问题)
- TIMING (时序违例): → timing (synth失败,时序模型) / coder (lint/sim失败,代码未遵循时序)
- RESOURCE (资源超限): → timing (synth失败,约束需调整) / coder
- UNKNOWN → lint (保守全量回退)

## 输出格式
返回严格的 JSON:
```json
{{
  "error_category": "syntax" | "logic" | "timing" | "resource" | "unknown",
  "rollback_target": "coder" | "microarch" | "timing" | "lint",
  "reasoning": "根因分析（中文，1-3句）",
  "fix_strategy": "修复策略说明（中文，1-2句）"
}}
```

重要：只返回 JSON，不要添加其他文本。"""

        try:
            from veriflow_agent.chat.llm import LLMConfig, call_llm_stream

            # Use the session's LLM config if available
            api_key = context.get("llm_api_key", "")
            base_url = context.get("llm_base_url", "")
            model = context.get("llm_model", "")
            config = LLMConfig(api_key=api_key, base_url=base_url, model=model)

            messages = [{"role": "user", "content": analysis_prompt}]
            response_text = ""
            for chunk in call_llm_stream(messages, config):
                response_text += chunk

            # Parse JSON from response
            json_match = re.search(
                r'```(?:json)?\s*(\{[\s\S]*?\})\s*```',
                response_text,
            )
            if json_match:
                json_str = json_match.group(1)
            else:
                json_match = re.search(r'\{[\s\S]*"error_category"[\s\S]*\}', response_text)
                json_str = json_match.group(0) if json_match else response_text

            analysis = _json.loads(json_str)

            # Validate rollback target against known stages
            valid_targets = {"coder", "microarch", "timing", "lint"}
            rollback_target = analysis.get("rollback_target", "lint")
            if rollback_target not in valid_targets:
                logger.warning(
                    "LLM returned invalid rollback_target '%s', defaulting to 'lint'",
                    rollback_target,
                )
                rollback_target = "lint"

            result = {
                "llm_error_category": analysis.get("error_category", "unknown"),
                "llm_rollback_target": rollback_target,
                "llm_error_reasoning": analysis.get("reasoning", ""),
                "llm_fix_strategy": analysis.get("fix_strategy", ""),
            }
            logger.info(
                "LLM error analysis: category=%s, rollback=%s, reasoning=%s",
                result["llm_error_category"],
                result["llm_rollback_target"],
                result["llm_error_reasoning"][:100],
            )
            return result

        except Exception as e:
            logger.warning("LLM error analysis failed, using mechanical fallback: %s", e)
            return {
                "llm_error_category": "",
                "llm_rollback_target": "",
                "llm_error_reasoning": f"LLM analysis failed: {e}",
                "llm_fix_strategy": "",
            }

    @staticmethod
    def _snapshot_directory(directory: Path) -> dict[str, str] | None:
        """Snapshot file contents for tamper protection."""
        if not directory.exists():
            return None

        snapshot = {}
        for f in directory.iterdir():
            if f.is_file():
                try:
                    snapshot[f.name] = f.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to snapshot %s: %s", f.name, e)
        return snapshot

    @staticmethod
    def _restore_snapshot(directory: Path, snapshot: dict[str, str] | None) -> None:
        """Restore directory contents from a snapshot."""
        if snapshot is None or not directory.exists():
            return

        for existing in list(directory.iterdir()):
            if existing.is_file() and existing.name not in snapshot:
                try:
                    existing.unlink()
                except Exception:
                    pass

        for name, content in snapshot.items():
            file_path = directory / name
            try:
                current = file_path.read_text(encoding="utf-8") if file_path.exists() else None
                if current != content:
                    file_path.write_text(content, encoding="utf-8")
            except Exception:
                pass

    def _write_fixed_rtl(self, rtl_dir: Path, llm_output: str) -> int:
        """Parse LLM output and write fixed RTL files.

        The LLM is expected to return the fixed Verilog code wrapped in
        ```verilog ... ``` blocks, with one module per block.

        Returns:
            Number of files written.
        """
        import re

        if not rtl_dir.exists():
            rtl_dir.mkdir(parents=True, exist_ok=True)

        # Extract Verilog modules from the LLM output
        # Look for ```verilog ... ``` or ``` ... ``` blocks
        verilog_pattern = r'```(?:verilog)?\s*\n(.*?)\n```'
        matches = re.findall(verilog_pattern, llm_output, re.DOTALL)
        written = 0

        # If no code-fenced blocks found, try extracting raw module content
        if not matches and "module " in llm_output and "endmodule" in llm_output:
            start = llm_output.find("module ")
            end = llm_output.rfind("endmodule") + len("endmodule")
            matches = [llm_output[start:end].strip()]

        for verilog_content in matches:
            verilog_content = verilog_content.strip()
            if not verilog_content:
                continue

            # Extract module name from "module NAME ("
            module_match = re.search(r'module\s+(\w+)\s*\(', verilog_content, re.IGNORECASE)
            if not module_match:
                continue

            module_name = module_match.group(1)
            safe_name = re.sub(r'[^a-zA-Z0-9_]', '', module_name) or "unnamed"
            rtl_path = rtl_dir / f"{safe_name}.v"

            # Write the fixed RTL file
            rtl_path.write_text(verilog_content, encoding="utf-8")
            written += 1

        return written
