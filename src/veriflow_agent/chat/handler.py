"""Pipeline chat handler — bridges Gradio UI ↔ LangGraph pipeline.

Streams pipeline progress as markdown messages by iterating over
graph.stream() events and formatting each stage transition.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Generator

from veriflow_agent.graph.state import (
    VeriFlowState,
    StageOutput,
    create_initial_state,
    MAX_RETRIES,
    ErrorCategory,
)
from veriflow_agent.graph.graph import create_veriflow_graph
from veriflow_agent.chat.project_manager import (
    create_project_from_requirement,
    update_requirement,
)
from veriflow_agent.chat.formatters import (
    format_pipeline_start,
    format_stage_progress,
    format_debugger_event,
    format_final_summary,
    format_inspection_response,
)

logger = logging.getLogger("veriflow")

# Keywords for intent classification
_INSPECT_KEYWORDS = {
    "show", "display", "read", "what", "view", "open",
    "rtl", "code", "verilog", "spec", "report", "timing",
    "synthesis", "quality", "files", "result", "list",
}

_MODIFY_KEYWORDS = {
    "add", "modify", "change", "update", "fix", "remove",
    "increase", "decrease", "extend", "reduce", "rename",
    "replace", "insert", "delete",
}


class PipelineChatHandler:
    """Bridges Gradio ChatInterface ↔ LangGraph pipeline.

    Handles multi-turn conversation:
    - New design: Creates project dir, runs full pipeline
    - Inspection: Reads generated files from project dir
    - Modification: Updates requirement, re-runs pipeline
    """

    def __init__(self):
        self._project_dirs: dict[str, Path] = {}  # session_id -> Path
        self._pipeline_running: dict[str, bool] = {}

    def handle_message(
        self,
        message: str,
        history: list[dict],
        session_id: str = "default",
    ) -> Generator[str, None, None]:
        """Main chat entry point. Called by Gradio for each user message.

        Yields incremental markdown strings for streaming display.

        Args:
            message: User's chat message.
            history: Chat history in Gradio message format.
            session_id: Session identifier for project isolation.

        Yields:
            Markdown strings (accumulated, each yield replaces previous).
        """
        # Classify intent
        intent = self._classify_intent(message, history)

        if intent == "inspect":
            yield from self._handle_inspection(message, session_id)
        elif intent == "modify":
            yield from self._handle_modification(message, session_id)
        else:
            yield from self._handle_new_design(message, session_id)

    def _classify_intent(self, message: str, history: list[dict]) -> str:
        """Classify user message intent."""
        msg_lower = message.lower().strip()
        msg_words = set(re.findall(r'\w+', msg_lower))

        # If no pipeline run yet, always treat as new design
        if not history or not any(
            r.get("role") == "assistant" and "Pipeline" in r.get("content", "")
            for r in history
        ):
            return "new"

        # Check for inspection keywords
        if msg_words & _INSPECT_KEYWORDS:
            return "inspect"

        # Check for modification keywords
        if msg_words & _MODIFY_KEYWORDS:
            return "modify"

        # Short message likely inspection
        if len(msg_lower) < 30:
            return "inspect"

        # Default: new design
        return "new"

    def _handle_inspection(
        self, message: str, session_id: str,
    ) -> Generator[str, None, None]:
        """Handle file inspection queries."""
        project_dir = self._project_dirs.get(session_id)
        if not project_dir or not project_dir.exists():
            yield "No project found. Please start a new design first.\n\nExample: *Design a 4-bit ALU supporting ADD, SUB, AND, OR operations*"
            return

        yield format_inspection_response(message, project_dir)

    def _handle_modification(
        self, message: str, session_id: str,
    ) -> Generator[str, None, None]:
        """Handle design modification requests."""
        project_dir = self._project_dirs.get(session_id)
        if not project_dir or not project_dir.exists():
            yield "No existing project to modify. Please start a new design first."
            return

        update_requirement(project_dir, message)
        yield f"### Updating Design\n\n> {message[:120]}\n\nRe-running pipeline with updated requirements...\n"

        yield from self._run_pipeline(project_dir, session_id)

    def _handle_new_design(
        self, message: str, session_id: str,
    ) -> Generator[str, None, None]:
        """Handle a new design request."""
        project_dir = create_project_from_requirement(message)
        self._project_dirs[session_id] = project_dir

        yield from self._run_pipeline(project_dir, session_id)

    def _run_pipeline(
        self, project_dir: Path, session_id: str,
    ) -> Generator[str, None, None]:
        """Execute the LangGraph pipeline with streaming.

        Uses graph.stream() to get per-node state updates.
        Each update is formatted and yielded as markdown.
        """
        self._pipeline_running[session_id] = True

        try:
            graph = create_veriflow_graph(with_checkpointer=True)
            state = create_initial_state(str(project_dir))
            config = {
                "configurable": {
                    "thread_id": f"chat-{session_id}-{project_dir.name}",
                }
            }

            # Read requirement for header
            req_path = project_dir / "requirement.md"
            req_text = req_path.read_text(encoding="utf-8") if req_path.exists() else ""

            response = format_pipeline_start(req_text)
            yield response

            # Track completed stages for progress bar
            completed: list[str] = []
            failed: list[str] = []
            retry_counts: dict[str, int] = {}
            stage_num = 0
            prev_stages_count = 0

            for event in graph.stream(state, config):
                if not self._pipeline_running.get(session_id, True):
                    response += "\n\n**Pipeline stopped by user.**\n"
                    yield response
                    return

                # event is {node_name: state_update_dict}
                for node_name, updates in event.items():
                    stage_output_key = f"{node_name}_output"
                    stage_output = updates.get(stage_output_key)

                    if not stage_output:
                        continue

                    # Update tracking
                    completed = list(updates.get("stages_completed", completed))
                    failed = list(updates.get("stages_failed", failed))
                    retry_counts = dict(updates.get("retry_count", retry_counts))

                    if node_name == "debugger":
                        # Debugger feedback loop event
                        feedback_source = updates.get("feedback_source", "")
                        rollback_target = updates.get("target_rollback_stage", "lint")
                        error_categories = updates.get("error_categories", {})
                        error_cat = error_categories.get(feedback_source, "unknown") if isinstance(error_categories, dict) else "unknown"

                        response += format_debugger_event(
                            feedback_source=feedback_source,
                            retry_count=retry_counts.get(feedback_source, 1),
                            max_retries=MAX_RETRIES,
                            rollback_target=rollback_target,
                            error_category=error_cat,
                            all_completed=completed,
                            all_failed=failed,
                            retry_counts=retry_counts,
                        )
                    else:
                        # Regular stage completion
                        if node_name in completed or node_name in failed:
                            if node_name not in [s for s in completed[:prev_stages_count]]:
                                stage_num += 1
                        prev_stages_count = len(completed)

                        response += format_stage_progress(
                            stage_name=node_name,
                            stage_output=stage_output,
                            all_completed=completed,
                            all_failed=failed,
                            retry_counts=retry_counts,
                            stage_num=stage_num,
                            total_stages=8,
                        )

                    yield response

            # Final summary
            try:
                final_state = graph.get_state(config).values
            except Exception:
                final_state = {}

            response += format_final_summary(final_state, project_dir)

            # Auto-display RTL code
            from veriflow_agent.chat.formatters import format_rtl_code_display
            rtl_display = format_rtl_code_display(project_dir)
            if rtl_display:
                response += rtl_display

            yield response

        except Exception as e:
            logger.exception("Pipeline execution failed")
            response += f"\n\n### Error\n\nPipeline execution failed:\n```\n{e}\n```\n"
            yield response

        finally:
            self._pipeline_running[session_id] = False

    def stop_pipeline(self, session_id: str = "default") -> None:
        """Signal the running pipeline to stop."""
        self._pipeline_running[session_id] = False
