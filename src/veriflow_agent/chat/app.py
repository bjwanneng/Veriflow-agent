"""Gradio Blocks dashboard for VeriFlow-Agent.

Dark terminal-inspired UI modeled after Claude Code's aesthetic.
Layout: sidebar (pipeline stages + files) + main chat area.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import gradio as gr

from veriflow_agent.chat.handler import PipelineChatHandler

logger = logging.getLogger("veriflow")

_handler = PipelineChatHandler()

# ── Dark theme CSS (Claude Code aesthetic) ──────────────────────────────

DARK_CSS = """
/* === Global === */
.gradio-container {
    background: #1a1b26 !important;
    color: #a9b1d6 !important;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif !important;
    max-width: 100% !important;
    padding: 0 !important;
}

/* === Top bar === */
.vf-topbar {
    background: #16161e !important;
    border-bottom: 1px solid #292e42 !important;
    padding: 12px 24px !important;
    display: flex;
    align-items: center;
    gap: 12px;
}
.vf-topbar-title {
    font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace !important;
    font-size: 16px !important;
    font-weight: 600 !important;
    color: #7aa2f7 !important;
    letter-spacing: 0.5px !important;
}
.vf-topbar-badge {
    background: #292e42 !important;
    color: #565f89 !important;
    font-size: 11px !important;
    padding: 2px 8px !important;
    border-radius: 3px !important;
    font-family: monospace !important;
}

/* === Sidebar === */
.vf-sidebar {
    background: #16161e !important;
    border-right: 1px solid #292e42 !important;
    min-height: calc(100vh - 52px) !important;
    padding: 16px !important;
    overflow-y: auto !important;
}
.vf-sidebar h3 {
    color: #565f89 !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 1.2px !important;
    margin: 20px 0 8px 0 !important;
    font-family: 'Segoe UI', system-ui, sans-serif !important;
}
.vf-sidebar h3:first-child {
    margin-top: 0 !important;
}

/* Pipeline stage row */
.vf-stage {
    display: flex !important;
    align-items: center !important;
    gap: 10px !important;
    padding: 7px 10px !important;
    border-radius: 4px !important;
    margin-bottom: 2px !important;
    font-size: 13px !important;
    transition: background 0.15s !important;
}
.vf-stage:hover {
    background: #1a1b26 !important;
}
.vf-stage-dot {
    width: 8px !important;
    height: 8px !important;
    border-radius: 50% !important;
    flex-shrink: 0 !important;
    background: #292e42 !important;
}
.vf-stage-dot.running {
    background: #e0af68 !important;
    box-shadow: 0 0 6px #e0af6844 !important;
    animation: pulse 1.5s ease-in-out infinite !important;
}
.vf-stage-dot.pass {
    background: #9ece6a !important;
}
.vf-stage-dot.fail {
    background: #f7768e !important;
}
.vf-stage-dot.retry {
    background: #bb9af7 !important;
}
.vf-stage-name {
    color: #a9b1d6 !important;
    flex: 1 !important;
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
    font-size: 12.5px !important;
}
.vf-stage-time {
    color: #565f89 !important;
    font-size: 11px !important;
    font-family: monospace !important;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

/* File listing in sidebar */
.vf-file {
    padding: 4px 10px 4px 28px !important;
    font-size: 12px !important;
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
    color: #565f89 !important;
}
.vf-file::before {
    content: '>' !important;
    margin-right: 6px !important;
    color: #292e42 !important;
}

/* === Main chat area === */
.vf-chat-area {
    background: #1a1b26 !important;
    min-height: calc(100vh - 52px) !important;
}

/* Chat messages */
.chatbot {
    background: transparent !important;
}
.message {
    background: #1a1b26 !important;
    border: none !important;
    font-size: 14px !important;
    line-height: 1.6 !important;
}
.message.user {
    background: #292e42 !important;
    border-radius: 8px !important;
    padding: 10px 14px !important;
}

/* Input area */
.vf-input-area {
    border-top: 1px solid #292e42 !important;
    background: #16161e !important;
    padding: 16px 24px !important;
}
.vf-input-area textarea {
    background: #1a1b26 !important;
    border: 1px solid #292e42 !important;
    color: #a9b1d6 !important;
    border-radius: 6px !important;
    font-size: 14px !important;
    padding: 12px 16px !important;
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
}
.vf-input-area textarea:focus {
    border-color: #7aa2f7 !important;
    box-shadow: 0 0 0 1px #7aa2f744 !important;
}
.vf-input-area textarea::placeholder {
    color: #3b4261 !important;
}

/* Buttons */
.vf-btn-primary {
    background: #7aa2f7 !important;
    color: #1a1b26 !important;
    border: none !important;
    font-weight: 600 !important;
    border-radius: 4px !important;
}
.vf-btn-primary:hover {
    background: #89b4fa !important;
}
.vf-btn-ghost {
    background: transparent !important;
    color: #565f89 !important;
    border: 1px solid #292e42 !important;
    border-radius: 4px !important;
}
.vf-btn-ghost:hover {
    border-color: #3b4261 !important;
    color: #a9b1d6 !important;
}

/* Code blocks in chat */
.vf-chat-area pre {
    background: #16161e !important;
    border: 1px solid #292e42 !important;
    border-radius: 4px !important;
    padding: 12px !important;
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
    font-size: 13px !important;
    overflow-x: auto !important;
}
.vf-chat-area code {
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
    color: #9ece6a !important;
}

/* Markdown tables */
.vf-chat-area table {
    border-collapse: collapse !important;
    width: 100% !important;
    font-size: 13px !important;
}
.vf-chat-area th {
    background: #16161e !important;
    color: #7aa2f7 !important;
    border: 1px solid #292e42 !important;
    padding: 6px 10px !important;
    text-align: left !important;
    font-weight: 600 !important;
}
.vf-chat-area td {
    border: 1px solid #292e42 !important;
    padding: 6px 10px !important;
    color: #a9b1d6 !important;
}

/* Scrollbar */
::-webkit-scrollbar {
    width: 6px !important;
}
::-webkit-scrollbar-track {
    background: #16161e !important;
}
::-webkit-scrollbar-thumb {
    background: #292e42 !important;
    border-radius: 3px !important;
}
::-webkit-scrollbar-thumb:hover {
    background: #3b4261 !important;
}

/* Example chips */
.vf-examples {
    padding: 16px 24px !important;
}
.vf-example-chip {
    background: #1a1b26 !important;
    border: 1px solid #292e42 !important;
    color: #a9b1d6 !important;
    border-radius: 4px !important;
    padding: 8px 14px !important;
    font-size: 13px !important;
    cursor: pointer !important;
    transition: all 0.15s !important;
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
}
.vf-example-chip:hover {
    border-color: #7aa2f7 !important;
    color: #7aa2f7 !important;
    background: #1a1b26 !important;
}

/* Empty state */
.vf-empty-state {
    text-align: center !important;
    padding: 60px 40px !important;
    color: #3b4261 !important;
}
.vf-empty-state h2 {
    color: #565f89 !important;
    font-size: 18px !important;
    font-weight: 400 !important;
    margin-bottom: 8px !important;
    font-family: 'Segoe UI', system-ui, sans-serif !important;
}
.vf-empty-state p {
    font-size: 14px !important;
    color: #3b4261 !important;
}

/* Settings section in sidebar */
.vf-settings-row {
    display: flex !important;
    align-items: center !important;
    justify-content: space-between !important;
    padding: 6px 10px !important;
    font-size: 12px !important;
    color: #565f89 !important;
}
.vf-settings-label {
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
    font-size: 11px !important;
}
.vf-settings-value {
    color: #a9b1d6 !important;
    font-family: monospace !important;
    font-size: 11px !important;
}
"""

# ── Pipeline stages metadata ────────────────────────────────────────────

STAGES = [
    ("architect", "Architecture"),
    ("microarch", "Micro-Arch"),
    ("timing", "Timing Model"),
    ("coder", "RTL Generation"),
    ("skill_d", "Quality Gate"),
    ("lint", "Lint Check"),
    ("sim", "Simulation"),
    ("synth", "Synthesis"),
]

EXAMPLES = [
    "Design a 4-bit ALU supporting ADD, SUB, AND, OR with zero and carry flags, targeting 100MHz",
    "Create a pipelined UART transmitter with configurable baud rate and 8-N-1 format",
    "Design a 32-bit synchronous FIFO with programmable depth and full/empty flags",
    "Implement a PWM controller with configurable frequency and duty cycle registers",
]


def _build_sidebar_stages_html() -> str:
    """Build initial sidebar pipeline stages HTML."""
    rows = []
    for stage_id, label in STAGES:
        rows.append(
            f'<div class="vf-stage">'
            f'  <div class="vf-stage-dot" id="dot-{stage_id}"></div>'
            f'  <span class="vf-stage-name">{label}</span>'
            f'  <span class="vf-stage-time" id="time-{stage_id}"></span>'
            f'</div>'
        )
    return "\n".join(rows)




def _build_interface() -> gr.Blocks:
    """Build the full dashboard layout."""

    with gr.Blocks(
        title="VeriFlow-Agent",
        fill_height=True,
    ) as demo:

        # ── Top bar ──────────────────────────────────────────────
        gr.HTML(
            '<div class="vf-topbar">'
            '  <span class="vf-topbar-title">VeriFlow-Agent</span>'
            '  <span class="vf-topbar-badge">RTL Pipeline</span>'
            "</div>"
        )

        # ── Main content: sidebar + chat ─────────────────────────
        with gr.Row(equal_height=False):
            # Sidebar
            with gr.Column(scale=1, min_width=240, elem_classes=["vf-sidebar"]):
                sidebar_stages = gr.HTML(
                    _build_sidebar_stages_html(), elem_id="vf-sidebar-stages",
                )

                gr.HTML("<h3>Files</h3>")
                files_html = gr.HTML(
                    '<div style="color:#3b4261;font-size:12px;padding:8px 10px;">No files yet</div>',
                    elem_id="vf-files",
                )

                gr.HTML("<h3>Settings</h3>")
                gr.HTML(
                    '<div class="vf-settings-row">'
                    '  <span class="vf-settings-label">LLM</span>'
                    '  <span class="vf-settings-value">claude_cli</span>'
                    "</div>"
                    '<div class="vf-settings-row">'
                    '  <span class="vf-settings-label">Budget</span>'
                    '  <span class="vf-settings-value">1M tokens</span>'
                    "</div>"
                )

                clear_btn = gr.Button(
                    "New Design", elem_classes=["vf-btn-ghost"], size="sm"
                )

            # Chat area
            with gr.Column(scale=4, elem_classes=["vf-chat-area"]):
                chatbot = gr.Chatbot(
                    min_height=500,
                    placeholder="Describe the digital circuit you want to design...",
                    elem_id="vf-chatbot",
                    buttons=["copy"],
                )

                with gr.Row(elem_classes=["vf-input-area"]):
                    msg_input = gr.Textbox(
                        placeholder="Describe your design requirement... (e.g. Design a 4-bit ALU with ADD, SUB, AND, OR)",
                        show_label=False,
                        scale=8,
                        lines=2,
                        autofocus=True,
                    )
                    send_btn = gr.Button(
                        "Send",
                        variant="primary",
                        elem_classes=["vf-btn-primary"],
                        scale=1,
                        min_width=80,
                    )

        # ── Wiring ───────────────────────────────────────────────
        session_id_state = gr.State(value="")

        def _init_session():
            return str(uuid.uuid4())[:8]

        def _send(message, history, sid):
            if not sid:
                sid = str(uuid.uuid4())[:8]
            if not message.strip():
                yield history, sid, _build_sidebar_stages_html(), '<div style="color:#3b4261;font-size:12px;padding:8px 10px;">No files yet</div>'
                return

            # Add user message
            history = history + [{"role": "user", "content": message}]
            yield history, sid, _build_sidebar_stages_html(), '<div style="color:#3b4261;font-size:12px;padding:8px 10px;">No files yet</div>'

            # Stream pipeline response
            full_response = ""
            for chunk in _handler.handle_message(message, history, sid):
                full_response = chunk
                # Update sidebar stages based on response content
                sidebar_html = _update_stages_from_response(full_response)
                files = _update_files_from_session(sid)
                history_updated = history + [{"role": "assistant", "content": full_response}]
                yield history_updated, sid, sidebar_html, files

        def _clear():
            return [], str(uuid.uuid4())[:8], _build_sidebar_stages_html(), '<div style="color:#3b4261;font-size:12px;padding:8px 10px;">No files yet</div>'

        # Send on button click or Enter
        send_btn.click(
            _send,
            inputs=[msg_input, chatbot, session_id_state],
            outputs=[chatbot, session_id_state, sidebar_stages, files_html],
        ).then(lambda: "", outputs=[msg_input])

        msg_input.submit(
            _send,
            inputs=[msg_input, chatbot, session_id_state],
            outputs=[chatbot, session_id_state, sidebar_stages, files_html],
        ).then(lambda: "", outputs=[msg_input])

        clear_btn.click(
            _clear,
            outputs=[chatbot, session_id_state, sidebar_stages, files_html],
        )

        # Load session ID on page load
        demo.load(_init_session, outputs=[session_id_state])

    return demo


def _update_stages_from_response(response: str) -> str:
    """Parse streaming response to update sidebar stage dots.

    Looks for stage completion markers in the markdown to determine
    which stages have passed/failed.
    """
    stage_status = {}
    for stage_id, _ in STAGES:
        stage_status[stage_id] = "pending"

    # Detect stage mentions in the response
    stage_patterns = {
        "architect": ["Architecture Analysis", "Stage 1/", "architect"],
        "microarch": ["Micro-Architecture", "microarch"],
        "timing": ["Timing Model", "Stage 3/", "timing"],
        "coder": ["RTL Code Generation", "Stage 4/", "coder"],
        "skill_d": ["Quality Check", "skill_d"],
        "lint": ["Lint Check", "lint"],
        "sim": ["Simulation", "sim"],
        "synth": ["Synthesis", "synth"],
    }

    for stage_id, patterns in stage_patterns.items():
        for pat in patterns:
            if f"{pat}** — PASSED" in response or f"PASSED" in response and pat in response.lower():
                stage_status[stage_id] = "pass"
            elif f"{pat}** — FAILED" in response or f"FAILED" in response and pat in response.lower():
                stage_status[stage_id] = "fail"

    # Running stage: find the last mentioned stage that isn't complete
    last_running = None
    for stage_id, _ in STAGES:
        for pat in stage_patterns[stage_id]:
            if pat in response:
                if stage_status[stage_id] == "pending":
                    last_running = stage_id

    if last_running:
        stage_status[last_running] = "running"

    # Build HTML
    rows = []
    for stage_id, label in STAGES:
        status = stage_status[stage_id]
        rows.append(
            f'<div class="vf-stage">'
            f'  <div class="vf-stage-dot {status}" id="dot-{stage_id}"></div>'
            f'  <span class="vf-stage-name">{label}</span>'
            f'  <span class="vf-stage-time" id="time-{stage_id}"></span>'
            f'</div>'
        )
    return "\n".join(rows)


def _update_files_from_session(session_id: str) -> str:
    """Check if the handler has generated files for this session."""
    project_dir = _handler._project_dirs.get(session_id)
    if not project_dir or not project_dir.exists():
        return '<div style="color:#3b4261;font-size:12px;padding:8px 10px;">No files yet</div>'

    lines = []
    for subdir in ["workspace/docs", "workspace/rtl", "workspace/tb"]:
        d = Path(project_dir) / subdir
        if d.exists():
            files = sorted(f for f in d.iterdir() if f.is_file())
            if files:
                for f in files:
                    lines.append(
                        f'<div class="vf-file">{f.name}</div>'
                    )
    if not lines:
        return '<div style="color:#3b4261;font-size:12px;padding:8px 10px;">No files yet</div>'
    return "\n".join(lines)


def launch_chat(
    host: str = "0.0.0.0",
    port: int = 7860,
    share: bool = False,
) -> None:
    """Launch the VeriFlow-Agent chat dashboard.

    Args:
        host: Host to bind the server to.
        port: Port to run the chat server on.
        share: Create a public Gradio share URL.
    """
    demo = _build_interface()

    print(f"\n  VeriFlow-Agent Chat UI")
    print(f"  Local:   http://localhost:{port}")
    if share:
        print(f"  Share:   (Gradio public URL will be generated)")
    print()

    demo.launch(
        server_name=host,
        server_port=port,
        share=share,
        show_error=True,
        quiet=False,
        css=DARK_CSS,
    )
