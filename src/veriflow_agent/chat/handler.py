"""Pipeline chat handler — bridges Gradio UI ↔ LangGraph pipeline.

Supports two modes:
- Conversational chat: General questions answered directly by the LLM
- Pipeline execution: Full RTL design pipeline triggered by explicit design requests

Intent classification determines which mode to use.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

from veriflow_agent.chat.formatters import (
    format_debugger_event,
    format_final_summary,
    format_inspection_response,
    format_pipeline_start,
    format_stage_progress,
    format_stage_started,
)
from veriflow_agent.chat.llm import (
    LLMConfig,
    call_llm_stream,
)
from veriflow_agent.chat.project_manager import (
    create_project_from_requirement,
    update_requirement,
)
from veriflow_agent.graph.graph import create_veriflow_graph
from veriflow_agent.graph.state import (
    MAX_RETRIES,
    create_initial_state,
)

logger = logging.getLogger("veriflow")

# ── Intent classification ───────────────────────────────────────────────

# Strong signals that the user wants to START a design pipeline
_DESIGN_SIGNALS = {
    "design a", "create a", "implement a", "build a", "generate",
    "write verilog", "write rtl", "rtl code", "verilog module",
    "i need a", "help me design", "help me create", "help me build",
    "produce a", "synthesize", "make a circuit", "digital circuit",
    "fpga module", "asic design", "pipeline", "start design",
    "设计", "实现", "生成", "编写",
    "写一个", "帮我写", "帮我设计", "帮我实现",
    "做一个", "造一个", "设计一个", "实现一个",
    "veriflow-agent",
}

# Inspection: user wants to see existing pipeline outputs.
# Kept narrow and RTL-domain-specific to avoid catching general questions
# like "what is X?" or "show me how Y works".
_INSPECT_KEYWORDS = {
    "show rtl", "show verilog", "show spec", "show report", "show timing",
    "display rtl", "view rtl", "read rtl", "open rtl",
    "list files", "list rtl", "list modules",
    "rtl code", "verilog code", "synthesis report", "quality report",
    "查看rtl", "查看代码", "查看报告", "显示rtl",
}

# Modification: user wants to change existing design.
# Require at least one design-domain noun to reduce false positives.
_MODIFY_KEYWORDS = {
    "modify the", "change the", "update the", "fix the",
    "add a port", "add a module", "add a register",
    "remove the", "rename the", "replace the",
    "increase the", "decrease the",
    "修改设计", "修改模块", "增加端口", "删除模块", "更新设计",
}


class PipelineChatHandler:
    """Bridges Gradio ChatInterface ↔ LangGraph pipeline.

    Handles multi-turn conversation:
    - Chat: General questions answered by LLM directly
    - Design: Creates project dir, runs full pipeline
    - Inspection: Reads generated files from project dir
    - Modification: Updates requirement, re-runs pipeline
    """

    def __init__(self):
        self._project_dirs: dict[str, Path] = {}  # session_id -> Path
        self._pipeline_running: dict[str, bool] = {}
        self._llm_configs: dict[str, LLMConfig] = {}  # session_id -> config
        self._workspace_dirs: dict[str, Path] = {}   # session_id -> workspace override
        self._default_workspace: Path | None = None  # global default from config
        self._stage_mode: str = "auto"  # "auto" | "step"; loaded from config per run

    def set_llm_config(self, session_id: str, config: LLMConfig) -> None:
        """Update LLM configuration for a session."""
        self._llm_configs[session_id] = config

    def get_llm_config(self, session_id: str) -> LLMConfig:
        """Get LLM config for session, creating default if needed."""
        if session_id not in self._llm_configs:
            self._llm_configs[session_id] = LLMConfig()
        return self._llm_configs[session_id]

    def get_project_dir(self, session_id: str) -> Path | None:
        """Get the project directory for a session (public accessor)."""
        return self._project_dirs.get(session_id)

    # ── Workspace management ──────────────────────────────────────────

    def set_default_workspace(self, path: str | None) -> None:
        """Set global default workspace (from config or CLI flag)."""
        if path:
            self._default_workspace = Path(path).resolve()

    def set_workspace(self, session_id: str, path: str) -> str:
        """Set per-session workspace. Returns resolved path string."""
        resolved = Path(path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        self._workspace_dirs[session_id] = resolved
        return str(resolved)

    def get_workspace(self, session_id: str) -> Path | None:
        """Get effective workspace for a session (per-session > default > None)."""
        return self._workspace_dirs.get(session_id) or self._default_workspace

    def handle_message(
        self,
        message: str,
        history: list[dict],
        session_id: str = "default",
        event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """Main chat entry point. Called by Gradio for each user message.

        Yields incremental markdown strings for streaming display.

        Args:
            event_callback: Optional callable(event_type: str, payload: dict).
                            Called on stage transitions for structured notifications.
        """
        intent = self._classify_intent(message, history)

        if intent == "inspect":
            yield from self._handle_inspection(message, session_id)
        elif intent == "modify":
            yield from self._handle_modification(message, session_id)
        elif intent == "design":
            yield from self._handle_new_design(message, session_id, event_callback)
        else:
            # Default: conversational chat
            yield from self._handle_chat(message, history, session_id)

    def _classify_intent(self, message: str, history: list[dict]) -> str:
        """Classify user message intent.

        Priority: inspect > modify > design > chat
        Design is only triggered by explicit design request signals.
        """
        msg_lower = message.lower().strip()

        # Check if this is a pipeline-triggering design request
        has_design_signal = any(sig in msg_lower for sig in _DESIGN_SIGNALS)

        # Check if there's an existing project (pipeline has been run)
        has_project = any(
            r.get("role") == "assistant" and "Pipeline" in r.get("content", "")
            for r in history
        )

        # 1. Inspection: user wants to view existing outputs
        if has_project and any(kw in msg_lower for kw in _INSPECT_KEYWORDS) and len(msg_lower) < 120:
            return "inspect"

        # 2. Modification: user wants to change existing design
        if has_project and any(kw in msg_lower for kw in _MODIFY_KEYWORDS) and len(msg_lower) < 200:
            return "modify"

        # 3. Design: explicit design request
        if has_design_signal and len(msg_lower) > 8:
            return "design"

        # 4. Default: chat
        return "chat"

    # ── Conversational chat ──────────────────────────────────────────

    def _handle_chat(
        self,
        message: str,
        history: list[dict],
        session_id: str,
    ) -> Generator[str, None, None]:
        """Handle general conversation using the LLM directly."""
        config = self.get_llm_config(session_id)

        # Build conversation context from history (last 10 messages)
        recent_history = history[-10:] if history else []
        llm_messages = []
        for msg in recent_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                llm_messages.append({"role": role, "content": content})

        # Add current message
        llm_messages.append({"role": "user", "content": message})

        try:
            accumulated = ""
            for chunk in call_llm_stream(llm_messages, config):
                accumulated += chunk
                yield chunk  # Yield only the incremental chunk, not accumulated
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Chat LLM failed: {error_msg}")
            yield (
                f"I couldn't connect to the LLM backend.\n\n"
                f"**Error:** {error_msg}\n\n"
                f"Please configure your LLM settings in the sidebar "
                f"(API key, model, backend).\n\n"
                f"If you want to start a design, describe the circuit in detail, "
                f"e.g.: *\"Design a 4-bit ALU supporting ADD, SUB, AND, OR\"*"
            )

    # ── Pipeline modes ───────────────────────────────────────────────

    def _handle_inspection(
        self, message: str, session_id: str,
    ) -> Generator[str, None, None]:
        """Handle file inspection queries."""
        project_dir = self._project_dirs.get(session_id)
        if not project_dir or not project_dir.exists():
            yield "No project found. Please start a new design first.\n\nExample: *\"Design a 4-bit ALU supporting ADD, SUB, AND, OR operations\"*"
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
        self, message: str, session_id: str, event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """Handle a new design request."""
        workspace = self.get_workspace(session_id)
        project_dir = create_project_from_requirement(
            message, base_dir=str(workspace) if workspace else None,
        )
        self._project_dirs[session_id] = project_dir

        yield from self._run_pipeline(project_dir, session_id, event_callback)

    # ── Pipeline execution ───────────────────────────────────────────

    def _run_pipeline(
        self, project_dir: Path, session_id: str, event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """Execute the LangGraph pipeline with streaming.

        Yields incremental markdown chunks (not accumulated).
        Calls event_callback on stage transitions for structured notifications.
        When stage_mode=="step", emits 'stage_paused' events between stages,
        allowing the UI/gateway to pause and wait for user confirmation.
        """
        self._pipeline_running[session_id] = True

        # Load config to get stage_mode and token_budget
        from veriflow_agent.gateway.config import VeriFlowConfig  # local import to avoid circular
        try:
            cfg = VeriFlowConfig.load()
            stage_mode = cfg.stage_mode  # "auto" | "step"
            token_budget = cfg.token_budget
        except Exception:
            stage_mode = "auto"
            token_budget = 1_000_000

        def _emit(event_type: str, payload: dict) -> None:
            if event_callback:
                try:
                    event_callback(event_type, payload)
                except Exception:
                    pass

        try:
            graph = create_veriflow_graph(with_checkpointer=True)
            llm_cfg = self.get_llm_config(session_id)
            state = create_initial_state(
                str(project_dir),
                token_budget=token_budget,
                llm_api_key=llm_cfg.api_key,
                llm_base_url=llm_cfg.base_url,
                llm_model=llm_cfg.model,
            )
            config = {
                "configurable": {
                    "thread_id": f"chat-{session_id}-{project_dir.name}",
                }
            }

            # Read requirement for header
            req_path = project_dir / "requirement.md"
            req_text = req_path.read_text(encoding="utf-8") if req_path.exists() else ""

            _emit("stage_update", {"stage": "pipeline", "status": "started"})

            chunk = format_pipeline_start(req_text)
            yield chunk

            # Pre-announce the first stage (architect) so the UI shows
            # activity immediately, even though the LLM call will block
            # graph.stream() for 30-200 seconds.
            _emit("stage_update", {
                "stage": "architect",
                "status": "started",
                "stage_num": 1,
                "total_stages": 8,
            })

            # Track completed stages for progress bar
            completed: list[str] = []
            failed: list[str] = []
            retry_counts: dict[str, int] = {}
            stage_num = 0  # Incremented to N at stage N's completion
            prev_stages_count = 0

            for event in graph.stream(state, config):
                if not self._pipeline_running.get(session_id, True):
                    yield "\n\n**Pipeline stopped by user.**\n"
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

                        incremental = format_debugger_event(
                            feedback_source=feedback_source,
                            retry_count=retry_counts.get(feedback_source, 1),
                            max_retries=MAX_RETRIES,
                            rollback_target=rollback_target,
                            error_category=error_cat,
                            all_completed=completed,
                            all_failed=failed,
                            retry_counts=retry_counts,
                        )
                        _emit("stage_update", {
                            "stage": "debugger",
                            "status": "retry",
                            "source": feedback_source,
                            "retry_count": retry_counts.get(feedback_source, 1),
                            "rollback_target": rollback_target,
                        })
                    else:
                        # Regular stage completion — increment stage counter
                        stage_num += 1
                        prev_stages_count = len(completed)

                        incremental = format_stage_progress(
                            stage_name=node_name,
                            stage_output=stage_output,
                            all_completed=completed,
                            all_failed=failed,
                            retry_counts=retry_counts,
                            stage_num=stage_num,
                            total_stages=8,
                        )

                        # Extract structured info for event
                        stage_success = hasattr(stage_output, 'success') and stage_output.success
                        stage_duration = getattr(stage_output, 'duration_s', 0)
                        stage_artifacts = getattr(stage_output, 'artifacts', []) or []
                        _emit("stage_update", {
                            "stage": node_name,
                            "status": "pass" if stage_success else "fail",
                            "stage_num": stage_num,
                            "total_stages": 8,
                            "duration_s": stage_duration,
                            "artifacts": [str(a) for a in stage_artifacts],
                            "completed": completed,
                            "failed": failed,
                        })

                        # Pre-announce next stage if there is one
                        from veriflow_agent.chat.formatters import STAGE_ORDER
                        stage_idx = STAGE_ORDER.index(node_name) if node_name in STAGE_ORDER else -1
                        if stage_idx >= 0 and stage_idx + 1 < len(STAGE_ORDER):
                            next_stage = STAGE_ORDER[stage_idx + 1]
                            _emit("stage_update", {
                                "stage": next_stage,
                                "status": "started",
                                "stage_num": stage_num + 1,
                                "total_stages": 8,
                            })

                    yield incremental

                    # ── Step-mode pause ──────────────────────────────────
                    # After each non-debugger stage, if stage_mode is "step",
                    # emit a stage_paused event so the gateway can wait for
                    # the user to click "Next Stage" before continuing.
                    if stage_mode == "step" and node_name != "debugger":
                        from veriflow_agent.chat.formatters import STAGE_ORDER
                        stage_idx = STAGE_ORDER.index(node_name) if node_name in STAGE_ORDER else -1
                        has_next = stage_idx >= 0 and stage_idx + 1 < len(STAGE_ORDER)
                        if has_next:
                            next_stage_name = STAGE_ORDER[stage_idx + 1]
                            _emit("stage_paused", {
                                "stage": node_name,
                                "next_stage": next_stage_name,
                                "completed": completed,
                                "failed": failed,
                            })
                            # Yield a UI hint
                            yield (
                                f"\n\n> ⏸ **Stage `{node_name}` complete** — "
                                f"click **▶ Next Stage** to run `{next_stage_name}`, "
                                f"or **▶▶ Run All** to complete automatically.\n"
                            )
            # Final summary
            try:
                final_state = graph.get_state(config).values
            except Exception:
                final_state = {}

            summary = format_final_summary(final_state, project_dir)

            # Auto-display RTL code
            from veriflow_agent.chat.formatters import format_rtl_code_display
            rtl_display = format_rtl_code_display(project_dir)
            if rtl_display:
                summary += rtl_display

            _emit("stage_update", {
                "stage": "pipeline",
                "status": "done",
                "completed": completed,
                "failed": failed,
            })

            yield summary

        except Exception as e:
            logger.exception("Pipeline execution failed")
            _emit("stage_update", {"stage": "pipeline", "status": "error", "error": str(e)})
            yield f"\n\n### Error\n\nPipeline execution failed:\n```\n{e}\n```\n"

        finally:
            self._pipeline_running[session_id] = False

    def stop_pipeline(self, session_id: str = "default") -> None:
        """Signal the running pipeline to stop."""
        self._pipeline_running[session_id] = False
