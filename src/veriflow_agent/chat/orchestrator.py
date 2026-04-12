"""Orchestrator Agent — unified LLM-driven conversation with tool calling.

Replaces the old "classify intent → dispatch to handler" pattern with a
single LLM agent loop that can call tools to inspect projects, start
pipelines, read files, etc.

Architecture:
    User ⟷ OrchestratorAgent (LLM + tool loop)
                  │
                  ├── start_pipeline(requirement)
                  ├── read_file(path)
                  ├── list_files(directory)
                  ├── get_project_status()
                  ├── update_requirement(text)
                  └── scan_context_files()
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any, Callable

from veriflow_agent.chat.llm import LLMConfig, call_llm_stream
from veriflow_agent.chat.project_manager import (
    create_project_from_requirement,
    update_requirement,
)

logger = logging.getLogger("veriflow")

# ── Tool JSON Schema definitions (OpenAI format) ────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "start_pipeline",
            "description": "启动 RTL 设计流水线。⚠️ 只能调用一次，系统会阻止重复调用。当用户需要设计 Verilog 模块时调用。会读取需求文件并执行完整的 8 阶段设计流程（架构→微架构→时序→编码→质量检查→语法检查→仿真→综合）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "requirement": {
                        "type": "string",
                        "description": "整理后的完整需求文本（包含功能、接口、位宽等技术细节）",
                    },
                    "use_context_files": {
                        "type": "boolean",
                        "description": "是否使用 context/ 目录中的参考文件作为需求来源",
                    },
                },
                "required": ["requirement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目目录中的文件内容。用于查看 RTL 代码、spec、报告、时序模型等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径，如 workspace/rtl/alu.v 或 workspace/docs/spec.json",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出项目目录中指定目录下的文件。用于查看有哪些 RTL 文件、报告、文档等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "相对于项目根目录的目录路径，如 workspace/rtl 或 workspace/docs。留空列出根目录。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_status",
            "description": "获取项目当前状态：有哪些文件、pipeline 是否运行过、各阶段完成情况。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_requirement",
            "description": "更新需求文档并重新运行设计流水线。当用户要求修改已有设计时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "modification": {
                        "type": "string",
                        "description": "用户要求的具体修改内容",
                    },
                },
                "required": ["modification"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_context_files",
            "description": "扫描项目 context/ 目录中的参考文档（需求文档、约束文件、参考设计等）。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_pipeline",
            "description": "从指定阶段开始增量执行流水线（不删除已有产出物）。当用户说'继续'、'从lint开始'、'跑仿真'时使用。系统会自动检测哪些阶段已完成（通过检查磁盘文件），从第一个未完成的阶段开始执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_stage": {
                        "type": "string",
                        "description": "起始阶段名称。可选值: architect, microarch, timing, coder, skill_d, lint, sim, synth。留空自动检测。",
                        "enum": ["", "architect", "microarch", "timing", "coder", "skill_d", "lint", "sim", "synth"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_system",
            "description": "诊断Pipeline系统级问题。当遇到ImportError、NameError、DLL加载失败、EDA工具找不到等问题时调用。检查：1) EDA工具是否安装 2) Python导入是否正常 3) 环境变量是否正确。",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_snippet": {
                        "type": "string",
                        "description": "错误信息的片段，用于针对性诊断",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_code_fix",
            "description": "提议修复代码中的问题。当诊断发现是Pipeline代码本身的bug（如缺失导入、错误路径等）时使用。会先展示修复方案给用户确认，不会自动执行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "需要修复的文件路径（相对于项目根目录或绝对路径）",
                    },
                    "issue_description": {
                        "type": "string",
                        "description": "问题描述，如'NameError: categorize_error not defined'",
                    },
                    "proposed_fix": {
                        "type": "string",
                        "description": "具体的修复方案描述，如'在函数开头添加导入语句'",
                    },
                },
                "required": ["file_path", "issue_description", "proposed_fix"],
            },
        },
    },
]


class OrchestratorAgent:
    """Unified LLM orchestrator with tool calling.

    Replaces the old _handle_llm_driven() intent classification pattern.
    The LLM decides what to do by calling tools, not by returning a JSON mode.
    """

    def __init__(self, handler: Any, session_id: str):
        """Args:
            handler: PipelineChatHandler instance (for _run_pipeline etc.)
            session_id: Session identifier
        """
        self.handler = handler
        self.session_id = session_id
        self._pipeline_executed = False  # Hard guard: pipeline can only run once per agent loop
        self._pipeline_result_returned = False  # Hard stop: break loop after pipeline result fed back
        self._pipeline_start_time: float = 0  # Timestamp for freshness verification

        # Shared context for cross-stage intelligence (Orchestrator → Supervisor)
        self._shared_context: dict[str, Any] = {
            "user_requirement_summary": "",
            "clarification_history": [],  # Q&A pairs from architect clarification
            "key_design_decisions": [],  # Critical decisions made during flow
            "user_preferences": {},  # User preferences detected (e.g., always async reset)
            "extracted_parameters": {},  # Technical params (width, depth, frequency, etc.)
        }

    def run(
        self,
        message: str,
        history: list[dict],
        event_callback: Any = None,
    ) -> Generator[str, None, None]:
        """Main agent loop: LLM → tool_call → execute → feed back → repeat.

        Yields incremental markdown strings for the TUI to display.
        """
        config = self.handler.get_llm_config(self.session_id)
        project_dir = self.handler.get_project_dir(self.session_id) or Path(
            self.handler.get_workspace(self.session_id) or "."
        )

        def _emit(event_type: str, payload: dict) -> None:
            if event_callback:
                try:
                    event_callback(event_type, payload)
                except Exception:
                    pass

        # Fast path: check for retry/continue keywords before calling LLM
        # This handles the case where user says "继续" or "重新运行" after pipeline
        retry_keywords = [
            "重试", "retry", "重新运行", "再试一次", "请重试", "重新执行",
            "rerun", "重新生成", "重新做", "再来一次", "再来", "重新来过",
            "重新跑", "重新跑一下", "重新生成rtl", "重新运行生成",
        ]
        continue_keywords = [
            "继续", "continue", "往下跑", "跑下去", "接着跑", "接着来",
            "跑lint", "跑仿真", "跑综合", "运行lint", "运行仿真",
            "run lint", "run sim", "run synth",
        ]
        message_lower = message.lower().strip()
        is_retry = any(kw in message_lower for kw in retry_keywords)
        is_continue = any(kw in message_lower for kw in continue_keywords)

        if is_retry or is_continue:
            # Check if we have a project with previous pipeline run
            if project_dir.exists() and (project_dir / "workspace").exists():
                _emit("progress_message", {"text": "检测到继续/重试请求，检查项目状态…"})

                # Determine which stage to restart from
                from_stage = self._detect_resume_stage(project_dir, message_lower)
                label = "继续执行" if is_continue else "重新执行"

                # ── Scheme B: LLM-validated Fast Path ──────────────────────
                # Show decision to user and request confirmation before executing.
                confirm_event = self.handler.prepare_input_wait(self.session_id)
                _emit("needs_input", {
                    "phase": "confirm_resume",
                    "title": f"{label}设计流水线",
                    "message": f"系统检测到您希望{label}设计流程。",
                    "detected_stage": from_stage,
                    "original_input": message,
                    "question": "是否确认从以下阶段开始执行？",
                    "options": [
                        f"确认，从 {from_stage} 阶段开始",
                        "取消，我需要修改需求",
                        "转给 AI 分析我的完整意图",
                    ],
                    "default": f"确认，从 {from_stage} 阶段开始",
                })

                feedback = self.handler.wait_on_prepared(
                    self.session_id, confirm_event, timeout=300
                )

                if feedback == "__cancelled__":
                    yield "\n\n**已取消。**\n"
                    return

                # Parse user choice
                feedback_lower = (feedback or "").lower().strip()
                confirm_keywords = ["确认", "是", "yes", "y", "开始", "执行", ""]
                cancel_keywords = ["取消", "否", "no", "n", "修改", "取消"]

                is_confirmed = any(kw in feedback_lower for kw in confirm_keywords)
                is_cancelled = any(kw in feedback_lower for kw in cancel_keywords) and not is_confirmed

                # Option 3: Let LLM analyze (user wants to modify or unclear)
                use_llm = "转给" in (feedback or "") or "ai" in feedback_lower or "分析" in feedback_lower

                if is_cancelled or (not is_confirmed and not use_llm):
                    yield "\n\n**已取消。** 请描述您的修改需求，我将重新分析。\n"
                    # Fall through to normal LLM processing with user's feedback as context
                    message = feedback or message  # Use feedback as new message context
                    # Continue to LLM agent loop below (don't return)

                elif use_llm:
                    yield "\n\n**转给 AI 分析…**\n"
                    # Continue to LLM agent loop with full context

                else:  # is_confirmed
                    # User confirmed, execute fast path
                    yield f"\n\n**{label}设计流水线**\n\n从 **{from_stage}** 阶段开始…\n"
                    yield from self.handler._run_pipeline_partial(
                        project_dir, self.session_id,
                        from_stage=from_stage,
                        event_callback=event_callback,
                    )
                    return

        # Build conversation messages
        messages = self._build_messages(message, history, project_dir)

        # Agent loop — max 10 tool-calling rounds
        for turn in range(10):
            if turn == 0:
                _emit("progress_message", {"text": "正在分析…"})

            # Call LLM with tools
            accumulated_text = ""
            tool_calls: list[dict] = []

            try:
                for chunk in call_llm_stream(
                    messages, config,
                    system_prompt=self._build_system_prompt(project_dir),
                    tools=TOOL_SCHEMAS,
                ):
                    if isinstance(chunk, str):
                        # Text content
                        if chunk:
                            accumulated_text += chunk
                            yield chunk
                    elif isinstance(chunk, dict) and chunk.get("type") == "tool_call":
                        tool_calls.append(chunk)
            except Exception as e:
                logger.error("Orchestrator LLM call failed: %s", e)
                yield f"\n\n**LLM 调用失败**: {e}"
                return

            # No tool calls → done (text was already yielded)
            if not tool_calls:
                return

            # Process tool calls — build assistant message with tool_calls
            # Some APIs require content to be a string (not None) when tool_calls present
            assistant_content = accumulated_text if accumulated_text else ""
            messages.append({"role": "assistant", "content": assistant_content})

            # Reconstruct tool_calls in OpenAI format for the message
            openai_tool_calls = []
            for tc in tool_calls:
                openai_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": tc["function"],
                })
            messages[-1]["tool_calls"] = openai_tool_calls

            logger.debug(
                "Assistant message with %d tool_calls, ids=%s",
                len(openai_tool_calls),
                [tc["id"] for tc in openai_tool_calls],
            )

            # Execute each tool and feed results back
            pipeline_just_completed = False
            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                tool_args_str = tc["function"]["arguments"]
                tool_call_id = tc["id"]

                try:
                    tool_args = json.loads(tool_args_str) if tool_args_str else {}
                except json.JSONDecodeError:
                    tool_args = {}

                _emit("progress_message", {"text": f"调用工具: {tool_name}"})
                logger.info("Tool call: %s(%s)", tool_name, tool_args_str[:200])

                result = self._execute_tool(
                    tool_name, tool_args, project_dir, config, _emit,
                )

                # Feed tool result back as a tool message
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(result),
                })

                # Track if pipeline just completed — will need hard stop after this
                if tool_name in ("start_pipeline", "update_requirement"):
                    pipeline_just_completed = True

            # ── Hard stop: if pipeline result was just returned, give LLM
            #    exactly ONE more turn to produce text, then force-break.
            #    This prevents the "LLM ignores instructions and keeps
            #    calling tools" infinite loop.
            if pipeline_just_completed:
                self._pipeline_result_returned = True

                # Check if pipeline result indicates failure
                # If so, don't give LLM more turns — just report the failure
                pipeline_failed = any(
                    "failed" in str(result).lower() or "error" in str(result).lower()
                    for result in [messages[-1].get("content", "")]
                    if isinstance(result, str)
                )
                if pipeline_failed:
                    logger.info(
                        "Pipeline completed with failures. "
                        "Skipping LLM post-pipeline turn to avoid confusion."
                    )
                    yield "\n\n**设计流程已完成（有阶段失败）。** 可以输入「重试」重新执行失败的阶段。"
                    return

                # Continue to next iteration so LLM can produce a text response.
                # If it calls tools instead, the loop will break below.
                continue

            if self._pipeline_result_returned:
                # We already gave the LLM a turn after pipeline result.
                # If it's still calling tools instead of responding, force-break.
                logger.warning(
                    "LLM called tools after pipeline result instead of responding. "
                    "Force-breaking agent loop."
                )
                yield "\n\n**设计流程已完成。** 如果需要查看详细结果，请使用 read_file 或 list_files 工具。"
                return

            # Continue loop — LLM will see tool results and decide next action

        # If we hit 10 rounds, yield a warning
        yield "\n\n**达到最大工具调用轮数，停止。**"

    def _execute_tool(
        self,
        name: str,
        args: dict,
        project_dir: Path,
        config: LLMConfig,
        emit_fn: Callable,
    ) -> str:
        """Execute a single tool call and return the result string."""
        try:
            if name == "start_pipeline":
                # Hard guard: only one pipeline execution per agent loop
                if self._pipeline_executed:
                    # Check if outputs already exist from previous run
                    fs_ok, fs_detail = self._verify_pipeline_outputs(project_dir)
                    if fs_ok:
                        return (
                            "Pipeline 已完成，所有产出物已在磁盘上验证通过。"
                            f"{fs_detail}\n\n"
                            "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告设计结果。"
                            "不要调用 get_project_status、read_file、list_files 等工具。直接回复用户。"
                        )
                    return (
                        "Pipeline 已经执行过一次。\n\n"
                        "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告当前状态和错误信息。"
                        "不要调用 get_project_status、read_file、list_files 等工具。直接回复用户。"
                    )
                self._pipeline_executed = True
                return self._tool_start_pipeline(args, project_dir, emit_fn)
            elif name == "read_file":
                return self._tool_read_file(args, project_dir)
            elif name == "list_files":
                return self._tool_list_files(args, project_dir)
            elif name == "get_project_status":
                return self._tool_get_project_status(project_dir)
            elif name == "update_requirement":
                return self._tool_update_requirement(args, project_dir, emit_fn)
            elif name == "resume_pipeline":
                return self._tool_resume_pipeline(args, project_dir, emit_fn)
            elif name == "scan_context_files":
                return self._tool_scan_context_files(project_dir)
            elif name == "diagnose_system":
                return self._tool_diagnose_system(args)
            elif name == "propose_code_fix":
                return self._tool_propose_code_fix(args, emit_fn)
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            logger.error("Tool %s execution failed: %s", name, e)
            return f"Tool execution error: {e}"

    def _tool_start_pipeline(
        self, args: dict, project_dir: Path, emit_fn: Callable,
    ) -> str:
        """Execute start_pipeline tool — runs the full RTL pipeline.

        Called only once per agent loop (guarded by _execute_tool).
        """
        import shutil

        requirement = args.get("requirement", "")
        use_context = args.get("use_context_files", False)

        if not requirement:
            return "Error: requirement is empty"

        # Handle context files
        if use_context:
            context_result = self._tool_scan_context_files(project_dir)
            if "no context" not in context_result.lower() and "no files" not in context_result.lower():
                # Prepend context info to requirement
                from veriflow_agent.context.scanner import scan_context
                bundle = scan_context(project_dir)
                context_parts = []
                for f in bundle.files:
                    if f.content:
                        context_parts.append(f"<!-- From {f.rel_path} -->\n{f.content}")
                if context_parts:
                    requirement = "\n\n".join(context_parts)

        # ── Clean old workspace outputs to prevent stale-file false positives ──
        workspace = project_dir / "workspace"
        for subdir in ["docs", "rtl", "tb", "logs"]:
            d = workspace / subdir
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

        # Clear stale clarification cache so the new run starts fresh
        cache_path = project_dir / ".veriflow" / "clarification_cache.json"
        if cache_path.exists():
            try:
                cache_path.unlink()
            except Exception:
                pass

        self.handler._project_dirs[self.session_id] = project_dir

        # Write requirement
        req_path = project_dir / "requirement.md"
        req_path.write_text(requirement, encoding="utf-8")

        # Record timestamp BEFORE pipeline starts — for freshness verification
        self._pipeline_start_time = __import__("time").time()

        emit_fn("progress_message", {"text": "需求已确认，启动设计流程…"})

        # Run pipeline — collect output
        pipeline_output = []
        for chunk in self.handler._run_pipeline(
            project_dir, self.session_id, event_callback=emit_fn,
        ):
            pipeline_output.append(chunk)

        full_output = "".join(pipeline_output)

        # ── Primary: file-system verification ──
        fs_ok, fs_detail = self._verify_pipeline_outputs(project_dir)

        # ── Supplementary: string-based diagnostics from output ──
        has_error = "Pipeline 执行异常" in full_output or "Pipeline execution failed" in full_output
        has_stage_fail = "[FAIL]" in full_output or " — FAILED " in full_output

        if has_error or has_stage_fail:
            logger.error(
                "Pipeline failed (string diagnostics). fs_ok=%s, detail=%s",
                fs_ok, fs_detail,
            )
            return (
                f"❌ Pipeline 执行失败 — 部分阶段报告了错误。{fs_detail}\n\n"
                "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告此失败信息，让用户决定下一步。"
            )
        elif not fs_ok:
            logger.warning("Pipeline outputs incomplete: %s", fs_detail)
            return (
                f"⚠️ Pipeline 输出不完整 — {fs_detail}\n\n"
                "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告此信息。"
            )
        else:
            return (
                f"✅ Pipeline 执行成功 — 所有关键产出物已验证通过。{fs_detail}\n\n"
                "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告设计已完成，简要描述生成的文件。"
            )

    def _verify_pipeline_outputs(self, project_dir: Path) -> tuple[bool, str]:
        """Check actual files on disk to determine pipeline completeness.

        Also verifies file freshness (mtime > pipeline_start_time) to avoid
        false positives from stale artifacts of previous runs.

        Returns (success, detail_message).
        """
        import os

        workspace = project_dir / "workspace"
        start_time = getattr(self, "_pipeline_start_time", 0)

        artifacts = {
            "architect": workspace / "docs" / "spec.json",
            "coder": workspace / "rtl",
            "synth": workspace / "docs" / "synth_report.json",
        }

        missing: list[str] = []
        stale: list[str] = []
        for stage, path in artifacts.items():
            if stage == "coder":
                if not path.exists():
                    missing.append("coder: workspace/rtl/ not found")
                else:
                    v_files = list(path.glob("*.v"))
                    if not v_files:
                        missing.append("coder: no RTL files in workspace/rtl/")
                    elif start_time > 0:
                        # Check freshness: at least one .v file should be newer than pipeline start
                        fresh = any(
                            os.path.getmtime(f) >= start_time for f in v_files
                        )
                        if not fresh:
                            stale.append("coder: RTL files are stale (from previous run)")
            else:
                if not path.exists():
                    missing.append(f"{stage}: {path.name} not found")
                elif start_time > 0:
                    if os.path.getmtime(path) < start_time:
                        stale.append(f"{stage}: {path.name} is stale (from previous run)")

        issues = missing + stale
        if issues:
            return False, "Issues: " + "; ".join(issues)
        return True, "All key outputs present and fresh"

    def _tool_read_file(self, args: dict, project_dir: Path) -> str:
        """Read a file from the project directory."""
        path = args.get("path", "")
        if not path:
            return "Error: path is empty"

        file_path = project_dir / path
        if not file_path.exists():
            return f"File not found: {path}"

        # Safety: prevent path traversal
        try:
            file_path.resolve().relative_to(project_dir.resolve())
        except ValueError:
            return "Error: path outside project directory"

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            # Truncate very large files
            if len(content) > 10000:
                return content[:10000] + f"\n... (truncated, total {len(content)} chars)"
            return content
        except Exception as e:
            return f"Error reading file: {e}"

    def _tool_list_files(self, args: dict, project_dir: Path) -> str:
        """List files in a directory."""
        directory = args.get("directory", "")
        dir_path = project_dir / directory if directory else project_dir

        if not dir_path.exists():
            return f"Directory not found: {directory or '.'}"

        try:
            entries = sorted(dir_path.iterdir())
            lines = []
            for entry in entries:
                name = entry.name
                if entry.is_dir():
                    lines.append(f"  {name}/")
                else:
                    size = entry.stat().st_size
                    lines.append(f"  {name} ({size} bytes)")
            if not lines:
                return "Directory is empty"
            return f"Files in {directory or '.'}:\n" + "\n".join(lines[:50])
        except Exception as e:
            return f"Error listing directory: {e}"

    def _tool_get_project_status(self, project_dir: Path) -> str:
        """Get current project status."""
        lines = [f"Project directory: {project_dir}"]

        if not project_dir.exists():
            return "No project directory found"

        # Check requirement
        req_path = project_dir / "requirement.md"
        if req_path.exists():
            req_text = req_path.read_text(encoding="utf-8", errors="replace")
            lines.append(f"requirement.md: {len(req_text)} chars")
        else:
            lines.append("requirement.md: not found")

        # Check workspace outputs
        workspace = project_dir / "workspace"
        if workspace.exists():
            for subdir in ["docs", "rtl", "tb", "logs"]:
                d = workspace / subdir
                if d.exists():
                    files = list(d.iterdir())
                    lines.append(f"workspace/{subdir}/: {len(files)} files")
                    for f in files[:5]:
                        lines.append(f"  - {f.name}")

        # Check context
        context_dir = project_dir / "context"
        if context_dir.exists():
            context_files = list(context_dir.rglob("*"))
            context_files = [f for f in context_files if f.is_file()]
            lines.append(f"context/: {len(context_files)} files")

        return "\n".join(lines)

    def _tool_update_requirement(
        self, args: dict, project_dir: Path, emit_fn: Callable,
    ) -> str:
        """Update requirement and re-run pipeline."""
        import shutil

        modification = args.get("modification", "")
        if not modification:
            return "Error: modification is empty"

        req_path = project_dir / "requirement.md"
        if not req_path.exists():
            return "No existing requirement.md found. Use start_pipeline instead."

        # Clean workspace outputs for fresh re-run
        workspace = project_dir / "workspace"
        for subdir in ["docs", "rtl", "tb", "logs"]:
            d = workspace / subdir
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

        # Clear stale clarification cache
        cache_path = project_dir / ".veriflow" / "clarification_cache.json"
        if cache_path.exists():
            try:
                cache_path.unlink()
            except Exception:
                pass

        update_requirement(project_dir, modification)
        emit_fn("progress_message", {"text": "需求已更新，重新启动 pipeline…"})

        self._pipeline_start_time = __import__("time").time()

        pipeline_output = []
        for chunk in self.handler._run_pipeline(
            project_dir, self.session_id, event_callback=emit_fn,
        ):
            pipeline_output.append(chunk)

        full_output = "".join(pipeline_output)

        # Verify outputs using same logic as start_pipeline
        fs_ok, fs_detail = self._verify_pipeline_outputs(project_dir)
        if not fs_ok:
            return (
                f"⚠️ Pipeline 重新执行后输出不完整: {fs_detail}\n\n"
                "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告此信息。"
            )
        return (
            f"✅ Pipeline 重新执行成功。{fs_detail}\n\n"
            "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告更新后的设计结果。"
        )

    def _tool_scan_context_files(self, project_dir: Path) -> str:
        """Scan context/ directory for reference documents."""
        context_dir = project_dir / "context"
        if not context_dir.is_dir():
            return "No context/ directory found"

        try:
            from veriflow_agent.context.scanner import scan_context
            bundle = scan_context(project_dir)
            if not bundle.files:
                return "No files found in context/"

            lines = [f"Found {len(bundle.files)} context files:"]
            for f in bundle.files:
                cat = f.category.value
                preview = f.content[:100].replace("\n", " ") if f.content else "(empty)"
                lines.append(f"  - {f.rel_path} [{cat}]: {preview}…")
            return "\n".join(lines)
        except Exception as e:
            return f"Error scanning context: {e}"

    def _detect_resume_stage(self, project_dir: Path, message_lower: str = "") -> str:
        """Detect which stage to resume from based on existing files and user hint.

        Checks artifacts on disk to find the first missing stage, then
        allows user message hints to override.
        """
        ws = project_dir / "workspace"

        # Stage → (artifact_path_or_check, stage_name)
        stage_artifacts = [
            ("architect", ws / "docs" / "spec.json"),
            ("microarch", ws / "docs" / "micro_arch.md"),
            ("timing", ws / "docs" / "timing_model.yaml"),
        ]

        # Check doc artifacts
        first_missing = "architect"
        for stage_name, artifact_path in stage_artifacts:
            if not artifact_path.exists():
                first_missing = stage_name
                break
        else:
            # All docs exist — check RTL files
            rtl_dir = ws / "rtl"
            if rtl_dir.exists():
                v_files = [f for f in rtl_dir.glob("*.v") if not f.name.startswith("tb_")]
                if v_files:
                    first_missing = "skill_d"  # RTL exists, check quality
                else:
                    first_missing = "coder"
            else:
                first_missing = "coder"

        # If quality report exists and shows issues, start from skill_d
        quality_report = ws / "docs" / "quality_report.json"
        if first_missing == "skill_d" and quality_report.exists():
            try:
                import json as _json
                report = _json.loads(quality_report.read_text(encoding="utf-8"))
                score = report.get("combined_score", report.get("quality_score", 0))
                if score >= 0.5:
                    first_missing = "lint"  # Quality passed, go to lint
            except Exception:
                pass

        # Check if lint logs exist (indicating lint was already run)
        if first_missing == "lint":
            logs_dir = ws / "logs"
            if logs_dir.exists():
                lint_logs = list(logs_dir.glob("lint*.log"))
                if lint_logs:
                    # Lint was already run — check sim
                    sim_logs = list(logs_dir.glob("sim*.log"))
                    if sim_logs:
                        first_missing = "synth"
                    # else: stay at sim (which comes after lint)

        # User message hints override auto-detection
        stage_hints = {
            "rtl": "coder", "verilog": "coder", "代码": "coder",
            "rtl代码": "coder", "代码生成": "coder",
            "架构": "architect", "规格": "architect", "spec": "architect",
            "微架构": "microarch", "微结构": "microarch",
            "时序": "timing", "timing": "timing",
            "质量": "skill_d", "quality": "skill_d",
            "lint": "lint", "语法": "lint", "语法检查": "lint",
            "仿真": "sim", "simulation": "sim", "simulation": "sim",
            "综合": "synth", "synthesis": "synth",
        }
        for hint, target in stage_hints.items():
            if hint in message_lower:
                return target

        return first_missing

    def _tool_resume_pipeline(
        self, args: dict, project_dir: Path, emit_fn: Callable,
    ) -> str:
        """Resume pipeline from a specific stage — does NOT delete existing outputs.

        Auto-detects the first missing stage if from_stage is not specified.
        """
        from_stage = args.get("from_stage", "")
        if not from_stage:
            from_stage = self._detect_resume_stage(project_dir)

        # Validate stage name
        valid_stages = {"architect", "microarch", "timing", "coder", "skill_d", "lint", "sim", "synth"}
        if from_stage not in valid_stages:
            return f"Error: invalid stage '{from_stage}'. Valid: {', '.join(sorted(valid_stages))}"

        emit_fn("progress_message", {
            "text": f"增量执行：从 {from_stage} 阶段开始（保留已有产出物）…"
        })

        pipeline_output = []
        for chunk in self.handler._run_pipeline_partial(
            project_dir, self.session_id,
            from_stage=from_stage,
            event_callback=emit_fn,
        ):
            pipeline_output.append(chunk)

        full_output = "".join(pipeline_output)

        # Verify outputs
        fs_ok, fs_detail = self._verify_pipeline_outputs(project_dir)

        has_fail = "failed" in full_output.lower() or "失败" in full_output
        if has_fail:
            return (
                f"⚠️ 增量执行完成但有阶段失败。{fs_detail}\n\n"
                "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告执行结果和失败信息。"
            )

        return (
            f"✅ 增量执行完成。{fs_detail}\n\n"
            "【系统指令】你必须立即停止调用任何工具，直接用中文向用户报告执行结果。"
        )

    def _build_messages(
        self, message: str, history: list[dict], project_dir: Path,
    ) -> list[dict]:
        """Build conversation messages with history."""
        messages = []
        # Recent history (last 10 messages)
        for msg in history[-10:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        # Current message
        messages.append({"role": "user", "content": message})
        return messages

    def _build_system_prompt(self, project_dir: Path) -> str:
        """Build the orchestrator system prompt."""
        # Gather project context
        status = self._tool_get_project_status(project_dir)

        return f"""你是 VeriFlow-Agent，一个专业的 RTL 设计助手。你通过工具调用来帮助用户完成 Verilog 硬件设计。

## 当前项目状态
{status}

## 你的能力

你可以通过调用工具来：
1. **start_pipeline** — 当用户需要设计新的硬件模块时，整理需求后启动完整的 RTL 设计流水线
2. **read_file** — 查看项目中已有的文件（RTL 代码、spec、报告等）
3. **list_files** — 列出目录内容
4. **get_project_status** — 查看项目当前状态
5. **update_requirement** — 修改已有设计的需求并重新运行（⚠️ 会删除所有已有产出物！仅在用户明确要修改设计时使用）
6. **resume_pipeline** — 从指定阶段增量执行（不删除已有产出物）。系统自动检测已完成阶段，从第一个未完成阶段开始
7. **scan_context_files** — 扫描 context/ 目录的参考文档
8. **diagnose_system** — 诊断系统级问题（EDA工具、Python导入、环境变量等）
9. **propose_code_fix** — 提议并应用代码修复（需用户确认）

## 行为准则

1. **先理解再行动**: 分析用户意图后再决定是否调用工具
2. **直接回答**: 简单问题（如"什么是 AXI 协议"）直接回复，不需要工具
3. **主动使用工具**: 当用户需要查看文件或启动设计时，主动调用相应工具
4. **整理需求**: 启动流水线前，确保需求文本包含足够的技术细节（功能、接口、位宽等）
5. **上下文文件**: 如果用户说"需求在目录里"或"看 context 目录"，先 scan_context_files 再 start_pipeline
6. **中文回复**: 默认用中文回复用户

## ⚠️ 工具选择规则（极其重要！）

选择错误的工具会破坏用户的工作成果，请务必遵守：

| 用户意图 | 正确工具 | 说明 |
|----------|----------|------|
| "继续"、"往下跑"、"跑lint/仿真/综合" | **resume_pipeline** | 增量执行，不删除已有文件 |
| "修改设计"、"加个端口"、"改一下" | **update_requirement** | 删除已有产出物，全量重跑 |
| "重新设计"、"从零开始"、新需求 | **start_pipeline** | 新项目，全量执行 |
| "检查一下"、"看看代码" | read_file / get_project_status | 只读查看 |

**绝对不要** 在用户说"继续"时调用 update_requirement（会删除所有RTL代码！）或 start_pipeline。

## 关于 start_pipeline

启动流水线前，你需要整理出完整的需求文本，包括：
- 模块功能描述
- 输入/输出接口定义
- 关键参数（位宽、频率、协议等）
- 性能要求（如有）

如果用户需求不够明确，先直接向用户提问，不要急于启动流水线。

## ⚠️ 关键规则：Pipeline 失败处理

如果 start_pipeline 工具返回的结果包含 "FAILED" 或 "terminated early"：
- **绝对不要** 再次调用 start_pipeline — 同样的错误会重复出现
- 使用 read_file 查看 workspace/logs/ 下的错误日志
- 使用 get_project_status 检查哪些阶段完成了
- 把错误信息直接告诉用户，让用户决定下一步
- **如遇到系统级错误**（ImportError、NameError、DLL加载失败等），调用 diagnose_system 工具诊断

## ⚠️ 关键规则：不要过度调用工具

- **start_pipeline 只能调用一次**。系统会强制阻止第二次调用。
- 调用 start_pipeline 后，无论成功或失败，都应该直接向用户报告结果。
- 使用工具获取信息后，立即用中文向用户报告结果。
- 不要反复调用 get_project_status / list_files 来"确认"结果。
"""

    def _tool_diagnose_system(self, args: dict) -> str:
        """Diagnose system-level issues: EDA tools, Python imports, env vars."""
        error_snippet = args.get("error_snippet", "")

        results = []
        results.append("=== 系统诊断报告 ===\n")

        # 1. Check EDA tools
        results.append("## 1. EDA 工具检查\n")
        try:
            from veriflow_agent.tools.eda_utils import find_eda_tool, get_eda_env

            tools = ["iverilog", "vvp", "yosys"]
            for tool in tools:
                path = find_eda_tool(tool)
                if path:
                    results.append(f"  ✓ {tool}: {path}")
                else:
                    results.append(f"  ✗ {tool}: 未找到")

            # Check environment
            env = get_eda_env()
            yosyshq = env.get("YOSYSHQ_ROOT", "")
            if yosyshq:
                results.append(f"  ✓ YOSYSHQ_ROOT={yosyshq}")
            else:
                results.append("  ! YOSYSHQ_ROOT 未设置（如使用 oss-cad-suite 可能需要）")
        except Exception as e:
            results.append(f"  ! EDA检查出错: {e}")

        # 2. Check Python imports
        results.append("\n## 2. Python 导入检查\n")
        critical_imports = [
            "veriflow_agent.graph.state",
            "veriflow_agent.agents.base",
            "veriflow_agent.tools.eda_utils",
            "veriflow_agent.chat.handler",
        ]
        for mod in critical_imports:
            try:
                __import__(mod)
                results.append(f"  ✓ {mod}")
            except ImportError as e:
                results.append(f"  ✗ {mod}: {e}")

        # 3. Check environment variables
        results.append("\n## 3. 环境变量检查\n")
        env_vars = ["YOSYSHQ_ROOT", "IVERILOG_HOME", "PATH"]
        for var in env_vars:
            val = __import__("os").environ.get(var, "")
            if val:
                if var == "PATH":
                    paths = val.split(__import__("os").pathsep)[:3]
                    results.append(f"  ✓ {var}: {len(val.split(__import__('os').pathsep))} entries (showing first 3)")
                    for p in paths:
                        results.append(f"      - {p}")
                else:
                    results.append(f"  ✓ {var}={val}")
            else:
                results.append(f"  ! {var}: 未设置")

        # 4. Analyze error snippet if provided
        if error_snippet:
            results.append("\n## 4. 错误分析\n")
            error_lower = error_snippet.lower()

            if "importerror" in error_lower or "modulenotfound" in error_lower:
                results.append("  检测到 ImportError:")
                results.append("  - 建议: 检查 PYTHONPATH 或重新安装 veriflow-agent")
                results.append("  - 运行: pip install -e .")
            elif "nameerror" in error_lower:
                results.append("  检测到 NameError:")
                results.append("  - 建议: 代码中使用了未定义的变量/函数")
                results.append("  - 可以使用 propose_code_fix 工具提议修复")
            elif "dll" in error_lower or "3221225785" in error_snippet:
                results.append("  检测到 DLL 加载失败:")
                results.append("  - 建议: 检查 oss-cad-suite 的 lib/ 目录是否在 PATH 中")
                results.append("  - 建议: 检查 YOSYSHQ_ROOT 环境变量")
            elif "iverilog" in error_lower and ("not found" in error_lower or "找不到" in error_snippet):
                results.append("  检测到 iverilog 未找到:")
                results.append("  - 建议: 安装 iverilog 或设置 IVERILOG_HOME")
                results.append("  - Windows: https://bleyer.org/icarus/")
                results.append("  - 或安装 oss-cad-suite: https://github.com/YosysHQ/oss-cad-suite-build")

        return "\n".join(results)

    def _tool_propose_code_fix(self, args: dict, emit_fn: Callable) -> str:
        """Propose a code fix and ask user for confirmation before applying."""
        file_path = args.get("file_path", "")
        issue_description = args.get("issue_description", "")
        proposed_fix = args.get("proposed_fix", "")

        if not file_path or not issue_description:
            return "Error: file_path and issue_description are required"

        # Resolve file path
        try:
            # Try as absolute first, then relative to project
            target_path = Path(file_path)
            if not target_path.is_absolute():
                # Search in common locations
                search_paths = [
                    Path(self.handler._project_dirs.get(self.session_id, ".")),
                    Path("src"),
                    Path("."),
                ]
                for base in search_paths:
                    candidate = base / file_path
                    if candidate.exists():
                        target_path = candidate
                        break
        except Exception as e:
            return f"Error resolving path: {e}"

        if not target_path.exists():
            return f"File not found: {file_path} (tried {target_path})"

        # Read current content
        try:
            current_content = target_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        # Show current file info
        lines = [
            f"## 代码修复提议",
            f"",
            f"**文件**: `{target_path}`",
            f"**问题**: {issue_description}",
            f"",
            f"**建议修复**:",
            f"{proposed_fix}",
            f"",
            f"---",
            f"**文件前 50 行预览**:",
            f"```python",
        ]
        preview_lines = current_content.split("\n")[:50]
        lines.extend(preview_lines)
        if len(current_content.split("\n")) > 50:
            lines.append("... (truncated)")
        lines.extend(["```", ""])

        # Request user confirmation
        confirm_event = self.handler.prepare_input_wait(self.session_id)
        emit_fn("needs_input", {
            "phase": "confirm_code_fix",
            "title": "代码修复确认",
            "message": "\n".join(lines),
            "file_path": str(target_path),
            "issue": issue_description,
            "proposed_fix": proposed_fix,
            "question": "是否应用此修复？",
            "options": [
                "确认应用修复",
                "取消（我自己修改）",
            ],
            "default": "确认应用修复",
        })

        feedback = self.handler.wait_on_prepared(self.session_id, confirm_event, timeout=300)

        if feedback == "__cancelled__":
            return "修复已取消"

        feedback_lower = (feedback or "").lower().strip()
        confirm_keywords = ["确认", "应用", "是", "yes", "y", "同意"]
        is_confirmed = any(kw in feedback_lower for kw in confirm_keywords)

        if not is_confirmed:
            return "修复已取消。请手动修改代码后重试。"

        # Apply the fix - this is a simple implementation
        # In a real scenario, we'd use more sophisticated code editing
        try:
            # For now, we support simple additions like adding imports
            if "添加导入" in proposed_fix or "import" in proposed_fix.lower():
                # Add import at the top of the file
                import_line = proposed_fix.split("'")[1] if "'" in proposed_fix else proposed_fix.split("'")[1] if '"' in proposed_fix else None
                if not import_line:
                    # Try to extract from issue description
                    if "categorize_error" in issue_description:
                        import_line = "from veriflow_agent.graph.state import categorize_error, get_rollback_target"
                    else:
                        import_line = "# Fix import not determined"

                # Find insertion point (after existing imports)
                content_lines = current_content.split("\n")
                insert_idx = 0
                for i, line in enumerate(content_lines):
                    if line.startswith("import ") or line.startswith("from "):
                        insert_idx = i + 1

                content_lines.insert(insert_idx, import_line)
                new_content = "\n".join(content_lines)

                # Write back
                target_path.write_text(new_content, encoding="utf-8")

                return f"✅ 修复已应用：在 {target_path} 第 {insert_idx + 1} 行添加了导入语句\n\n添加内容: `{import_line}`"

            return f"⚠️ 已确认，但自动修复逻辑尚未实现此类型的修复。请手动修改 {target_path}"

        except Exception as e:
            return f"修复应用失败: {e}"
