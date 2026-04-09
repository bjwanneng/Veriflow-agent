"""DebuggerAgent - Error Correction (LLM-based).

Analyzes lint/sim/synth error logs and applies fixes to RTL code.
Reads error history from state to accumulate context across retries.
Testbench files are strictly read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import logging
import re

from veriflow_agent.agents.base import AgentResult, BaseAgent


logger = logging.getLogger("veriflow.agent")


class DebuggerAgent(BaseAgent):
    """Error Correction Agent.

    Input: error_type, error_log, rtl_files, error_history (from state)
    Output: Fixed RTL files in workspace/rtl/
    """

    def __init__(self):
        super().__init__(
            name="debugger",
            prompt_file="stage4_debugger.md",
            required_inputs=[],
            output_artifacts=["workspace/rtl/*.v"],
            max_retries=3,
            llm_backend="openai",
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
            AgentResult indicating whether fixes were applied.
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

        # Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "ERROR_TYPE": error_type,
            "ERROR_LOG": error_log[:5000],
            "RTL_FILES": ", ".join(rtl_paths),
            "TIMING_MODEL_YAML": timing_model_text,
            "ERROR_HISTORY": history_text,
            "FEEDBACK_SOURCE": feedback_source,
        }

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

        # Refresh file list after debug
        updated_paths = [str(f) for f in rtl_dir.glob("*.v") if not f.name.startswith("tb_")]

        if files_written == 0:
            return AgentResult(
                success=False,
                stage=self.name,
                errors=["LLM output contained no valid Verilog modules to write"],
                metrics={"error_type": error_type, "files_fixed": 0},
                raw_output=llm_output[:2000],
            )

        return AgentResult(
            success=True,
            stage=self.name,
            artifacts=updated_paths,
            metrics={
                "error_type": error_type,
                "files_fixed": len(updated_paths),
            },
            raw_output=llm_output[:2000],
        )

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
