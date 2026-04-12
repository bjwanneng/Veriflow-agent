"""Pipeline chat handler — bridges Gradio UI ↔ LangGraph pipeline.

Supports two modes:
- Conversational chat: General questions answered directly by the LLM
- Pipeline execution: Full RTL design pipeline triggered by explicit design requests

Intent classification determines which mode to use.
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
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
    format_supervisor_event,
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

# Human-readable descriptions for each pipeline stage
_STAGE_DESCRIPTIONS: dict[str, str] = {
    "architect": "正在分析电路规格，提取接口定义和时序约束…",
    "microarch": "正在设计微架构，规划内部数据通路…",
    "timing":    "正在建立时序模型，分析关键路径…",
    "coder":     "正在调用 LLM 生成 Verilog RTL 代码…",
    "skill_d":   "正在进行 RTL 代码质量分析…",
    "lint":      "正在运行 iverilog 语法检查…",
    "sim":       "正在运行功能仿真，验证逻辑正确性…",
    "synth":     "正在运行 Yosys 逻辑综合，估算面积和时序…",
    "debugger":  "调试器介入，分析错误并准备修复…",
}

# ── Intent classification ───────────────────────────────────────────────

# Fallback keywords used ONLY when LLM is unavailable.
# LLM-driven intent recognition is the primary path; these are safety net.

_INSPECT_KEYWORDS = {
    "show rtl", "show verilog", "show spec", "show report", "show timing",
    "display rtl", "view rtl", "read rtl", "open rtl",
    "list files", "list rtl", "list modules",
    "rtl code", "verilog code", "synthesis report", "quality report",
    "查看rtl", "查看代码", "查看报告", "显示rtl",
}

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
        # Interactive architect: input wait mechanism
        self._input_events: dict[str, threading.Event] = {}
        self._input_data: dict[str, str] = {}
        # Pipeline restart: session_id -> (reason, updated_requirement)
        self._restart_requested: dict[str, tuple[str, str]] = {}

    def prepare_input_wait(self, session_id: str) -> threading.Event:
        """Create and register an input Event BEFORE emitting to UI.

        MUST be called before emit_fn("needs_input", ...) to prevent a race
        where the UI thread processes the event and calls provide_user_input
        before this thread has created the Event.
        """
        event = threading.Event()
        self._input_events[session_id] = event
        return event

    def wait_on_prepared(self, session_id: str, event: threading.Event, timeout: float = 600) -> str | None:
        """Wait on a pre-registered event. Use after prepare_input_wait()."""
        if event.wait(timeout=timeout):
            return self._input_data.pop(session_id, "")
        self._input_events.pop(session_id, None)
        return None

    def wait_for_user_input(self, session_id: str, timeout: float = 600) -> str | None:
        """Block until user provides input. Returns feedback or None on timeout.

        WARNING: Callers that emit UI events and then wait must use
        prepare_input_wait() + wait_on_prepared() instead to avoid race conditions.
        """
        event = threading.Event()
        self._input_events[session_id] = event
        if event.wait(timeout=timeout):
            return self._input_data.pop(session_id, "")
        self._input_events.pop(session_id, None)
        return None

    def provide_user_input(self, session_id: str, text: str) -> None:
        """Called by TUI to provide user feedback for architect clarification."""
        self._input_data[session_id] = text
        event = self._input_events.pop(session_id, None)
        if event:
            event.set()

    def cancel_input_wait(self, session_id: str) -> None:
        """Cancel any pending input wait (e.g. user pressed Ctrl+C)."""
        self._input_data[session_id] = "__cancelled__"
        event = self._input_events.pop(session_id, None)
        if event:
            event.set()

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
        """Main chat entry point. Called by TUI for each user message.

        Delegates to OrchestratorAgent — a unified LLM agent with tool calling
        that handles all intents (design, chat, inspect, modify) in one loop.
        """
        from veriflow_agent.chat.orchestrator import OrchestratorAgent

        orchestrator = OrchestratorAgent(self, session_id)
        yield from orchestrator.run(message, history, event_callback)

    def _classify_intent(self, message: str, history: list[dict]) -> str:
        """Classify user intent — always delegates to LLM for analysis.

        The LLM decides between design / chat / inspect / modify.
        Keywords are only used as fallback when called externally
        (e.g. _classify_intent_fallback).
        """
        # All messages go through LLM-driven analysis
        return "llm_analyze"

    @staticmethod
    def _classify_intent_fallback(message: str, history: list[dict]) -> str:
        """Keyword-based fallback intent classification.

        Used only when the LLM is unavailable or times out.
        """
        msg_lower = message.lower().strip()
        has_project = any(
            r.get("role") == "assistant" and "Pipeline" in r.get("content", "")
            for r in history
        )
        if has_project and any(kw in msg_lower for kw in _INSPECT_KEYWORDS) and len(msg_lower) < 120:
            return "inspect"
        if has_project and any(kw in msg_lower for kw in _MODIFY_KEYWORDS) and len(msg_lower) < 200:
            return "modify"
        return "design"

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

        # Retry loop for chat
        import time as _time
        max_retries = 10
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                accumulated = ""
                for chunk in call_llm_stream(llm_messages, config):
                    accumulated += chunk
                    yield chunk
                return  # success
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Chat LLM failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    yield f"\n\n[{attempt}/{max_retries} 重试中…]"
                    _time.sleep(1.0 * attempt)

        # All retries failed
        yield (
            f"\n\n**LLM 连接失败** ({max_retries} 次重试后)\n\n"
            f"错误: {last_error}\n\n"
            f"请检查网络连接和 API 配置。"
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

    def _handle_llm_driven(
        self,
        message: str,
        history: list[dict],
        session_id: str,
        event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """LLM-driven handler: LLM decides everything in one call.

        This is the core unified entry point. Instead of separate intent classification
        and context scanning, we give LLM all information upfront:
        - User message
        - Conversation history
        - Available context files (if any)

        LLM returns a structured decision about what to do next.
        """
        workspace = self.get_workspace(session_id)
        if workspace:
            project_dir = workspace
        else:
            project_dir = self._project_dirs.get(session_id) or Path(tempfile.mkdtemp(prefix="veriflow-chat-"))

        def _emit(event_type: str, payload: dict) -> None:
            if event_callback:
                try:
                    event_callback(event_type, payload)
                except Exception:
                    pass

        # ─────────────────────────────────────────────────────────────────
        # PHASE 1: Gather ALL context (user input + context/ files)
        # ─────────────────────────────────────────────────────────────────
        _emit("progress_message", {"text": "正在分析您的需求…"})

        # Scan context/ directory if it exists
        context_info = []
        context_dir = project_dir / "context"
        if context_dir.is_dir():
            from veriflow_agent.context.scanner import scan_context
            bundle = scan_context(project_dir)
            if bundle.files:
                _emit("progress_message", {
                    "text": f"发现 {len(bundle.files)} 个参考文档 (context/)"
                })
                for f in bundle.files:
                    cat_label = {
                        "requirement": "需求",
                        "reference": "参考",
                        "constraint": "约束",
                        "code_style": "规范",
                        "unknown": "其他",
                    }.get(f.category.value, "其他")
                    # Include preview of file content (first 500 chars)
                    preview = f.content[:500].replace("\n", " ")[:200]
                    context_info.append(
                        f"File: {f.rel_path}\n"
                        f"Category: {cat_label}\n"
                        f"Preview: {preview}...\n"
                    )

        context_section = "\n\n".join(context_info) if context_info else "(No context/ directory or no files found)"

        # Build conversation history for LLM
        history_text = ""
        if history:
            recent = history[-6:]  # Last 6 messages
            lines = []
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:200]
                if content:
                    lines.append(f"{role}: {content}")
            history_text = "\n".join(lines)

        # ─────────────────────────────────────────────────────────────────
        # PHASE 2: Single LLM call to decide everything
        # ─────────────────────────────────────────────────────────────────
        config = self.get_llm_config(session_id)

        system_prompt = f"""你是 RTL 设计助手的核心决策引擎。用户需要你的帮助来设计 Verilog 硬件模块。

你的任务：分析用户的完整输入（包括对话历史和可用的参考文件），做出最佳决策。

## 当前用户输入
{message}

## 对话历史
{history_text if history_text else "(无历史对话)"}

## 项目目录中的参考文件 (context/)
{context_section}

## 决策框架

请分析后返回严格的 JSON 格式：

```json
{{
  "mode": "design" | "chat" | "inspect" | "modify",
  "reasoning": "你的分析过程（用中文）",
  "requirement": "如果是design/modify模式，提取/整理后的完整需求文本",
  "use_context_files": true | false,
  "context_files_to_read": ["filename1.md", "filename2.md"],
  "needs_clarification": true | false,
  "clarification_question": "如果需要澄清，向用户提出的问题",
  "target_files": ["rtl", "spec", "synth_report", "timing"]
}}
```

决策规则：

1. **mode="chat"** - 当用户只是聊天、询问问题、或讨论与设计无关的话题
   - 例如："你好"、"你能做什么"、"什么是AXI协议"、"给我讲个笑话"

2. **mode="design"** - 当用户有设计意图时
   a. 如果用户输入包含具体功能描述 → use_context_files=false, needs_clarification=false
   b. 如果用户说"需求在目录里"、"在下面"、或context/中有清晰的需求文件 → use_context_files=true
   c. 如果需求模糊（只说"写个模块"但没说什么模块）→ needs_clarification=true

3. **mode="inspect"** - 当用户想查看已有项目产出物时
   - 例如："show me the RTL code"、"查看报告"、"spec是什么"
   - target_files 指定要查看的内容: "rtl" / "spec" / "synth_report" / "timing"

4. **mode="modify"** - 当用户想修改已有的设计时
   - 例如："修改模块增加一个端口"、"change the adder to carry-lookahead"
   - requirement 字段应包含修改指令

5. **requirement字段** - 提取出的最终需求文本：
   - 如果是直接输入：整理用户的技术描述（保留位宽、协议、功能等关键信息）
   - 如果用context文件：综合用户的简短指令 + context中的详细需求

6. **context_files_to_read** - 当use_context_files=true时，列出应该读取的文件名
   - 优先读取 "context/req/" 或 "context/requirements/" 下的文件
   - 也可以读取其他相关文件（如约束文件、参考设计等）

重要：
- 必须是有效的 JSON 格式
- reasoning 字段解释你的思考过程
- 不要在 JSON 外添加其他内容"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        # Call LLM with progress feedback
        response_text = ""
        import time as _time
        import json as _json
        import queue
        import threading

        _emit("progress_message", {"text": "正在连接 LLM 服务…"})

        for attempt in range(1, 4):  # Reduced from 5 to 3 attempts
            response_text = ""
            result_queue: queue.Queue[tuple[str, bool]] = queue.Queue()

            def _call_llm_thread(q: queue.Queue):
                """Run LLM call in background thread, put result in queue."""
                try:
                    text = ""
                    for chunk in call_llm_stream(messages, config):
                        text += chunk
                    q.put((text, True))  # (response, success)
                except Exception as e:
                    logger.error("LLM call error in thread: %s", e)
                    q.put((str(e), False))

            try:
                _emit("progress_message", {"text": f"正在调用 LLM 分析 (尝试 {attempt}/3)…"})

                llm_thread = threading.Thread(
                    target=_call_llm_thread, args=(result_queue,), daemon=True,
                )
                llm_thread.start()

                # Wait for result with 90s timeout (OpenAI client has 120s timeout)
                try:
                    result_text, success = result_queue.get(timeout=90)
                except queue.Empty:
                    # Thread still running — daemon thread will be cleaned up
                    logger.warning("LLM call timeout (attempt %d/3)", attempt)
                    _emit("progress_message", {"text": f"LLM 调用超时，准备重试…"})
                    if attempt < 3:
                        _time.sleep(1.0 * attempt)
                        continue
                    else:
                        break

                if not success:
                    logger.warning("LLM call error (attempt %d/3): %s", attempt, result_text)
                    if attempt < 3:
                        _time.sleep(1.0 * attempt)
                        continue

                response_text = result_text
                if response_text:
                    logger.info("LLM analysis completed, response length: %d", len(response_text))
                    break
                else:
                    logger.warning("Empty LLM response (attempt %d/3)", attempt)
                    if attempt < 3:
                        _time.sleep(0.5 * attempt)

            except Exception as e:
                logger.warning("LLM analysis failed (attempt %d/3): %s", attempt, e)
                if attempt < 3:
                    _time.sleep(0.5 * attempt)

        # If no response after all retries, use fallback
        if not response_text:
            _emit("progress_message", {"text": "LLM 分析失败，使用关键词匹配 fallback"})
            # Fallback: use keyword-based classification
            fallback_intent = self._classify_intent_fallback(message, history)
            import json as _fallback_json
            if fallback_intent == "inspect":
                response_text = _fallback_json.dumps({
                    "mode": "inspect",
                    "reasoning": "LLM unavailable, keyword fallback",
                    "target_files": ["rtl"],
                })
            elif fallback_intent == "modify":
                response_text = _fallback_json.dumps({
                    "mode": "modify",
                    "reasoning": "LLM unavailable, keyword fallback",
                    "requirement": message,
                })
            else:
                response_text = _fallback_json.dumps({
                    "mode": "design",
                    "requirement": message,
                    "use_context_files": False,
                    "needs_clarification": False,
                })
            logger.warning(f"Using fallback response due to LLM failure: {fallback_intent}")

        # Parse LLM response
        try:
            # Extract JSON from markdown code blocks or raw JSON
            json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r'\{[\s\S]*"mode"[\s\S]*\}', response_text)
                json_str = json_match.group(0) if json_match else response_text

            decision = _json.loads(json_str)
        except Exception as e:
            logger.error(f"Failed to parse LLM decision: {e}\nResponse: {response_text}")
            # Fallback: use keyword-based classification
            fallback_intent = self._classify_intent_fallback(message, history)
            if fallback_intent == "inspect":
                decision = {"mode": "inspect", "target_files": ["rtl"]}
            elif fallback_intent == "modify":
                decision = {"mode": "modify", "requirement": message}
            else:
                decision = {
                    "mode": "design",
                    "requirement": message,
                    "use_context_files": False,
                    "needs_clarification": False,
                }

        # ─────────────────────────────────────────────────────────────────
        # PHASE 3: Execute LLM's decision
        # ─────────────────────────────────────────────────────────────────

        mode = decision.get("mode", "design")

        if mode == "chat":
            # Not a design request
            _emit("progress_message", {"text": "切换到对话模式…"})
            yield from self._handle_chat(message, history, session_id)
            return

        if mode == "inspect":
            # User wants to view existing project outputs
            _emit("progress_message", {"text": "查看项目产出物…"})
            yield from self._handle_inspection(message, session_id)
            return

        if mode == "modify":
            # User wants to modify existing design
            _emit("progress_message", {"text": "检测到修改需求…"})
            project_dir = self._project_dirs.get(session_id)
            if project_dir and project_dir.exists():
                update_requirement(project_dir, message)
                yield f"### Updating Design\n\n> {message[:120]}\n\nRe-running pipeline with updated requirements...\n"
                yield from self._run_pipeline(project_dir, session_id)
            else:
                yield "No existing project to modify. Please start a new design first."
            return

        # mode == "design": ensure project directory exists
        if not project_dir.exists() or not (project_dir / "workspace").exists():
            (project_dir / "workspace" / "docs").mkdir(parents=True, exist_ok=True)
            (project_dir / "workspace" / "rtl").mkdir(parents=True, exist_ok=True)
            (project_dir / "workspace" / "tb").mkdir(parents=True, exist_ok=True)
            (project_dir / "workspace" / "logs").mkdir(parents=True, exist_ok=True)
        self._project_dirs[session_id] = project_dir

        if decision.get("needs_clarification"):
            question = decision.get("clarification_question", "请提供更多设计需求细节")
            _emit("needs_input", {
                "phase": "clarification",
                "question": question,
                "reasoning": decision.get("reasoning", ""),
            })
            return

        # Build final requirement
        if decision.get("use_context_files"):
            # Read specified context files
            from veriflow_agent.context.scanner import scan_context, DocCategory
            bundle = scan_context(project_dir)
            files_to_read = decision.get("context_files_to_read", [])

            # If no specific files specified, read all requirement category files
            if not files_to_read:
                req_files = bundle.by_category.get(DocCategory.REQUIREMENT, [])
                if req_files:
                    files_to_read = [f.rel_path for f in req_files]

            # Read and combine content
            context_parts = []
            for rel_path in files_to_read:
                file_path = project_dir / rel_path
                if file_path.exists():
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        context_parts.append(f"<!-- From {rel_path} -->\n{content}")
                    except Exception as e:
                        logger.warning(f"Failed to read {rel_path}: {e}")

            if context_parts:
                final_requirement = "\n\n".join(context_parts)
                _emit("progress_message", {
                    "text": f"已读取 {len(context_parts)} 个参考文件"
                })
            else:
                # Context files specified but not found
                _emit("needs_input", {
                    "phase": "error",
                    "message": "您提到了目录中的需求文件，但我没有找到指定的文件。请直接描述您的设计需求。"
                })
                return
        else:
            # Use LLM-extracted requirement or original message
            final_requirement = decision.get("requirement", message)

        # Write requirement and start pipeline
        req_path = project_dir / "requirement.md"
        req_path.write_text(final_requirement, encoding="utf-8")

        _emit("progress_message", {"text": "需求已确认，启动设计流程…"})

        # Run pipeline with restart support
        run_count = 0
        while True:
            run_count += 1
            yield from self._run_pipeline(project_dir, session_id, event_callback)

            restart = self._restart_requested.pop(session_id, None)
            if restart:
                reason, updated_req = restart
                req_path.write_text(updated_req, encoding="utf-8")
                yield f"\n\n### 重新启动 Pipeline (第 {run_count + 1} 次)\n\n> {reason}\n\n需求已更新，正在重新执行设计流程…\n"
                _emit("progress_message", {
                    "text": f"需求已更新，重新启动 pipeline (第 {run_count + 1} 次)"
                })
                continue
            break


    # ── Pipeline execution ───────────────────────────────────────────

    def _architect_clarification(
        self,
        project_dir: Path,
        session_id: str,
        req_text: str,
        emit_fn: Any,
    ) -> Generator[str, None, None]:
        """Pre-pipeline architect clarification — fully LLM-driven with JSON output.

        Uses structured JSON output from LLM to determine:
        - Is requirement clear?
        - What questions to ask if unclear?
        - How to present choices to user?

        No hardcoded parsing or string matching.
        """
        if not req_text or len(req_text.strip()) < 10:
            return

        emit_fn("progress_message", {"text": "分析需求，检查是否有需要确认的细节…"})

        # Check for cached confirmation (to avoid re-asking on restart)
        cache_path = project_dir / ".veriflow" / "clarification_cache.json"
        if cache_path.exists():
            try:
                import json as _json
                cache = _json.loads(cache_path.read_text(encoding="utf-8"))
                if cache.get("confirmed"):
                    emit_fn("progress_message", {"text": "使用缓存的需求确认结果"})
                    return
            except Exception:
                pass

        config = self.get_llm_config(session_id)

        # LLM analyzes requirement and returns structured decision
        analysis_prompt = f"""你是 RTL 架构师。分析以下硬件设计需求并做出决策。

## 需求文本
{req_text[:4000]}

## 你的任务
1. 分析需求是否足够明确（是否有具体的模块功能、接口定义、位宽等）
2. 如果不明确，生成需要向用户确认的问题
3. 返回严格的 JSON 格式决策

## 输出格式
```json
{{
  "is_clear": true | false,
  "reasoning": "分析理由（中文）",
  "draft_summary": "对需求的理解摘要，包括建议的模块名、主要功能、接口",
  "questions": [
    {{
      "id": "q1",
      "text": "问题文本",
      "options": ["选项A", "选项B", "选项C"],
      "default_index": 0
    }}
  ]
}}
```

规则：
- is_clear=true: 需求已经明确，无需提问（questions为空数组）
- is_clear=false: 需求有歧义，需要用户澄清（questions包含问题）
- 每个问题提供 2-4 个选项
- default_index 是默认选项的索引（从0开始）
- 问题应覆盖：位宽、深度、频率、复位方式、接口协议等不明确之处"""

        messages = [{"role": "user", "content": analysis_prompt}]

        import time as _time
        import json as _json

        # Call LLM and parse JSON response — capture internally, NOT yielded to UI
        response_text = ""
        for attempt in range(1, 6):
            try:
                for chunk in call_llm_stream(messages, config):
                    response_text += chunk
                    # Do NOT yield the raw JSON to the user — this is an internal decision
                break
            except Exception as e:
                logger.warning(f"Architect analysis failed (attempt {attempt}/5): {e}")
                if attempt < 5:
                    _time.sleep(0.5 * attempt)
                else:
                    emit_fn("progress_message", {"text": "需求分析失败，使用默认处理方式"})
                    # Fallback: treat as clear
                    response_text = '{"is_clear": true, "draft_summary": "使用原始需求", "questions": []}'

        # Parse JSON response
        try:
            json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_match = re.search(r'\{[\s\S]*"is_clear"[\s\S]*\}', response_text)
                json_str = json_match.group(0) if json_match else response_text

            decision = _json.loads(json_str)
        except Exception as e:
            logger.error(f"Failed to parse architect decision: {e}")
            # Fallback
            decision = {"is_clear": True, "draft_summary": "", "questions": []}

        is_clear = decision.get("is_clear", True)
        questions = decision.get("questions", [])
        draft_summary = decision.get("draft_summary", "")

        # If clear or no questions, do minimal confirmation
        if is_clear or not questions:
            emit_fn("progress_message", {"text": "需求分析完成，请确认草案"})

            # Show draft to user for confirmation
            display_draft = draft_summary or "需求已记录，准备开始设计。"
            confirm_event = self.prepare_input_wait(session_id)
            emit_fn("needs_input", {
                "phase": "draft_confirm",
                "draft": display_draft[:800],
                "question": "是否确认此设计草案并继续？",
                "options": ["确认，继续设计", "需要修改"],
                "default": "确认，继续设计",
            })

            feedback = self.wait_on_prepared(session_id, confirm_event, timeout=600)

            if feedback == "__cancelled__":
                return

            # Parse answer - LLM-driven interpretation
            confirm_lower = feedback.lower().strip() if feedback else ""
            confirmed = confirm_lower in ["确认，继续设计", "确认", "是", "yes", "y", ""] or \
                       (len(confirm_lower) == 1 and confirm_lower in ["a", "1"])

            # Save to cache
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                cache_path.write_text(_json.dumps({
                    "confirmed": confirmed,
                    "answer": feedback,
                    "timestamp": _time.time()
                }, indent=2), encoding="utf-8")
            except Exception:
                pass

            if confirmed:
                emit_fn("progress_message", {"text": "需求确认完成，启动设计流程"})
                return
            else:
                # User wants to modify
                req_path = project_dir / "requirement.md"
                updated = req_text + "\n\n## 用户修改意见\n" + (feedback if feedback else "需要修改")
                req_path.write_text(updated, encoding="utf-8")
                emit_fn("progress_message", {"text": "需求已更新，重新启动 pipeline"})
                return

        # Show draft summary first
        if draft_summary:
            draft_event = self.prepare_input_wait(session_id)
            emit_fn("needs_input", {
                "phase": "draft",
                "draft": draft_summary[:600],
            })
            self.wait_on_prepared(session_id, draft_event, timeout=600)

        # Ask questions one by one
        emit_fn("progress_message", {"text": f"需要确认 {len(questions)} 个问题…"})

        answers: list[str] = []
        for idx, q in enumerate(questions):
            q_text = q.get("text", "请确认")
            q_options = q.get("options", ["是", "否"])
            q_default_idx = q.get("default_index", 0)
            q_default = q_options[q_default_idx] if 0 <= q_default_idx < len(q_options) else q_options[0]

            q_event = self.prepare_input_wait(session_id)
            emit_fn("needs_input", {
                "phase": "question",
                "question": q_text,
                "options": q_options,
                "default": q_default,
                "index": idx + 1,
                "total": len(questions),
            })

            feedback = self.wait_on_prepared(session_id, q_event, timeout=600)

            if feedback == "__cancelled__":
                return

            # Fast path: simple answer resolution (no LLM call)
            answer = self._resolve_answer_simple(feedback, q_options, q_default)
            answers.append(f"- {q_text} → {answer}")

        if not answers:
            return

        # Compile answers and update requirement
        answer_text = "\n".join(answers)
        req_path = project_dir / "requirement.md"
        updated = req_text + "\n\n## 用户确认\n" + answer_text
        req_path.write_text(updated, encoding="utf-8")

        # Save cache to prevent re-asking on restart
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_path.write_text(_json.dumps({
                "confirmed": True,
                "answer": f"已回答 {len(answers)} 个确认问题",
                "timestamp": _time.time()
            }, indent=2), encoding="utf-8")
        except Exception:
            pass

        # ── NEW: Save shared context for cross-stage intelligence ───
        shared_context_path = project_dir / ".veriflow" / "shared_context.json"
        try:
            shared_context = {
                "user_requirement_summary": req_text[:1000],
                "clarification_history": [
                    {"question": q.get("text", ""), "answer": answers[i].split(" → ")[-1] if i < len(answers) else ""}
                    for i, q in enumerate(questions)
                ],
                "key_design_decisions": [
                    f"设计草案: {draft_summary[:200]}" if draft_summary else "",
                    f"用户确认: {len(answers)} 个关键参数",
                ],
                "extracted_parameters": self._extract_parameters_from_qa(questions, answers),
                "timestamp": _time.time(),
            }
            shared_context_path.write_text(
                _json.dumps(shared_context, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.debug("Failed to save shared context: %s", e)

        emit_fn("progress_message", {
            "text": f"需求确认完成 ({len(answers)} 项)，启动设计流程"
        })

    @staticmethod
    def _extract_parameters_from_qa(questions: list[dict], answers: list[str]) -> dict[str, str]:
        """Extract key design parameters from Q&A pairs.

        Uses LLM to dynamically extract parameters rather than hardcoding
        specific parameter names. This keeps the system generic for any
        RTL design domain (CPU, DSP, networking, etc.).
        """
        import json as _json

        if not questions or not answers:
            return {}

        # Build Q&A text for LLM analysis
        qa_text = []
        for i, q in enumerate(questions):
            q_text = q.get("text", "")
            answer = answers[i].split(" → ")[-1] if i < len(answers) else ""
            qa_text.append(f"Q: {q_text}\nA: {answer}")

        # Use LLM to extract parameters dynamically
        prompt = f"""Analyze the following Q&A pairs from a hardware design clarification session.
Extract key technical parameters and their values as a JSON object.

{chr(10).join(qa_text)}

Respond with ONLY a JSON object mapping parameter names to values. Examples:
{{"clock_frequency": "100MHz", "data_width": "32-bit"}}
{{"memory_size": "4KB", "pipeline_stages": "5"}}
{{"protocol": "AXI4", "burst_mode": "enabled"}}

If no clear parameters found, return {{}}."""

        try:
            from veriflow_agent.chat.llm import call_llm_stream
            from veriflow_agent.gateway.config import VeriFlowConfig

            config = VeriFlowConfig.load().to_llm_config()
            response = ""
            for chunk in call_llm_stream([{"role": "user", "content": prompt}], config):
                response += chunk

            # Extract JSON from response
            json_match = __import__('re').search(r'\{[^{}]*\}', response)
            if json_match:
                return _json.loads(json_match.group(0))
        except Exception as e:
            logger.debug("Dynamic parameter extraction failed: %s", e)

        return {}

    @staticmethod
    def _resolve_answer_simple(user_input: str, options: list[str], default: str) -> str:
        """Fast path: resolve user's answer without LLM call.

        Handles:
        - Empty input -> default
        - Exact match (case-insensitive)
        - Letter/number index (A/B/C/D or 1/2/3/4)
        - Partial match
        """
        if not user_input:
            return default

        user_clean = user_input.strip().lower()

        # Exact match
        for opt in options:
            if opt.lower() == user_clean:
                return opt

        # Letter/number index
        if len(user_clean) == 1:
            # A/B/C/D -> 0/1/2/3
            if user_clean in "abcd":
                idx = ord(user_clean) - ord('a')
                if 0 <= idx < len(options):
                    return options[idx]
            # 1/2/3/4 -> 0/1/2/3
            if user_clean.isdigit():
                idx = int(user_clean) - 1
                if 0 <= idx < len(options):
                    return options[idx]

        # Partial match (option text contained in user input)
        for opt in options:
            if opt.lower() in user_clean:
                return opt

        # Partial match reverse (user input contained in option)
        for opt in options:
            if user_clean in opt.lower():
                return opt

        return default

    def _cleanup_session_input_state(self, session_id: str) -> None:
        """Clean up input wait state for a session (called on reset/cancel)."""
        self._input_events.pop(session_id, None)
        self._input_data.pop(session_id, None)

    # ── Stage feedback analysis ─────────────────────────────────────────

    def _gather_stage_context(
        self, project_dir: Path, stage: str,
    ) -> str:
        """Gather context about a completed stage: errors, artifacts, logs."""
        parts: list[str] = []

        stage_artifact_map: dict[str, list[str]] = {
            "architect": ["workspace/docs/spec.json"],
            "microarch": ["workspace/docs/micro_arch.md"],
            "timing":    ["workspace/docs/timing_model.yaml"],
            "coder":     ["workspace/rtl/*.v"],
            "skill_d":   [],
            "lint":      ["workspace/logs/lint*.log"],
            "sim":       ["workspace/logs/sim*.log"],
            "synth":     ["workspace/docs/synth_report.json", "workspace/logs/synth*.log"],
        }

        for pattern in stage_artifact_map.get(stage, []):
            if "*" in pattern:
                matches = list(project_dir.glob(pattern))
            else:
                p = project_dir / pattern
                matches = [p] if p.exists() else []
            for artifact_path in matches[:3]:
                try:
                    content = artifact_path.read_text(encoding="utf-8", errors="replace")
                    if content.strip():
                        rel = artifact_path.relative_to(project_dir)
                        parts.append(f"### {rel}\n```\n{content[:3000]}\n```")
                except Exception:
                    pass

        logs_dir = project_dir / "workspace" / "logs"
        if logs_dir.is_dir():
            for log_file in sorted(logs_dir.glob("*.log"))[-5:]:
                try:
                    content = log_file.read_text(encoding="utf-8", errors="replace")
                    tail = content[-2000:] if len(content) > 2000 else content
                    rel = log_file.relative_to(project_dir)
                    parts.append(f"### {rel} (尾部)\n```\n{tail}\n```")
                except Exception:
                    pass

        return "\n\n".join(parts) if parts else "(无可用产出物或日志)"

    def _llm_stage_chat(
        self,
        session_id: str,
        messages: list[dict[str, str]],
        project_dir: Path,
        stage: str,
        next_stage: str,
        completed: list[str],
        failed: list[str],
        emit_fn: Any = None,
    ) -> tuple[str, str]:
        """Call LLM with full stage context, return (answer, action).

        The LLM handles ALL understanding — investigating, answering,
        and deciding the next action. We only extract the action tag.

        Returns:
            (response_text, action) where action is one of:
            "ask_more" | "continue" | "rerun" | "rerun_with:<text>" | "stop"
        """
        import time as _time
        config = self.get_llm_config(session_id)
        stage_context = self._gather_stage_context(project_dir, stage)
        req_path = project_dir / "requirement.md"
        requirement = req_path.read_text(encoding="utf-8")[:3000] if req_path.exists() else ""
        stage_summary = ", ".join(completed) if completed else "(none)"
        failed_summary = ", ".join(failed) if failed else "(none)"

        system_prompt = f"""你是 RTL 设计专家和流程管理器。用户在 RTL pipeline 执行过程中与你对话。

## Pipeline 状态
- 已完成阶段: {stage_summary}
- 失败阶段: {failed_summary}
- 刚完成阶段: {stage}
- 下一阶段: {next_stage}

## 原始需求
{requirement[:2000]}

## 当前阶段产出物和日志
{stage_context}

## 你的职责
1. 先阅读产出物和日志，了解实际情况
2. 回答用户的问题，解释发生了什么
3. 如果用户提出修改意见，转化为需求补充
4. 在回答的最后一行，用 [ACTION:xxx] 标签标明你建议的操作

## ⚠️ 关键限制：你不能调用任何工具

你是一个纯文本对话系统。你**不能**调用 read_file、list_dir 或任何其他工具。
上面的"产出物和日志"已经由系统自动提供给你了，你只需要阅读并分析。
绝对不要在回复中包含 <toolcall>、<function_call> 或任何工具调用格式。
你只能通过纯文本回复用户。

[ACTION:xxx] 标签规则（只选一个，放在最后一行）：
- [ACTION:ask_more] — 需要用户进一步确认或回答问题
- [ACTION:continue] — 建议继续执行下一阶段
- [ACTION:rerun_from:stage_name] — 增量修复：从指定阶段重新执行（保留之前阶段的产出物）
- [ACTION:rerun_with:需求补充文本] — 全量重跑：修改需求后从头重新执行整个 pipeline
- [ACTION:escalate] — 升级：回到架构阶段重新设计（当当前方法不奏效时使用）
- [ACTION:stop] — 建议停止，需要人工介入

Stage 依赖链（决定增量修复的起始阶段）：
  architect → microarch → timing → coder → skill_d → lint → sim → synth
  - 修改了 spec.json (architect 产出) → rerun_from:architect
  - 修改了 micro_arch.md (microarch 产出) → rerun_from:microarch
  - 修改了 timing_model.yaml (timing 产出) → rerun_from:timing
  - 只改 RTL 代码或修复 lint/sim 错误 → rerun_from:coder
  - 需要修改 requirement.md → rerun_with:补充内容

特殊指令识别：
- 如果用户说"重试"、"retry"、"重新运行"、"再试一次"、"请重试"、"重新生成"、"重新做"等 → 使用 [ACTION:rerun_from:当前阶段名]
- 如果当前阶段失败了，用户想重新运行 → 使用 [ACTION:rerun_from:{stage}]
- 优先使用 rerun_from 而非 rerun_with，因为增量修复更快且保留已完成的工作。

注意：[ACTION:xxx] 标签是给系统看的，用户看不到，请自然地回答用户的问题。"""

        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # Retry loop — up to 10 attempts
        max_retries = 10
        last_error = ""
        for attempt in range(1, max_retries + 1):
            response_text = ""
            try:
                for chunk in call_llm_stream(full_messages, config):
                    response_text += chunk

                # Success — extract action tag
                action = "ask_more"
                answer = response_text
                action_match = re.search(
                    r'\[ACTION:(rerun_with:.+|rerun_from:\w+|escalate|ask_more|continue|stop)\]\s*$',
                    response_text, re.MULTILINE
                )
                if action_match:
                    action = action_match.group(1)
                    answer = response_text[:action_match.start()].rstrip()

                # Strip any tool-call XML/JSON that the LLM might have generated
                # despite being told not to. Patterns: <toolcall>...</toolcall>,
                # <function_call>...</function_call>, etc.
                answer = re.sub(
                    r'<toolcall>[\s\S]*?</toolcall>',
                    '', answer, flags=re.IGNORECASE
                )
                answer = re.sub(
                    r'<function_call>[\s\S]*?</function_call>',
                    '', answer, flags=re.IGNORECASE
                )
                answer = re.sub(
                    r'```tool_call[\s\S]*?```',
                    '', answer, flags=re.IGNORECASE
                )
                answer = answer.strip()

                return answer, action

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Stage chat LLM call failed (attempt {attempt}/{max_retries}): {e}"
                )
                if emit_fn:
                    emit_fn("progress_message", {
                        "text": f"LLM 调用失败，正在重试 ({attempt}/{max_retries})…"
                    })
                # Brief backoff before retry
                if attempt < max_retries:
                    _time.sleep(1.0 * attempt)  # 1s, 2s, 3s...

        # All retries exhausted
        error_msg = f"LLM 调用失败 ({max_retries} 次重试后): {last_error}"
        logger.error(error_msg)
        return error_msg, "ask_more"

    def _step_mode_pause(
        self,
        session_id: str,
        project_dir: Path,
        stage_name: str,
        next_stage_name: str,
        completed: list[str],
        failed: list[str],
        event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """LLM-driven step-mode pause between stages.

        After each stage, if stage_mode=="step", this method:
        1. Emits a stage_confirm event to the UI
        2. Waits for user input
        3. If user provides feedback, sends it to LLM for analysis
        4. LLM decides: continue / ask_more / rerun_from / rerun_with / stop

        Yields markdown chunks for display.
        Returns a tuple (action, detail) where action is one of:
            "continue" — proceed to next stage
            "stop" — user wants to stop
            "rerun_from" — incremental re-run from `detail` stage
            "rerun_with" — full re-run with updated requirement `detail`

        IMPORTANT: This method is shared by _run_pipeline and _run_pipeline_partial
        to avoid ~80 lines of duplicated code.
        """
        def _emit(event_type: str, payload: dict) -> None:
            if event_callback:
                try:
                    event_callback(event_type, payload)
                except Exception:
                    pass

        confirm_event = self.prepare_input_wait(session_id)
        _emit("needs_input", {
            "phase": "stage_confirm",
            "stage": stage_name,
            "stage_success": stage_name not in failed,
            "next_stage": next_stage_name,
            "completed": completed,
            "failed": failed,
        })

        chat_history: list[dict[str, str]] = []

        while True:
            feedback = self.wait_on_prepared(
                session_id, confirm_event, timeout=3600
            )
            if feedback == "__cancelled__":
                yield "\n\n**Pipeline stopped by user.**\n"
                self._pipeline_running[session_id] = False
                return ("stop", "")

            # Shortcut: empty input = continue
            if not feedback:
                return ("continue", "")

            # Fast path: continue keywords (avoids LLM call for simple cases)
            feedback_lower = feedback.lower().strip()
            continue_keywords = [
                "继续", "continue", "ok", "好的", "下一步", "next",
                "yes", "y", "确认", "通过", "可以",
            ]
            if feedback_lower in continue_keywords:
                return ("continue", "")

            # Fast path: retry/regenerate keywords (avoids LLM call for simple cases)
            retry_keywords = [
                "重试", "retry", "重新运行", "再试一次", "请重试", "重新执行",
                "rerun", "重新生成", "重新做", "再来一次", "再来", "重新来过",
                "重新跑", "重新跑一下",
            ]
            if any(kw in feedback_lower for kw in retry_keywords):
                # Determine which stage to retry from based on user message
                from_stage = stage_name  # default: retry current stage
                stage_hints = {
                    "rtl": "coder", "verilog": "coder", "代码": "coder",
                    "rtl代码": "coder", "代码生成": "coder",
                    "架构": "architect", "规格": "architect",
                    "微架构": "microarch", "微结构": "microarch",
                    "时序": "timing", "timing": "timing",
                    "综合": "synth", "synthesis": "synth",
                }
                for hint, target in stage_hints.items():
                    if hint in feedback_lower:
                        from_stage = target
                        break
                _emit("progress_message", {"text": f"检测到重试请求，将从 {from_stage} 阶段重新执行…"})
                return ("rerun_from", from_stage)

            # Fast path: escalate keywords — go back to earlier stage for fundamental redesign
            escalate_keywords = [
                "升级", "escalate", "重新设计", "架构", "architect",
                "回到架构", "fundamental", "重新规划",
            ]
            if any(kw in feedback_lower for kw in escalate_keywords):
                _emit("progress_message", {"text": "检测到升级请求，将回到架构阶段重新设计…"})
                return ("rerun_from", "architect")

            # Complex cases: send to LLM for analysis
            _emit("progress_message", {
                "text": "正在分析你的输入，检查产出物…"
            })

            chat_history.append({"role": "user", "content": feedback})
            answer, action = self._llm_stage_chat(
                session_id,
                messages=chat_history,
                project_dir=project_dir,
                stage=stage_name,
                next_stage=next_stage_name,
                completed=completed,
                failed=failed,
                emit_fn=_emit,
            )
            chat_history.append({"role": "assistant", "content": answer})

            # Show LLM's answer — prepare Event BEFORE emit
            feedback_event = self.prepare_input_wait(session_id)
            _emit("needs_input", {
                "phase": "stage_feedback",
                "answer": answer,
            })

            # Execute LLM's decision
            if action == "continue":
                return ("continue", "")
            elif action == "stop":
                yield "\n\n**Pipeline stopped.**\n"
                self._pipeline_running[session_id] = False
                return ("stop", "")
            elif action.startswith("rerun_with:"):
                addition = action[len("rerun_with:"):]
                req_path = project_dir / "requirement.md"
                current_req = req_path.read_text(encoding="utf-8")
                updated = (
                    current_req
                    + "\n\n## 用户修改意见\n"
                    + addition
                )
                self._restart_requested[session_id] = (
                    addition, updated,
                )
                self._pipeline_running[session_id] = False
                return ("rerun_with", updated)
            elif action.startswith("rerun_from:"):
                target_stage = action[len("rerun_from:"):]
                self._pipeline_running[session_id] = False
                return ("rerun_from", target_stage)
            # else: "ask_more" → loop continues, wait on feedback_event
            confirm_event = feedback_event

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
                llm_backend=llm_cfg.backend,
                llm_api_key=llm_cfg.api_key,
                llm_base_url=llm_cfg.base_url,
                llm_model=llm_cfg.model,
            )
            # Use timestamp in thread_id to ensure fresh execution each time
            # (avoid LangGraph checkpoint resuming from previous failed run)
            import time as _time
            config = {
                "configurable": {
                    "thread_id": f"chat-{session_id}-{project_dir.name}-{_time.time():.0f}",
                }
            }

            # Read requirement for header
            req_path = project_dir / "requirement.md"
            req_text = req_path.read_text(encoding="utf-8") if req_path.exists() else ""

            _emit("stage_update", {"stage": "pipeline", "status": "started"})
            _emit("progress_message", {"text": "收到设计任务，正在解析需求文档…"})

            chunk = format_pipeline_start(req_text)
            yield chunk

            # ── Interactive architect: pre-pipeline clarification ──
            # NOTE: _architect_clarification is a regular function (no yield),
            # so we call it directly — NOT with yield from.
            self._architect_clarification(
                project_dir, session_id, req_text, _emit
            )

            _emit("progress_message", {"text": "启动 LangGraph RTL 流水线，共 8 个阶段…"})

            # Pre-announce the first stage (architect) so the UI shows
            # activity immediately, even though the LLM call will block
            # graph.stream() for 30-200 seconds.
            _emit("stage_update", {
                "stage": "architect",
                "status": "started",
                "stage_num": 1,
                "total_stages": 8,
            })
            _emit("progress_message", {"text": _STAGE_DESCRIPTIONS.get("architect", "")})

            # Track completed stages for progress bar
            completed: list[str] = []
            failed: list[str] = []
            retry_counts: dict[str, int] = {}
            stage_num = 0  # Incremented to N at stage N's completion
            prev_stages_count = 0

            event_count = 0
            for event in graph.stream(state, config):
                if not self._pipeline_running.get(session_id, True):
                    yield "\n\n**Pipeline stopped by user.**\n"
                    return

                event_count += 1
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

                    if node_name == "supervisor":
                        # Supervisor routing decision event
                        decision = updates.get("supervisor_decision", {})
                        call_count = updates.get("supervisor_call_count", 0)

                        incremental = format_supervisor_event(
                            decision=decision,
                            call_count=call_count,
                            max_calls=8,  # MAX_SUPERVISOR_CALLS
                        )
                        _emit("stage_update", {
                            "stage": "supervisor",
                            "status": "decision",
                            "decision": decision,
                            "call_count": call_count,
                        })
                        action = decision.get("action", "unknown")
                        target = decision.get("target_stage", "?")
                        root_cause = decision.get("root_cause", "")[:80]
                        _emit("progress_message", {
                            "text": f"Supervisor 分析完成: {action} → {target} ({root_cause})"
                        })
                    elif node_name == "debugger":
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
                        _emit("progress_message", {
                            "text": f"调试器介入 (第 {retry_counts.get(feedback_source, 1)} 次重试)，"
                                    f"分析 {feedback_source} 错误，回滚到 {rollback_target} 阶段…"
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
                        stage_errors = getattr(stage_output, 'errors', []) or []
                        stage_warnings = getattr(stage_output, 'warnings', []) or []
                        _emit("stage_update", {
                            "stage": node_name,
                            "status": "pass" if stage_success else "fail",
                            "stage_num": stage_num,
                            "total_stages": 8,
                            "duration_s": stage_duration,
                            "artifacts": [str(a) for a in stage_artifacts],
                            "errors": stage_errors[:5],
                            "warnings": stage_warnings[:3],
                            "completed": completed,
                            "failed": failed,
                        })

                        # Pre-announce next stage — only when current stage PASSED.
                        # When a stage fails, the graph routes to supervisor first,
                        # not the next linear stage, so pre-announcing "Lint Check"
                        # when skill_d failed is misleading.
                        from veriflow_agent.chat.formatters import STAGE_ORDER
                        stage_idx = STAGE_ORDER.index(node_name) if node_name in STAGE_ORDER else -1
                        if stage_success and stage_idx >= 0 and stage_idx + 1 < len(STAGE_ORDER):
                            next_stage = STAGE_ORDER[stage_idx + 1]
                            _emit("stage_update", {
                                "stage": next_stage,
                                "status": "started",
                                "stage_num": stage_num + 1,
                                "total_stages": 8,
                            })
                            desc = _STAGE_DESCRIPTIONS.get(next_stage, "")
                            if desc:
                                _emit("progress_message", {"text": desc})
                        elif not stage_success:
                            _emit("stage_update", {
                                "stage": "supervisor",
                                "status": "analyzing",
                                "source_stage": node_name,
                            })
                            from veriflow_agent.chat.formatters import STAGE_LABELS as _SL
                            _emit("progress_message", {
                                "text": f"{_SL.get(node_name, node_name)} 失败，Supervisor 正在分析原因…"
                            })

                    yield incremental

                    # ── Step-mode pause ──────────────────────────────────
                    # After each non-debugger stage, if stage_mode is "step",
                    # pause and let the LLM handle the conversation with the user.
                    if stage_mode == "step" and node_name != "debugger":
                        from veriflow_agent.chat.formatters import STAGE_ORDER
                        stage_idx = STAGE_ORDER.index(node_name) if node_name in STAGE_ORDER else -1
                        stage_success = hasattr(stage_output, 'success') and stage_output.success
                        has_next = stage_idx >= 0 and stage_idx + 1 < len(STAGE_ORDER)

                        # Only pause if there's a next stage OR stage failed
                        if has_next or not stage_success:
                            next_stage_name = STAGE_ORDER[stage_idx + 1] if has_next else ""
                            # Delegate to shared step-mode pause logic
                            result, detail = yield from self._step_mode_pause(
                                session_id=session_id,
                                project_dir=project_dir,
                                stage_name=node_name,
                                next_stage_name=next_stage_name,
                                completed=completed,
                                failed=failed,
                                event_callback=event_callback,
                            )
                            if result == "stop":
                                return
                            elif result == "rerun_from":
                                yield from self._run_pipeline_partial(
                                    project_dir, session_id,
                                    from_stage=detail,
                                    event_callback=event_callback,
                                )
                                return
                            elif result == "rerun_with":
                                return

            # Final summary
            if event_count == 0:
                logger.error(
                    "graph.stream() returned 0 events — pipeline graph produced no output. "
                    "This usually means the first node (architect) failed to return state updates, "
                    "or the graph routing hit END immediately."
                )
                yield (
                    "\n\n### ⚠️ Pipeline 产出了 0 个事件\n\n"
                    "LangGraph 流水线未执行任何阶段。可能原因：\n"
                    "- architect 阶段内部错误导致直接路由到 END\n"
                    "- graph.stream() 调用异常但被内部捕获\n\n"
                    "请检查日志后重试。"
                )

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

            # Stage completeness assertion
            from veriflow_agent.chat.formatters import STAGE_ORDER
            missing = [s for s in STAGE_ORDER if s not in completed and s not in failed]
            if missing:
                missing_report = (
                    f"\n\n### ⚠️ Pipeline 未完整执行\n\n"
                    f"| 状态 | 阶段 |\n|------|------|\n"
                )
                for s in STAGE_ORDER:
                    if s in completed:
                        missing_report += f"| ✅ 完成 | {s} |\n"
                    elif s in failed:
                        missing_report += f"| ❌ 失败 | {s} |\n"
                    else:
                        missing_report += f"| ⏭️ 未执行 | {s} |\n"
                missing_report += (
                    f"\n**缺失阶段: {', '.join(missing)}**\n\n"
                    f"可能原因: 某个阶段的异常导致 `graph.stream()` 提前终止。\n"
                    f"请检查日志后重新运行。\n"
                )
                summary += missing_report
                logger.warning(
                    "Pipeline incomplete — completed=%s, failed=%s, missing=%s",
                    completed, failed, missing,
                )

            _emit("stage_update", {
                "stage": "pipeline",
                "status": "done" if not missing else "incomplete",
                "completed": completed,
                "failed": failed,
                "missing": missing,
            })

            yield summary

        except Exception as e:
            import traceback as _tb
            tb_text = _tb.format_exc()
            logger.exception("Pipeline execution failed")
            logger.error("Full traceback:\n%s", tb_text)

            # Build detailed error report with stage progress
            from veriflow_agent.chat.formatters import STAGE_ORDER
            error_type = type(e).__name__
            stage_report = (
                f"\n\n### ❌ Pipeline 执行异常\n\n"
                f"**错误类型**: `{error_type}`\n"
                f"**错误信息**: `{e}`\n\n"
                f"### 阶段执行进度\n\n"
                f"| 状态 | 阶段 |\n|------|------|\n"
            )
            all_known = set(completed) | set(failed)
            for s in STAGE_ORDER:
                if s in completed:
                    stage_report += f"| ✅ 完成 | {s} |\n"
                elif s in failed:
                    stage_report += f"| ❌ 失败 | {s} |\n"
                else:
                    stage_report += f"| ⏭️ 未执行 | {s} |\n"
            stage_report += (
                f"\n已完成: {len(completed)}/{len(STAGE_ORDER)}  "
                f"失败: {len(failed)}  "
                f"未执行: {len(STAGE_ORDER) - len(all_known)}\n"
            )

            _emit("stage_update", {
                "stage": "pipeline",
                "status": "error",
                "error": str(e),
                "completed": completed,
                "failed": failed,
            })
            yield stage_report

        finally:
            self._pipeline_running[session_id] = False

    def _run_pipeline_partial(
        self,
        project_dir: Path,
        session_id: str,
        from_stage: str,
        event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """Incremental re-run: execute from `from_stage` onwards, preserving prior outputs.

        Instead of re-running the full LangGraph, this method directly calls
        the individual stage node functions from graph.py for the affected
        stages only. Prior stage outputs (files on disk) are preserved.

        Includes auto-healing: when a stage fails, the debugger is invoked
        to fix the issue, then stages are re-run from the rollback target.
        This mimics the LangGraph graph's conditional routing and retry loop.

        Args:
            from_stage: Stage name to start from (e.g., "coder", "architect").
        """
        from veriflow_agent.chat.formatters import STAGE_LABELS, STAGE_ORDER
        from veriflow_agent.gateway.config import VeriFlowConfig
        from veriflow_agent.graph.graph import (
            node_architect, node_microarch, node_timing,
            node_coder, node_skill_d, node_lint,
            node_sim, node_synth, node_debugger, node_tool_check,
        )
        from veriflow_agent.graph.state import (
            categorize_error, get_rollback_target,
        )

        # Validate from_stage
        if from_stage not in STAGE_ORDER:
            yield f"\n\n**Invalid stage: {from_stage}**. Valid stages: {', '.join(STAGE_ORDER)}\n"
            return

        start_idx = STAGE_ORDER.index(from_stage)
        # Use full STAGE_ORDER to allow rollback to earlier stages
        stages_to_run = STAGE_ORDER

        # Map stage names to their node functions
        node_fn_map = {
            "architect": node_architect,
            "microarch": node_microarch,
            "timing": node_timing,
            "coder": node_coder,
            "skill_d": node_skill_d,
            "lint": node_lint,
            "sim": node_sim,
            "synth": node_synth,
        }

        self._pipeline_running[session_id] = True

        # Load config
        try:
            cfg = VeriFlowConfig.load()
            stage_mode = cfg.stage_mode
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

        stages_from_start = len(STAGE_ORDER) - start_idx
        yield (
            f"\n\n### 增量修复\n\n"
            f"从 **{STAGE_LABELS.get(from_stage, from_stage)}** 阶段开始重跑 "
            f"({stages_from_start} 个阶段)，保留之前产出物…\n"
        )
        _emit("progress_message", {
            "text": f"增量修复：从 {from_stage} 开始重跑 {stages_from_start} 个阶段"
        })

        try:
            llm_cfg = self.get_llm_config(session_id)

            # Build initial state for the partial run
            state = create_initial_state(
                str(project_dir),
                token_budget=token_budget,
                llm_backend=llm_cfg.backend,
                llm_api_key=llm_cfg.api_key,
                llm_base_url=llm_cfg.base_url,
                llm_model=llm_cfg.model,
            )

            completed: list[str] = []
            failed: list[str] = []

            # ── Pre-check EDA tool availability when starting from EDA stages ──
            eda_stages = {"lint", "sim", "synth"}
            if from_stage in eda_stages or eda_stages.intersection(set(stages_to_run)):
                tool_updates = node_tool_check(state)
                if isinstance(tool_updates, dict):
                    state.update(tool_updates)
                skip_stages = state.get("eda_skip_stages", [])
                if skip_stages:
                    caveats = state.get("pipeline_complete_with_caveats", [])
                    for caveat in caveats:
                        _emit("progress_message", {"text": f"⚠ {caveat}"})
                    logger.info("Tool check: skipping stages %s", skip_stages)

            # ── Stage execution with auto-healing ─────────────────────
            stage_idx = 0
            max_global_retries = MAX_RETRIES  # Total auto-heal cycles allowed
            global_retry_count = 0

            while stage_idx < len(stages_to_run):
                stage_name = stages_to_run[stage_idx]

                if not self._pipeline_running.get(session_id, True):
                    yield "\n\n**Pipeline stopped by user.**\n"
                    return

                # Skip stages before start_idx (already completed in previous runs)
                if stage_idx < start_idx:
                    completed.append(stage_name)
                    stage_idx += 1
                    continue

                # Skip EDA stages whose tools are not available
                skip_stages = state.get("eda_skip_stages", [])
                if stage_name in skip_stages:
                    label = STAGE_LABELS.get(stage_name, stage_name)
                    logger.info("Skipping %s (tool not available)", stage_name)
                    yield f"\n**⏭ {label}** — 跳过 (EDA 工具不可用)\n"
                    _emit("stage_update", {
                        "stage": stage_name,
                        "status": "skipped",
                        "reason": "tool not available",
                    })
                    stage_idx += 1
                    continue

                node_fn = node_fn_map.get(stage_name)
                if not node_fn:
                    stage_idx += 1
                    continue

                label = STAGE_LABELS.get(stage_name, stage_name)
                _emit("stage_update", {
                    "stage": stage_name,
                    "status": "started",
                })
                _emit("progress_message", {"text": _STAGE_DESCRIPTIONS.get(stage_name, "")})

                # Execute the stage node
                updates: dict = {}
                try:
                    updates = node_fn(state)
                    if isinstance(updates, dict):
                        state.update(updates)
                except Exception as e:
                    logger.exception("[%s] Partial run stage failed", stage_name)
                    _emit("stage_update", {
                        "stage": stage_name,
                        "status": "error",
                        "error": str(e),
                    })
                    yield f"\n\n**{label} failed:** {e}\n"
                    failed.append(stage_name)
                    # Attempt auto-heal even on exceptions
                    healed, new_start_idx = self._auto_heal_stage(
                        state=state,
                        failed_stage=stage_name,
                        error_messages=[str(e)],
                        stages_to_run=stages_to_run,
                        node_fn_map=node_fn_map,
                        event_callback=event_callback,
                        global_retry_count=global_retry_count,
                        max_global_retries=max_global_retries,
                    )
                    # Yield debugger result summary
                    dbg_output_exc = state.get("debugger_output")
                    if dbg_output_exc:
                        from veriflow_agent.chat.formatters import format_debugger_event as _fmt_dbg
                        yield _fmt_dbg(
                            feedback_source=stage_name,
                            retry_count=state.get("retry_count", {}).get(stage_name, 1),
                            max_retries=MAX_RETRIES,
                            rollback_target=state.get("target_rollback_stage", stage_name),
                            error_category="exception",
                            all_completed=completed,
                            all_failed=failed,
                            retry_counts=state.get("retry_count", {}),
                        )
                    if healed:
                        global_retry_count += 1
                        # If rolling back to an earlier stage, update start_idx so it's not skipped
                        if new_start_idx < start_idx:
                            start_idx = new_start_idx
                        yield (
                            f"\n\n**自动修复中…** "
                            f"从 {STAGE_LABELS.get(stages_to_run[new_start_idx], stages_to_run[new_start_idx])} "
                            f"重新执行 (第 {global_retry_count}/{max_global_retries} 次自动修复)\n"
                        )
                        _emit("stage_update", {
                            "stage": "debugger",
                            "status": "auto_heal",
                            "target_stage": stages_to_run[new_start_idx],
                            "attempt": global_retry_count,
                        })
                        stage_idx = new_start_idx
                        continue
                    else:
                        # Auto-heal failed on exception - provide feedback and pause
                        yield (
                            f"\n\n**⚠️ 自动修复未能解决异常**\n\n"
                            f"阶段 **{label}** 发生异常且无法自动修复:\n"
                            f"```\n{e}\n```\n\n"
                            f"**建议操作:**\n"
                            f"1. 输入 **'escalate'** 或 **'升级'** - 回到架构阶段重新设计\n"
                            f"2. 输入具体反馈，描述你观察到的具体问题\n"
                            f"3. 输入 **'stop'** 或 **'停止'** - 停止 Pipeline\n"
                        )
                        # Pause for user decision regardless of stage_mode
                        next_idx_for_pause = STAGE_ORDER.index(stage_name) + 1
                        next_stage_for_pause = STAGE_ORDER[next_idx_for_pause] if next_idx_for_pause < len(STAGE_ORDER) else ""
                        result, detail = yield from self._step_mode_pause(
                            session_id=session_id,
                            project_dir=project_dir,
                            stage_name=stage_name,
                            next_stage_name=next_stage_for_pause,
                            completed=completed,
                            failed=failed,
                            event_callback=event_callback,
                        )
                        if result == "stop":
                            return
                        elif result == "rerun_from":
                            yield from self._run_pipeline_partial(
                                project_dir, session_id,
                                from_stage=detail,
                                event_callback=event_callback,
                            )
                            return
                        elif result == "rerun_with":
                            return
                        # If continue, break to end this partial run
                        break

                # Extract stage output for formatting
                stage_output_key = f"{stage_name}_output"
                stage_output = updates.get(stage_output_key) if isinstance(updates, dict) else None

                # Determine stage success - CRITICAL FIX: handle None case properly
                if stage_output is None:
                    # Stage produced no output - treat as failure, not success!
                    stage_success = False
                    stage_errors = ["Stage produced no output (possible internal error)"]
                    failed.append(stage_name)
                    _emit("stage_update", {
                        "stage": stage_name,
                        "status": "fail",
                        "error": "No output from stage",
                        "completed": completed,
                        "failed": failed,
                    })
                    yield f"\n✗ {label} (no output)\n"
                elif hasattr(stage_output, 'success'):
                    stage_success = stage_output.success
                    stage_errors = stage_output.errors if stage_output.errors else []
                    if stage_success:
                        completed.append(stage_name)
                        # Remove from failed if it was previously there
                        if stage_name in failed:
                            failed.remove(stage_name)
                    else:
                        if stage_name not in failed:
                            failed.append(stage_name)

                    # Format and yield progress
                    from veriflow_agent.chat.formatters import format_stage_progress
                    incremental = format_stage_progress(
                        stage_name=stage_name,
                        stage_output=stage_output,
                        all_completed=completed,
                        all_failed=failed,
                        retry_counts=state.get("retry_count", {}),
                        stage_num=STAGE_ORDER.index(stage_name) + 1,
                        total_stages=len(STAGE_ORDER),
                    )
                    yield incremental

                    _emit("stage_update", {
                        "stage": stage_name,
                        "status": "pass" if stage_success else "fail",
                        "completed": completed,
                        "failed": failed,
                    })
                else:
                    # Invalid stage_output structure
                    stage_success = False
                    stage_errors = ["Invalid stage output structure"]
                    failed.append(stage_name)
                    yield f"\n✗ {label} (invalid output)\n"

                # ── Auto-healing on stage failure ──────────────────
                # CRITICAL FIX: Always trigger auto-heal for failures (including None output)
                if not stage_success and stage_name not in ("architect",):
                    _emit("progress_message", {
                        "text": f"{label} 失败，启动自动修复流程…"
                    })

                    healed, new_start_idx = self._auto_heal_stage(
                        state=state,
                        failed_stage=stage_name,
                        error_messages=stage_errors,
                        stages_to_run=stages_to_run,
                        node_fn_map=node_fn_map,
                        event_callback=event_callback,
                        global_retry_count=global_retry_count,
                        max_global_retries=max_global_retries,
                    )

                    # Yield debugger result summary so the user can see what was fixed
                    dbg_output = state.get("debugger_output")
                    if dbg_output:
                        from veriflow_agent.chat.formatters import format_debugger_event
                        dbg_event = format_debugger_event(
                            feedback_source=stage_name,
                            retry_count=state.get("retry_count", {}).get(stage_name, 1),
                            max_retries=MAX_RETRIES,
                            rollback_target=state.get("target_rollback_stage", stage_name),
                            error_category=state.get("error_categories", {}).get(stage_name, "unknown") if isinstance(state.get("error_categories"), dict) else "unknown",
                            all_completed=completed,
                            all_failed=failed,
                            retry_counts=state.get("retry_count", {}),
                        )
                        yield dbg_event

                    if healed:
                        global_retry_count += 1
                        # If rolling back to an earlier stage, update start_idx so it's not skipped
                        if new_start_idx < start_idx:
                            start_idx = new_start_idx
                        yield (
                            f"\n\n**自动修复中…** "
                            f"从 {STAGE_LABELS.get(stages_to_run[new_start_idx], stages_to_run[new_start_idx])} "
                            f"重新执行 (第 {global_retry_count}/{max_global_retries} 次自动修复)\n"
                        )
                        _emit("stage_update", {
                            "stage": "debugger",
                            "status": "auto_heal",
                            "target_stage": stages_to_run[new_start_idx],
                            "attempt": global_retry_count,
                        })
                        stage_idx = new_start_idx
                        continue
                    else:
                        # Auto-heal failed - provide intelligent feedback
                        if global_retry_count >= max_global_retries:
                            yield (
                                f"\n\n**⚠️ 自动修复已达上限 ({max_global_retries} 次)**\n\n"
                                f"阶段 **{label}** 多次修复后仍然失败。\n\n"
                                f"**建议操作:**\n"
                                f"1. 输入 **'escalate'** 或 **'升级'** - 回到架构阶段重新设计\n"
                                f"2. 描述观察到的具体问题，我将分析并提供针对性建议\n"
                                f"3. 输入 **'stop'** 或 **'停止'** - 停止 Pipeline\n"
                            )
                        else:
                            yield (
                                f"\n\n**⚠️ 自动修复未能解决问题**\n\n"
                                f"Debugger 分析后无法自动修复 **{label}** 的问题。"
                                f"这可能需要更深层次的架构调整。\n\n"
                                f"**建议操作:**\n"
                                f"1. 输入 **'escalate'** 或 **'升级'** - 回到架构/微架构阶段重新设计\n"
                                f"2. 输入具体反馈，描述你观察到的具体问题\n"
                                f"3. 输入 **'rerun'** 或 **'重试'** - 从当前阶段再试一次\n"
                                f"4. 输入 **'stop'** 或 **'停止'** - 停止 Pipeline\n"
                            )
                        # CRITICAL FIX: Even in auto mode, when auto-heal fails,
                        # we must pause for user decision. Otherwise the pipeline
                        # appears to "hang" or continue with failed stages.
                        _emit("progress_message", {
                            "text": f"自动修复失败，等待用户决策…"
                        })
                        # Force step mode pause regardless of stage_mode setting
                        next_idx_for_pause = STAGE_ORDER.index(stage_name) + 1
                        next_stage_for_pause = STAGE_ORDER[next_idx_for_pause] if next_idx_for_pause < len(STAGE_ORDER) else ""
                        result, detail = yield from self._step_mode_pause(
                            session_id=session_id,
                            project_dir=project_dir,
                            stage_name=stage_name,
                            next_stage_name=next_stage_for_pause,
                            completed=completed,
                            failed=failed,
                            event_callback=event_callback,
                        )
                        if result == "stop":
                            return
                        elif result == "rerun_from":
                            yield from self._run_pipeline_partial(
                                project_dir, session_id,
                                from_stage=detail,
                                event_callback=event_callback,
                            )
                            return
                        elif result == "rerun_with":
                            return
                        # If continue, fall through to normal flow
                elif stage_success:
                    # Only mark as completed if explicitly successful
                    completed.append(stage_name)
                    yield f"\n✓ {label}\n"

                # Step-mode pause between stages (shared logic)
                if stage_mode == "step" and stage_name != stages_to_run[-1]:
                    stage_success_check = (
                        hasattr(stage_output, 'success') and stage_output.success
                        if stage_output else True
                    )
                    next_idx_check = STAGE_ORDER.index(stage_name) + 1
                    has_next = next_idx_check < len(STAGE_ORDER)

                    if has_next or not stage_success_check:
                        next_stage_name = STAGE_ORDER[next_idx_check] if has_next else ""
                        result, detail = yield from self._step_mode_pause(
                            session_id=session_id,
                            project_dir=project_dir,
                            stage_name=stage_name,
                            next_stage_name=next_stage_name,
                            completed=completed,
                            failed=failed,
                            event_callback=event_callback,
                        )
                        if result == "stop":
                            return
                        elif result == "rerun_from":
                            yield from self._run_pipeline_partial(
                                project_dir, session_id,
                                from_stage=detail,
                                event_callback=event_callback,
                            )
                            return
                        elif result == "rerun_with":
                            return

                stage_idx += 1

            # Summary
            if not failed:
                yield "\n\n**增量修复完成，所有重跑阶段通过！**\n"
            else:
                yield f"\n\n**增量修复完成，但以下阶段失败: {', '.join(failed)}**\n"

            _emit("stage_update", {
                "stage": "pipeline",
                "status": "done" if not failed else "error",
                "completed": completed,
                "failed": failed,
            })

        except Exception as e:
            logger.exception("Partial pipeline execution failed")
            _emit("stage_update", {"stage": "pipeline", "status": "error", "error": str(e)})
            yield f"\n\n### Error\n\nPartial pipeline execution failed:\n```\n{e}\n```\n"

        finally:
            self._pipeline_running[session_id] = False

    def _auto_heal_stage(
        self,
        state: dict,
        failed_stage: str,
        error_messages: list[str],
        stages_to_run: list[str],
        node_fn_map: dict,
        event_callback: Any,
        global_retry_count: int,
        max_global_retries: int,
    ) -> tuple[bool, int]:
        """Try to auto-heal a failed stage using the Supervisor LLM for intelligent routing.

        Calls node_supervisor() first for LLM-driven root-cause analysis, then acts on
        its decision (abort / degrade / escalate / route to debugger / jump to stage).
        Falls back to mechanical categorize_error() routing only if the supervisor LLM fails.

        Args:
            state: Current pipeline state dict (mutated in place).
            failed_stage: Name of the stage that failed.
            error_messages: Error strings from the failed stage.
            stages_to_run: Ordered list of stages being executed.
            node_fn_map: Mapping of stage name → node function.
            event_callback: UI event callback.
            global_retry_count: How many auto-heal cycles have been done.
            max_global_retries: Maximum auto-heal cycles allowed.

        Returns:
            Tuple of (healed: bool, new_start_idx: int).
            If healed is True, new_start_idx is the index in stages_to_run
            to resume from after the fix.
        """
        def _emit(event_type: str, payload: dict) -> None:
            if event_callback:
                try:
                    event_callback(event_type, payload)
                except Exception:
                    pass

        # ── Check retry limits ─────────────────────────────────────────────
        if global_retry_count >= max_global_retries:
            logger.warning(
                "Auto-heal: global retry limit (%d) reached for %s",
                max_global_retries, failed_stage,
            )
            return False, 0

        retry_count = state.get("retry_count", {})
        stage_retries = retry_count.get(failed_stage, 0)
        if stage_retries > max_global_retries:
            logger.warning(
                "Auto-heal: per-stage retry limit exceeded for %s (%d attempts)",
                failed_stage, stage_retries,
            )
            return False, 0

        # Set feedback_source so Supervisor and Debugger know what failed
        state["feedback_source"] = failed_stage

        # ── INTELLIGENT ROUTING: Call Supervisor LLM first ────────────────
        action = "retry_stage"
        target_stage = "debugger"
        rollback_stage = "coder"
        hint = ""

        _emit("stage_update", {
            "stage": "supervisor",
            "status": "analyzing",
            "source_stage": failed_stage,
        })
        from veriflow_agent.chat.formatters import STAGE_LABELS as _SL
        _emit("progress_message", {
            "text": f"{_SL.get(failed_stage, failed_stage)} 失败，Supervisor 正在智能分析根因…"
        })

        try:
            from veriflow_agent.graph.graph import node_supervisor as _node_supervisor
            supervisor_updates = _node_supervisor(state)
            if isinstance(supervisor_updates, dict):
                state.update(supervisor_updates)

            decision = (supervisor_updates or {}).get("supervisor_decision") or {}
            action = decision.get("action", "retry_stage")
            target_stage = decision.get("target_stage", "debugger")
            hint = decision.get("hint", "")
            root_cause = decision.get("root_cause", "")
            call_count = (supervisor_updates or {}).get("supervisor_call_count", 0)

            _emit("stage_update", {
                "stage": "supervisor",
                "status": "decision",
                "decision": decision,
                "call_count": call_count,
            })
            _emit("progress_message", {
                "text": f"Supervisor 分析完成: {action} → {target_stage} | {root_cause[:100]}"
            })
            logger.info(
                "Supervisor (partial run) decision for %s: action=%s, target=%s, root_cause=%s",
                failed_stage, action, target_stage, root_cause[:100],
            )

        except Exception as e:
            logger.warning("Supervisor call failed in _auto_heal_stage: %s", e)
            _emit("progress_message", {
                "text": (
                    f"⚠️ Supervisor LLM 调用失败（{e}）。"
                    "无法智能路由，流水线已暂停，等待您的决策。"
                )
            })
            return False, 0

        # ── Handle ABORT: Supervisor gave up ──────────────────────────────
        if action == "abort":
            logger.warning(
                "Supervisor decided to abort for %s: %s",
                failed_stage, (supervisor_updates or {}).get("supervisor_decision", {}).get("root_cause", ""),
            )
            return False, 0

        # ── Handle DEGRADE / CONTINUE: skip or advance ───────────────────
        if action in ("degrade", "continue"):
            skip_target = target_stage or ""
            if skip_target and skip_target in stages_to_run:
                logger.info("Supervisor %s: advancing to %s", action, skip_target)
                return True, stages_to_run.index(skip_target)
            # Advance past the failed stage
            if failed_stage in stages_to_run:
                curr_idx = stages_to_run.index(failed_stage)
                if curr_idx + 1 < len(stages_to_run):
                    return True, curr_idx + 1
            return False, 0

        # ── Determine rollback stage ───────────────────────────────────────
        # Valid pipeline stages that make sense as a rollback destination
        _VALID_ROLLBACK_STAGES = {"architect", "micro_arch", "timing", "coder", "skill_d", "lint", "sim", "synth"}
        if target_stage and target_stage != "debugger" and target_stage in _VALID_ROLLBACK_STAGES:
            # Supervisor gave a direct non-debugger target
            rollback_stage = target_stage
        else:
            # Supervisor wants debugger (or gave an unrecognised stage).
            # Read the rollback hint from state (set by supervisor via target_rollback_stage).
            saved = state.get("target_rollback_stage", "")
            if saved and saved != "debugger" and saved != failed_stage and saved in _VALID_ROLLBACK_STAGES:
                rollback_stage = saved
            else:
                # State has no usable rollback target — Supervisor should have provided one.
                # Pause rather than guess.
                _emit("progress_message", {
                    "text": (
                        "⚠️ Supervisor 未能提供有效的回滚目标，无法继续。"
                        "流水线已暂停，等待您的决策。"
                    )
                })
                logger.warning(
                    "Auto-heal: Supervisor returned target_stage='%s' with no valid "
                    "rollback in state. Pausing for user decision.",
                    target_stage,
                )
                return False, 0

        # Inject supervisor hint into strategy_override for the target stage
        if hint:
            strategy_override = dict(state.get("strategy_override", {}))
            strategy_override[rollback_stage] = hint
            state["strategy_override"] = strategy_override

        # ── ESCALATE_STAGE: jump directly, no debugger needed ─────────────
        if action == "escalate_stage" and target_stage and target_stage != "debugger":
            state["target_rollback_stage"] = rollback_stage
            logger.info("Supervisor escalating directly to %s (skipping debugger)", rollback_stage)
            if rollback_stage in stages_to_run:
                return True, stages_to_run.index(rollback_stage)
            return True, 0  # Before our range — restart from beginning

        # ── RETRY_STAGE with a direct non-debugger target: jump there ─────
        if action == "retry_stage" and target_stage and target_stage != "debugger":
            state["target_rollback_stage"] = rollback_stage
            logger.info("Supervisor retry_stage to %s (skipping debugger)", rollback_stage)
            if rollback_stage in stages_to_run:
                return True, stages_to_run.index(rollback_stage)
            return True, 0

        # ── RETRY_STAGE via DEBUGGER: fix RTL then rollback ───────────────
        state["target_rollback_stage"] = rollback_stage
        _emit("progress_message", {
            "text": f"Debugger 介入，修复 {failed_stage} 错误，目标回滚到 {rollback_stage}…"
        })
        logger.info(
            "Auto-heal: invoking debugger for %s → rollback to %s",
            failed_stage, rollback_stage,
        )

        debugger_failed = False
        debugger_failure_reason = ""
        try:
            debugger_updates = node_debugger(state)
            if isinstance(debugger_updates, dict):
                state.update(debugger_updates)

            debugger_output = debugger_updates.get("debugger_output")
            if debugger_output and hasattr(debugger_output, 'success') and not debugger_output.success:
                debugger_failure_reason = (
                    str(debugger_output.errors) if debugger_output.errors else "unknown error"
                )
                logger.warning("Auto-heal: debugger failed: %s", debugger_failure_reason)
                debugger_failed = True
            elif debugger_output:
                # Debugger succeeded — let it refine the rollback target if supervisor didn't pick one
                supervisor_picked_target = bool(state.get("supervisor_hint"))
                if (
                    not supervisor_picked_target
                    and hasattr(debugger_output, 'metrics')
                    and debugger_output.metrics
                ):
                    llm_target = debugger_output.metrics.get("llm_rollback_target", "")
                    if llm_target and llm_target != "debugger":
                        rollback_stage = llm_target
                        state["target_rollback_stage"] = rollback_stage
                        logger.info("Auto-heal: debugger refined rollback target → %s", rollback_stage)

        except Exception as e:
            debugger_failure_reason = str(e)
            logger.exception("Auto-heal: debugger invocation raised: %s", e)
            debugger_failed = True

        # ── Debugger failed: re-ask Supervisor rather than hardcoding escalation ──
        if debugger_failed:
            _emit("progress_message", {"text": f"Debugger 修复失败，重新请 Supervisor 决策…"})
            # Annotate state so Supervisor sees the debugger already tried and failed
            state["debugger_failure_note"] = (
                f"Debugger was called to fix '{failed_stage}' but failed: {debugger_failure_reason}. "
                f"Do NOT route to debugger again — choose a different strategy (e.g. target_stage=coder or escalate_stage)."
            )
            try:
                from veriflow_agent.graph.graph import node_supervisor as _node_supervisor
                sv2_updates = _node_supervisor(state)
                if isinstance(sv2_updates, dict):
                    state.update(sv2_updates)
                decision2 = (sv2_updates or {}).get("supervisor_decision") or {}
                action2 = decision2.get("action", "abort")
                target2 = decision2.get("target_stage", "")
                root2 = decision2.get("root_cause", "")
                _emit("progress_message", {
                    "text": f"Supervisor 二次决策: {action2} → {target2} | {root2[:80]}"
                })
                logger.info(
                    "Auto-heal: supervisor re-decision after debugger failure: action=%s target=%s",
                    action2, target2,
                )
                if action2 == "abort":
                    return False, 0
                if action2 in ("degrade", "continue"):
                    if target2 and target2 in stages_to_run:
                        return True, stages_to_run.index(target2)
                    if failed_stage in stages_to_run:
                        idx = stages_to_run.index(failed_stage)
                        if idx + 1 < len(stages_to_run):
                            return True, idx + 1
                    return False, 0
                # retry_stage / escalate_stage with a real target
                _VALID = {"architect", "micro_arch", "timing", "coder", "skill_d", "lint", "sim", "synth"}
                new_target = target2 if (target2 and target2 != "debugger" and target2 in _VALID) else "coder"
                state["target_rollback_stage"] = new_target
                _emit("progress_message", {"text": f"Supervisor 指示跳转到 {new_target}…"})
                if new_target in stages_to_run:
                    return True, stages_to_run.index(new_target)
                return True, 0  # target is before our range — restart from beginning
            except Exception as e2:
                logger.exception("Auto-heal: second supervisor call failed: %s — giving up", e2)
                return False, 0

        # Debugger succeeded — resume from rollback_stage
        if rollback_stage in stages_to_run:
            return True, stages_to_run.index(rollback_stage)
        # Rollback target is before our range — restart the whole partial run
        logger.warning(
            "Auto-heal: rollback target '%s' not in remaining stages %s — restarting from index 0",
            rollback_stage, stages_to_run,
        )
        return True, 0

    def stop_pipeline(self, session_id: str = "default") -> None:
        """Signal the running pipeline to stop."""
        self._pipeline_running[session_id] = False
