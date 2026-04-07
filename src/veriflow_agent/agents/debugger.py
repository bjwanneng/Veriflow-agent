"""DebuggerAgent - Error Correction (LLM-based).

Analyzes lint/sim/synth error logs and applies fixes to RTL code.
Reads error history from state to accumulate context across retries.
Testbench files are strictly read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


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

        # Refresh file list after debug
        updated_paths = [str(f) for f in rtl_dir.glob("*.v") if not f.name.startswith("tb_")]

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
                except Exception:
                    pass
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
