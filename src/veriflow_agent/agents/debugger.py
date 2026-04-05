"""DebuggerAgent - Stage 4: Error Correction.

Analyzes simulation or lint error logs and applies minimal fixes
to RTL code. Testbench files are strictly read-only.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from veriflow_agent.agents.base import AgentResult, BaseAgent


class DebuggerAgent(BaseAgent):
    """Stage 4: Error Correction.

    Input: Error log + RTL files
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
                - error_type: "lint" or "sim"
                - error_log: Error text from lint or simulation
                - rtl_files: List of RTL file paths (relative to project_dir)
                Optional:
                - timing_model_yaml: Path to timing model YAML

        Returns:
            AgentResult indicating whether fixes were applied.
        """
        project_dir = Path(context.get("project_dir", "."))
        error_type = context.get("error_type", "lint")
        error_log = context.get("error_log", "")

        rtl_files = context.get("rtl_files", [])
        if isinstance(rtl_files, str):
            rtl_files = [rtl_files]

        # Resolve RTL file paths
        rtl_paths = []
        for f in rtl_files:
            p = Path(f)
            if not p.is_absolute():
                p = project_dir / p
            if p.exists():
                rtl_paths.append(str(p))

        if not rtl_paths:
            # Auto-discover RTL files
            rtl_dir = project_dir / "workspace" / "rtl"
            if rtl_dir.exists():
                rtl_paths = [str(f) for f in rtl_dir.glob("*.v")]

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

        # Build LLM context
        llm_context = {
            "PROJECT_DIR": str(project_dir),
            "ERROR_TYPE": error_type,
            "ERROR_LOG": error_log[:5000],
            "RTL_FILES": ", ".join(rtl_paths),
            "TIMING_MODEL_YAML": timing_model_text,
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

        return AgentResult(
            success=True,
            stage=self.name,
            artifacts=rtl_paths,
            metrics={
                "error_type": error_type,
                "files_fixed": len(rtl_paths),
            },
            raw_output=llm_output[:2000],
        )

    @staticmethod
    def _snapshot_directory(directory: Path) -> dict[str, str] | None:
        """Snapshot file contents of a directory for tamper protection.

        Returns:
            Dict mapping filename to content, or None if directory doesn't exist.
        """
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

        for name, content in snapshot.items():
            file_path = directory / name
            try:
                current = file_path.read_text(encoding="utf-8")
                if current != content:
                    file_path.write_text(content, encoding="utf-8")
            except Exception:
                pass
