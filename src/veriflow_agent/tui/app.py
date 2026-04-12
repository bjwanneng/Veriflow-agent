"""VeriFlow-Agent — Claude Code-style full-screen terminal UI.

Performance model:
  - LLM tokens buffered in TUILogHandler, flushed every 100ms
  - Only veriflow.stream logger set to DEBUG (not entire veriflow namespace)
  - All UI updates use _DeferredUI + batch_update() to coalesce mutations
  - Pipeline outputs go to files, never printed to UI
  - Chat mode streams tokens to stream-text widget in real-time
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static

from veriflow_agent.chat.handler import PipelineChatHandler
from veriflow_agent.gateway.config import VeriFlowConfig

# ── Palette ───────────────────────────────────────────────────────────────

C_ACCENT  = "#7aa2f7"
C_DIM     = "#565f89"
C_USER    = "#7dcfff"
C_OK      = "#9ece6a"
C_ERR     = "#f7768e"
C_WARN    = "#e0af68"
C_BG      = "#1a1b2e"
C_PANEL   = "#16161e"
C_STREAM  = "#a9b1d6"
C_NARRATE = "#bb9af7"

STAGE_LABELS: dict[str, str] = {
    "architect": "Architecture",
    "microarch":  "Micro-Arch",
    "timing":     "Timing Model",
    "coder":      "RTL Generation",
    "skill_d":    "Quality Gate",
    "lint":       "Lint Check",
    "sim":        "Simulation",
    "synth":      "Synthesis",
    "debugger":   "Debugger",
    "pipeline":   "Pipeline",
}

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Token flush interval — 100ms for responsive streaming without flooding.
_FLUSH_INTERVAL = 0.10


# ── Deferred action queue ────────────────────────────────────────────────

class _DeferredUI:
    """Collects UI mutations from background thread, flushes in one batch.

    Instead of N call_from_thread calls, we post one that applies all
    pending mutations inside self.app.batch_update().
    Thread-safe: actions are appended from worker thread, flushed on UI thread.
    """

    def __init__(self, app: App) -> None:
        self._app = app
        self._actions: list[Callable] = []
        self._lock = threading.Lock()
        self._pending = False  # whether a call_from_thread is already scheduled

    def post(self, fn: Callable) -> None:
        call = False
        with self._lock:
            self._actions.append(fn)
            if not self._pending:
                self._pending = True
                call = True
        if call:
            # call_from_thread fails if called from UI thread.
            # Detect and run directly in that case.
            if threading.current_thread() is threading.main_thread():
                self._flush()
            else:
                self._app.call_from_thread(self._flush)

    def _flush(self) -> None:
        with self._lock:
            actions = self._actions[:]
            self._actions.clear()
            self._pending = False

        if not actions:
            return

        # Use batch_update to coalesce all UI mutations into one layout pass
        with self._app.batch_update():
            for fn in actions:
                try:
                    fn()
                except Exception:
                    pass


# ── Buffered log handler ─────────────────────────────────────────────────

class TUILogHandler(logging.Handler):
    """Intercepts veriflow.stream logs.

    TEXT: tokens are buffered and flushed every _FLUSH_INTERVAL.
    Control events (STAGE_START, TOOL_*) go through immediately but via
    the deferred UI queue to avoid layout thrashing.
    """

    def __init__(self, app: VeriFlowApp, ui: _DeferredUI) -> None:
        super().__init__()
        self._app = app
        self._ui = ui
        self._buf: list[str] = []
        self._buf_lock = threading.Lock()
        self._last_flush: float = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        try:
            if msg.startswith("TEXT:"):
                token = msg[5:]
                with self._buf_lock:
                    self._buf.append(token)
                now = time.monotonic()
                if now - self._last_flush >= _FLUSH_INTERVAL:
                    self._flush()

            elif msg.startswith("STAGE_START:"):
                self._flush()
                payload = json.loads(msg[12:])
                self._ui.post(lambda p=payload: self._app._on_log_event("stage_start", p))

            elif msg.startswith("TOOL_START:"):
                payload = json.loads(msg[11:])
                self._ui.post(lambda p=payload: self._app._on_log_event("tool_start", p))

            elif msg.startswith("TOOL_END:"):
                payload = json.loads(msg[9:])
                self._ui.post(lambda p=payload: self._app._on_log_event("tool_end", p))

            elif msg.startswith("PROGRESS:"):
                payload = json.loads(msg[9:])
                self._ui.post(lambda p=payload: self._app._on_log_event("progress", p))

        except Exception:
            pass

    def _flush(self) -> None:
        with self._buf_lock:
            if not self._buf:
                return
            batch = "".join(self._buf)
            self._buf.clear()
            self._last_flush = time.monotonic()
        self._ui.post(lambda b=batch: self._app._on_log_event("text_batch", b))

    def final_flush(self) -> None:
        self._flush()


# ── App ───────────────────────────────────────────────────────────────────

class VeriFlowApp(App):
    """Full-screen VeriFlow-Agent TUI — Claude Code style."""

    TITLE = "VeriFlow-Agent"

    DEFAULT_CSS = f"""
    Screen {{
        background: {C_BG};
        color: #a9b1d6;
    }}
    Header {{
        background: {C_PANEL};
        color: {C_ACCENT};
        text-style: bold;
    }}
    Footer {{
        background: {C_PANEL};
        color: {C_DIM};
    }}
    #chat-log {{
        height: 1fr;
        width: 100%;
        background: {C_BG};
        border: none;
        padding: 1 4;
        scrollbar-size: 1 1;
        scrollbar-color: {C_DIM} {C_PANEL};
    }}
    #stream-text {{
        height: auto;
        max-height: 12;
        width: 100%;
        background: {C_BG};
        color: {C_STREAM};
        padding: 0 5;
        display: none;
    }}
    #stream-text.active {{
        display: block;
        border-top: dashed {C_DIM};
    }}
    #status-bar {{
        height: 1;
        width: 100%;
        background: {C_PANEL};
        color: {C_WARN};
        padding: 0 4;
    }}
    #msg-input {{
        height: 3;
        width: 100%;
        background: {C_PANEL};
        border-top: solid {C_DIM};
        border-bottom: none;
        border-left: none;
        border-right: none;
        padding: 0 4;
        color: #a9b1d6;
    }}
    #msg-input:focus {{
        border-top: solid {C_ACCENT};
        border-bottom: none;
        border-left: none;
        border-right: none;
    }}
    """

    BINDINGS = [
        Binding("ctrl+q", "quit",        "Quit"),
        Binding("ctrl+c", "cancel",      "Cancel", show=False),
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+y", "save_log",    "SaveLog"),
    ]

    def __init__(self, project_dir: Path | None = None, mode: str = "standard") -> None:
        super().__init__()
        self._project_dir = project_dir
        self._mode = mode
        self._session_id = "tui"
        self._history: list[dict] = []
        self._busy = False
        self._cancel = threading.Event()

        cfg = VeriFlowConfig.load()
        self._cfg = cfg
        self._handler = PipelineChatHandler()
        self._handler.set_llm_config(self._session_id, cfg.to_llm_config())

        effective_dir = project_dir or Path.cwd()
        self._effective_dir = effective_dir
        self._handler.set_workspace(self._session_id, effective_dir)

        # Streaming state
        self._stream_lines: list[str] = []
        self._stage_start_ts: dict[str, float] = {}
        self._spinner_frame: int = 0
        self._is_pipeline: bool = False
        self._token_count: int = 0
        self._current_stage: str = ""
        self._preview_line: str = ""
        self._stage_announced: set[str] = set()
        self._last_token_update_ts: float = 0.0
        self._last_event_ts: float = 0.0
        self._waiting_for_input: bool = False

        # Selection mode state (arrow-key option picker)
        self._selection_options: list[str] = []
        self._selection_index: int = 0
        self._selection_active: bool = False
        self._selection_log_start: int = 0  # track where options start in RichLog

        # Widget caching (populated in on_mount)
        self._cached_log: RichLog | None = None
        self._cached_status: Static | None = None
        self._cached_stream: Static | None = None
        self._cached_input: Input | None = None

        # Deferred UI queue — initialised before log handler
        self._ui = _DeferredUI(self)
        self._log_handler = TUILogHandler(self, self._ui)

    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat-log", highlight=False, markup=False,
                      wrap=True, auto_scroll=True)
        yield Static("", id="stream-text")
        yield Static("", id="status-bar")
        yield Input(placeholder=">  Ask or describe a circuit…", id="msg-input")
        yield Footer()

    def on_mount(self) -> None:
        # ONLY set veriflow.stream to DEBUG — don't flood the entire namespace
        stream_log = logging.getLogger("veriflow.stream")
        stream_log.addHandler(self._log_handler)
        stream_log.setLevel(logging.DEBUG)
        # Keep parent veriflow at INFO to avoid processing thousands of debug msgs
        logging.getLogger("veriflow").setLevel(logging.INFO)

        # Cache widgets for performance (avoid repeated query_one calls)
        self._cached_log = self.query_one("#chat-log", RichLog)
        self._cached_status = self.query_one("#status-bar", Static)
        self._cached_stream = self.query_one("#stream-text", Static)
        self._cached_input = self.query_one("#msg-input", Input)

        self._cached_input.focus()
        model   = self._cfg.model or "default"
        backend = self._cfg.llm_backend
        self._cached_log.write(Text.from_markup(
            f"[bold {C_ACCENT}]VeriFlow-Agent[/] · [{C_DIM}]{backend} · {model} · {self._effective_dir}[/]"
        ))
        self._cached_log.write(Text.from_markup(
            f"[{C_DIM}]Describe your circuit to start. Commands: /new /quit[/]"
        ))

    def on_unmount(self) -> None:
        logging.getLogger("veriflow.stream").removeHandler(self._log_handler)

    # ── Log event handler (UI thread, inside batch_update) ─────────────────

    def _on_log_event(self, kind: str, payload: Any) -> None:
        self._last_event_ts = time.monotonic()

        # Use cached widgets (set in on_mount)
        status = self._cached_status
        log = self._cached_log
        stream = self._cached_stream

        if status is None:
            return

        if kind == "text_batch":
            if self._is_pipeline:
                # Pipeline mode: capture first line as "what it's doing" hint
                self._token_count += len(payload)
                if not self._preview_line:
                    first_line = payload.split("\n", 1)[0].strip()[:80]
                    if first_line:
                        self._preview_line = first_line
                ch = SPINNER[self._spinner_frame % len(SPINNER)]
                self._spinner_frame += 1
                label = STAGE_LABELS.get(self._current_stage, "")
                hint = self._preview_line or "thinking…"
                status.update(Text.from_markup(
                    f"[{C_WARN}]{ch}  {label}  ·  {hint}[/]"
                ))
                # Periodic chat log update (every ~3 seconds)
                now = time.monotonic()
                if (now - self._last_token_update_ts >= 3.0
                        and self._token_count > 0 and log):
                    self._last_token_update_ts = now
                    log.write(Text.from_markup(
                        f"[{C_DIM}]  ↳ generating… {self._token_count} chars[/]"
                    ))
            else:
                # Chat mode: show streaming tokens
                new_lines = payload.splitlines()
                self._stream_lines.extend(new_lines)
                self._stream_lines = self._stream_lines[-10:]
                if stream:
                    stream.update("\n".join(self._stream_lines))
                    if "active" not in stream.classes:
                        stream.add_class("active")

        elif kind == "stage_start":
            stage = payload.get("stage", "") if isinstance(payload, dict) else ""
            label = STAGE_LABELS.get(stage, stage)
            self._stage_start_ts.setdefault(stage, time.time())
            self._current_stage = stage
            self._token_count = 0
            self._preview_line = ""
            ch = SPINNER[self._spinner_frame % len(SPINNER)]
            self._spinner_frame += 1
            status.update(Text.from_markup(f"[{C_WARN}]{ch}  {label}  running…[/]"))
            # Announce stage start in chat log
            if stage and stage not in self._stage_announced and log:
                self._stage_announced.add(stage)
                log.write(Text.from_markup(f"  [{C_NARRATE}]▶ {label}[/]"))

        elif kind == "tool_start":
            tool  = payload.get("tool_name", "tool") if isinstance(payload, dict) else "tool"
            stage = payload.get("stage", "") if isinstance(payload, dict) else ""
            label = STAGE_LABELS.get(stage, stage)
            status.update(Text.from_markup(f"[{C_WARN}]→  {label}  ·  {tool}…[/]"))
            if log:
                log.write(Text.from_markup(f"  [{C_DIM}]  → {tool}…[/]"))

        elif kind == "tool_end":
            ok    = payload.get("success", True) if isinstance(payload, dict) else True
            stage = payload.get("stage", "") if isinstance(payload, dict) else ""
            label = STAGE_LABELS.get(stage, stage)
            col   = C_OK if ok else C_ERR
            icon  = "✓" if ok else "✗"
            status.update(Text.from_markup(f"[{col}]{icon}  {label}  done[/]"))

        elif kind == "progress":
            msg_text = payload.get("message", "") if isinstance(payload, dict) else ""
            if msg_text:
                status.update(Text.from_markup(f"[{C_NARRATE}]{msg_text}[/]"))
                if log:
                    log.write(
                        Text.from_markup(f"[{C_DIM}]{msg_text}[/]")
                    )

    # ── Stage event handler (UI thread, inside batch_update) ───────────────

    def _on_stage_event(self, event_type: str, payload: dict) -> None:
        # Use cached widgets (set in on_mount)
        log = self._cached_log
        status = self._cached_status
        stream = self._cached_stream
        inp = self._cached_input

        if log is None:
            return

        if event_type == "progress_message":
            text = payload.get("text", "")
            if text:
                log.write(Text.from_markup(f"[{C_DIM}]{text}[/]"))

        elif event_type == "stage_update":
            stage   = payload.get("stage", "")
            sstatus = payload.get("status", "")
            label   = STAGE_LABELS.get(stage, stage)

            if stage == "pipeline" and sstatus == "started":
                self._is_pipeline = True

            elif sstatus in ("started", "running") and stage != "pipeline":
                self._stage_start_ts.setdefault(stage, time.time())
                ch = SPINNER[self._spinner_frame % len(SPINNER)]
                self._spinner_frame += 1
                if status:
                    status.update(Text.from_markup(f"[{C_WARN}]{ch}  {label}  running…[/]"))
                # Announce stage start in chat log (if not already announced)
                if stage not in self._stage_announced:
                    self._stage_announced.add(stage)
                    log.write(Text.from_markup(f"[{C_NARRATE}]➜ {label}…[/]"))

            elif sstatus in ("pass", "fail", "error") and stage != "pipeline":
                # Clear stream preview
                self._stream_lines.clear()
                if stream:
                    stream.update("")
                    stream.remove_class("active")

                # Stage result — one line with duration
                dur = time.time() - self._stage_start_ts.pop(stage, time.time())
                ok  = sstatus == "pass"
                col = C_OK if ok else C_ERR
                icon = "✓" if ok else "✗"
                log.write(Text.from_markup(
                    f"[{col}]{icon}[/] [{C_DIM}]{label} ({dur:.1f}s)[/]"
                ))

                # Show errors and warnings on failure
                if not ok:
                    errors = payload.get("errors", [])
                    warnings = payload.get("warnings", [])
                    for err in errors[:5]:
                        err_text = str(err)[:200]
                        log.write(Text.from_markup(f"  [{C_ERR}]→ {err_text}[/]"))
                    for warn in warnings[:3]:
                        warn_text = str(warn)[:200]
                        log.write(Text.from_markup(f"  [{C_WARN}]! {warn_text}[/]"))

                # Show artifacts on success
                if ok:
                    artifacts = payload.get("artifacts", [])
                    if artifacts:
                        arts = ", ".join(Path(str(a)).name for a in artifacts[:5])
                        log.write(Text.from_markup(f"  [{C_DIM}]{arts}[/]"))

                if status:
                    status.update("")

            elif stage == "pipeline" and sstatus in ("done", "error"):
                self._is_pipeline = False
                self._stream_lines.clear()
                if stream:
                    stream.update("")
                    stream.remove_class("active")
                if status:
                    status.update("")

            elif sstatus == "retry":
                log.write(Text.from_markup(
                    f"[{C_WARN}]↻ Debugger retry: {payload.get('source', '')}[/]"
                ))

        elif event_type == "needs_input":
            phase = payload.get("phase", "question")
            self._waiting_for_input = True

            if phase == "draft":
                # Show draft overview, then wait briefly
                draft = payload.get("draft", "")
                if draft:
                    log.write(Text.from_markup(f"[{C_DIM}]{draft[:600]}[/]"))
                log.write(Text.from_markup(f"[{C_NARRATE}]Preparing questions…[/]"))
                if inp:
                    inp.focus()
                # Auto-confirm draft view — user doesn't need to type anything
                self._waiting_for_input = False
                self._handler.provide_user_input(self._session_id, "ok")

            elif phase == "question":
                # Show one question with selectable options
                question = payload.get("question", "")
                options = payload.get("options", [])
                default = payload.get("default", "")
                idx = payload.get("index", 1)
                total = payload.get("total", 1)

                log.write(Text.from_markup(
                    f"[{C_NARRATE}]{idx}/{total} {question}[/]"
                ))

                # Enter selection mode for arrow-key navigation
                if options:
                    self._selection_options = options
                    self._selection_index = 0
                    for i, opt in enumerate(options):
                        if opt == default:
                            self._selection_index = i
                            break
                    self._selection_active = True
                    self._render_selection_in_status()
                    log.write(Text.from_markup(
                        f"[{C_DIM}]↑↓ select · Enter confirm · or type custom answer[/]"
                    ))
                else:
                    log.write(Text.from_markup(
                        f"[{C_DIM}]Enter your answer:[/]"
                    ))

                if inp:
                    inp.focus()

            elif phase == "stage_confirm":
                # Stage completion confirmation
                stage = payload.get("stage", "")
                next_stage = payload.get("next_stage", "")
                stage_label = STAGE_LABELS.get(stage, stage)
                next_label = STAGE_LABELS.get(next_stage, next_stage)
                completed_stages = payload.get("completed", [])
                failed_stages = payload.get("failed", [])

                log.write(Text.from_markup(
                    f"[{C_OK}]✓[/] [{C_DIM}]{stage_label} complete[/]"
                ))

                # Show failed stages if any
                if failed_stages:
                    for fs in failed_stages:
                        fl = STAGE_LABELS.get(fs, fs)
                        log.write(Text.from_markup(
                            f"  [{C_ERR}]✗ {fl}[/]"
                        ))

                log.write(Text.from_markup(
                    f"[{C_NARRATE}]Next: {next_label}[/]"
                ))
                log.write(Text.from_markup(
                    f"[{C_DIM}]Press Enter to continue, or type feedback[/]"
                ))
                if status:
                    status.update(
                        Text.from_markup(
                            f"[{C_WARN}]⏸ {stage_label} done · Enter → {next_label}[/]"
                        )
                    )
                if inp:
                    inp.focus()

            elif phase == "stage_feedback":
                # Show LLM investigation response, then wait for next input
                answer = payload.get("answer", "")
                has_options = payload.get("options", False)
                if answer:
                    # Split answer into lines, write each
                    for line in answer.split("\n"):
                        if line.strip():
                            log.write(Text.from_markup(
                                f"[{C_STREAM}]{line}[/]"
                            ))
                if has_options:
                    log.write(Text.from_markup(
                        f"[{C_DIM}]Enter feedback or type continue/stop[/]"
                    ))
                else:
                    log.write(Text.from_markup(
                        f"[{C_DIM}]Enter=continue · rerun=restart · stop=halt · or ask questions[/]"
                    ))
                if status:
                    status.update(
                        Text.from_markup(f"[{C_WARN}]⏳ waiting…[/]")
                    )
                if inp:
                    inp.focus()

            elif phase == "confirm_resume":
                # Orchestrator fast-path: confirm which stage to resume from
                title = payload.get("title", "Resume Pipeline")
                detected_stage = payload.get("detected_stage", "")
                question = payload.get("question", "Confirm?")
                options = payload.get("options", [])
                default = payload.get("default", "")
                stage_label = STAGE_LABELS.get(detected_stage, detected_stage)

                log.write(Text.from_markup(
                    f"[{C_NARRATE}]{title}[/]"
                ))
                log.write(Text.from_markup(
                    f"[{C_NARRATE}]Detected resume stage: [bold]{stage_label}[/][/]"
                ))
                log.write(Text.from_markup(
                    f"[{C_DIM}]{question}[/]"
                ))

                # Enter selection mode for options
                if options:
                    self._selection_options = options
                    self._selection_index = 0
                    for i, opt in enumerate(options):
                        if opt == default:
                            self._selection_index = i
                            break
                    self._selection_active = True
                    self._render_selection_in_status()
                    log.write(Text.from_markup(
                        f"[{C_DIM}]↑↓ select · Enter confirm[/]"
                    ))
                else:
                    log.write(Text.from_markup(
                        f"[{C_DIM}]Press Enter to confirm[/]"
                    ))

                if inp:
                    inp.focus()

            elif phase == "confirm_code_fix":
                # Self-healing: user confirmation for code fix
                title = payload.get("title", "Code Fix")
                message = payload.get("message", "")
                question = payload.get("question", "Confirm?")
                options = payload.get("options", [])
                default = payload.get("default", "")
                file_path = payload.get("file_path", "")

                log.write(Text.from_markup(f"[{C_NARRATE}]{title}[/]"))
                # Show the fix proposal (already formatted in message)
                for line in message.split("\n"):
                    if line.strip():
                        log.write(Text.from_markup(f"[{C_DIM}]{line}[/]"))

                log.write(Text.from_markup(f"\n[{C_WARN}]{question}[/]"))

                # Enter selection mode
                if options:
                    self._selection_options = options
                    self._selection_index = 0
                    for i, opt in enumerate(options):
                        if opt == default:
                            self._selection_index = i
                            break
                    self._selection_active = True
                    self._render_selection_in_status()
                    log.write(Text.from_markup(
                        f"[{C_DIM}]↑↓ select · Enter confirm[/]"
                    ))
                else:
                    log.write(Text.from_markup(
                        f"[{C_DIM}]Type yes/no or press Enter to confirm[/]"
                    ))

                if inp:
                    inp.focus()

    # ── Input ─────────────────────────────────────────────────────────────

    def _render_selection_in_status(self) -> None:
        """Render the option selector in the stream-text widget.

        Uses the stream-text area (above the input) as a live selection panel.
        This avoids RichLog accumulation on arrow key navigation.
        """
        stream = self._cached_stream
        if not stream or not self._selection_options:
            return
        lines = []
        for i, opt in enumerate(self._selection_options):
            if i == self._selection_index:
                lines.append(f"[bold {C_ACCENT}]❯[/] [{C_USER}]{opt}[/]")
            else:
                lines.append(f"  [{C_DIM}]{opt}[/]")
        stream.update(Text.from_markup("\n".join(lines)))
        stream.add_class("active")

    def on_key(self, event) -> None:
        """Handle keyboard input — selection mode + Enter."""
        # Arrow key navigation in selection mode
        if self._selection_active and self._waiting_for_input:
            if event.key == "up":
                self._selection_index = max(0, self._selection_index - 1)
                self._refresh_selection_display()
                event.prevent_default()
                return
            elif event.key == "down":
                n = len(self._selection_options)
                self._selection_index = min(n - 1, self._selection_index + 1)
                self._refresh_selection_display()
                event.prevent_default()
                return
            elif event.key == "enter":
                # Confirm selection
                selected = self._selection_options[self._selection_index]
                self._selection_active = False
                self._waiting_for_input = False
                self._clear_stream()
                log = self._cached_log
                if log:
                    log.write(Text.from_markup(
                        f"[bold {C_USER}]>[/] [{C_USER}]{selected}[/]"
                    ))
                    log.write(Text(""))
                self._handler.provide_user_input(self._session_id, selected)
                status = self._cached_status
                if status:
                    status.update("")
                event.prevent_default()
                return
            elif event.key == "tab":
                # Tab also confirms
                selected = self._selection_options[self._selection_index]
                self._selection_active = False
                self._waiting_for_input = False
                self._clear_stream()
                log = self._cached_log
                if log:
                    log.write(Text.from_markup(
                        f"[bold {C_USER}]>[/] [{C_USER}]{selected}[/]"
                    ))
                    log.write(Text(""))
                self._handler.provide_user_input(self._session_id, selected)
                status = self._cached_status
                if status:
                    status.update("")
                event.prevent_default()
                return
            elif event.character and event.character.isprintable():
                # User starts typing — exit selection mode and let Input handle it
                self._selection_active = False
                self._clear_stream()
                # Don't prevent default — let the character go to Input widget
                return

        if event.key == "enter":
            inp = self._cached_input
            if inp and inp.has_focus:
                self._selection_active = False
                self._do_send()
                event.prevent_default()

    def _refresh_selection_display(self) -> None:
        """Update the option selector on arrow key navigation."""
        self._render_selection_in_status()

    def _clear_stream(self) -> None:
        """Clear the stream-text widget."""
        stream = self._cached_stream
        if stream:
            stream.update("")
            stream.remove_class("active")

    def _do_send(self) -> None:
        inp = self._cached_input
        log = self._cached_log
        status = self._cached_status

        if inp is None:
            return

        text = inp.value.strip()
        inp.value = ""

        # Clear selection mode if active (user typed custom text)
        self._selection_active = False

        # Handle waiting-for-input (architect Q&A or stage confirm)
        if self._waiting_for_input:
            # Empty text = Enter = "continue" for stage confirm
            if not text:
                text = "continue"
            self._waiting_for_input = False
            if log:
                if text != "continue":
                    log.write(Text.from_markup(f"[bold {C_USER}]>[/] [{C_USER}]{text}[/]"))
                log.write(Text(""))
            self._handler.provide_user_input(self._session_id, text)
            if status:
                status.update("")
            return

        if not text:
            return

        if self._busy:
            return

        if text.lower() in ("/new", "/reset"):
            self.action_new_session()
            return
        if text.lower() in ("/quit", "/exit"):
            self.action_quit()
            return

        # Write user message directly on UI thread (instant, no queue)
        if log:
            log.write(Text.from_markup(f"[bold {C_USER}]>[/] [{C_USER}]{text}[/]"))

        self._run_message(text)

    def action_cancel(self) -> None:
        """Cancel current operation and reset UI state."""
        self._cancel.set()
        self._waiting_for_input = False
        self._selection_active = False
        self._handler.cancel_input_wait(self._session_id)
        self._busy = False
        self._stream_lines.clear()

        stream = self._cached_stream
        status = self._cached_status
        log = self._cached_log
        inp = self._cached_input

        if stream:
            stream.update("")
            stream.remove_class("active")
        if status:
            status.update("")
        if log:
            log.write(Text.from_markup(f"  [{C_WARN}]Cancelled.[/]"))
        if inp:
            inp.focus()

    def action_new_session(self) -> None:
        self._history.clear()
        self._stream_lines.clear()
        self._stage_announced.clear()
        self._handler._cleanup_session_input_state(self._session_id)
        log = self._cached_log
        if log:
            log.clear()
            log.write(Text.from_markup(f"[{C_DIM}]Session reset.[/]"))

    def action_save_log(self) -> None:
        """Save chat log to file so user can copy from it."""
        log = self._cached_log
        status = self._cached_status
        try:
            if not log:
                return
            out_path = self._effective_dir / "veriflow_chat_log.txt"
            # RichLog.lines contains plain text lines
            lines = []
            for line in log.lines:
                lines.append(str(line))
            out_path.write_text("\n".join(lines), encoding="utf-8")
            if status:
                status.update(
                    Text.from_markup(f"[{C_OK}]Log saved to {out_path}[/]")
                )
        except Exception as e:
            if status:
                try:
                    status.update(
                        Text.from_markup(f"[{C_ERR}]Save failed: {e}[/]")
                    )
                except Exception:
                    pass

    # ── Runner (background thread) ────────────────────────────────────────

    @work(thread=True, exclusive=True)
    def _run_message(self, text: str) -> None:
        self._busy = True
        self._cancel.clear()
        self._stream_lines.clear()
        self._stage_start_ts.clear()
        self._is_pipeline = False
        self._stage_announced.clear()
        self._last_token_update_ts = 0.0
        self._last_event_ts = time.monotonic()

        def event_callback(event_type: str, payload: dict) -> None:
            self._ui.post(lambda e=event_type, p=payload: self._on_stage_event(e, p))

        chat_accumulated = ""
        pipeline_chunk_count = 0
        last_chat_stream_ts: float = 0.0
        error = ""

        try:
            for chunk in self._handler.handle_message(
                text, self._history, self._session_id,
                event_callback=event_callback,
            ):
                if self._cancel.is_set():
                    break

                if self._is_pipeline:
                    pipeline_chunk_count += 1
                else:
                    if len(chunk) >= len(chat_accumulated):
                        chat_accumulated = chunk
                    else:
                        chat_accumulated += chunk
                    # Stream chat tokens to stream-text widget (throttled ~150ms)
                    now = time.monotonic()
                    if now - last_chat_stream_ts >= 0.15 and chat_accumulated:
                        last_chat_stream_ts = now
                        snap = chat_accumulated
                        self._ui.post(lambda s=snap: self._update_chat_stream(s))

        except Exception as e:
            error = str(e)

        # Final flush
        self._log_handler.final_flush()

        # Cleanup — one batch for all cleanup mutations
        self._ui.post(self._cleanup_after_run)

        # Final message
        if error:
            self._ui.post(lambda: self.query_one("#chat-log", RichLog).write(
                Text.from_markup(f"[{C_ERR}]Error: {error}[/]")
            ))
        elif not self._is_pipeline and chat_accumulated:
            self._ui.post(lambda: self.query_one("#chat-log", RichLog).write(
                Text(chat_accumulated)
            ))
            self._history.append({"role": "user",      "content": text})
            self._history.append({"role": "assistant", "content": chat_accumulated})
        elif pipeline_chunk_count > 0:
            # Check if any stage actually completed — if not, show warning
            # instead of misleading "Pipeline complete"
            stage_ok = self._stage_announced  # stages that were announced
            has_completed_stage = len(stage_ok) > 0
            if has_completed_stage:
                self._ui.post(lambda: self.query_one("#chat-log", RichLog).write(
                    Text.from_markup(
                        f"[{C_OK}]✓[/] [{C_DIM}]Pipeline complete. "
                        f"Output: {self._effective_dir}[/]"
                    )
                ))
            else:
                self._ui.post(lambda: self.query_one("#chat-log", RichLog).write(
                    Text.from_markup(
                        f"[{C_WARN}]⚠[/] [{C_DIM}]Pipeline ended but no stages completed. "
                        f"Check logs in workspace/logs/[/]"
                    )
                ))
            self._history.append({"role": "user",      "content": text})
            self._history.append({"role": "assistant", "content": "[pipeline completed]" if has_completed_stage else "[pipeline incomplete]"})

        self._busy = False

    def _cleanup_after_run(self) -> None:
        """Reset stream/status widgets — called inside batch_update."""
        self._stream_lines.clear()
        stream = self.query_one("#stream-text", Static)
        stream.update("")
        stream.remove_class("active")
        self.query_one("#status-bar", Static).update("")
        self._stage_announced.clear()

    def _update_chat_stream(self, text: str) -> None:
        """Update the stream-text widget with chat response preview."""
        try:
            stream = self.query_one("#stream-text", Static)
            lines = text.splitlines()
            recent = "\n".join(lines[-10:])
            stream.update(recent)
            if "active" not in stream.classes:
                stream.add_class("active")
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────

def launch_tui(project_dir: Path | None = None, mode: str = "standard") -> None:
    """Launch the full-screen VeriFlow-Agent TUI."""
    VeriFlowApp(project_dir=project_dir, mode=mode).run()
