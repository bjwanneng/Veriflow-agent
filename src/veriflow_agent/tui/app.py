"""VeriFlow-Agent TUI — Claude Code-style terminal interface.

Midnight Forge aesthetic: deep charcoal, electric blue accents,
vertical pipeline rail with animated stage indicators.

Layout:
  ┌──────────────────────────────────────────────────┐
  │  Header: VeriFlow-Agent  RTL Pipeline            │
  ├────────────┬─────────────────────────────────────┤
  │  Sidebar   │  Chat Area                          │
  │  Pipeline  │  ┌───────────────────────────────┐  │
  │  Stages    │  │  RichLog (scrollable)         │  │
  │  --------  │  │  Markdown messages            │  │
  │  Files     │  │  Code blocks                  │  │
  │  --------  │  │  Pipeline progress            │  │
  │  Settings  │  └───────────────────────────────┘  │
  │  [LLM cfg] │  ┌───────────────────────┐ [Send]   │
  │  [New]     │  │  Input (TextArea)     │           │
  │            │  └───────────────────────┘           │
  ├────────────┴─────────────────────────────────────┤
  │  Footer: keybindings                             │
  └──────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from veriflow_agent.chat.llm import LLMConfig, call_llm_stream
from veriflow_agent.chat.handler import PipelineChatHandler
from veriflow_agent.tui.sidebar import PipelineSidebar
from veriflow_agent.tui.chat_area import ChatArea

if TYPE_CHECKING:
    pass

# ── Pipeline stages metadata ────────────────────────────────────────────

STAGES = [
    ("architect", "Architecture", 1),
    ("microarch", "Micro-Arch", 1.5),
    ("timing", "Timing Model", 2),
    ("coder", "RTL Generation", 3),
    ("skill_d", "Quality Gate", 3.5),
    ("lint", "Lint Check", 4),
    ("sim", "Simulation", 4),
    ("synth", "Synthesis", 5),
]


class VeriFlowApp(App):
    """VeriFlow-Agent terminal UI.

    A Claude Code-style chat interface for the RTL design pipeline.
    """

    TITLE = "VeriFlow-Agent"
    SUB_TITLE = "RTL Design Pipeline"

    CSS_PATH = "styles.tss"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+n", "new_design", "New Design"),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar"),
        Binding("enter", "send_message", "Send", show=False),
    ]

    # Reactive state
    llm_backend: reactive[str] = reactive("claude_cli")
    pipeline_running: reactive[bool] = reactive(False)

    def __init__(self):
        super().__init__()
        self._handler = PipelineChatHandler()
        self._session_id = "tui-session"
        self._handler.set_llm_config(
            self._session_id, LLMConfig(backend="claude_cli")
        )

    def compose(self) -> ComposeResult:
        """Build the UI layout."""
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            yield PipelineSidebar(id="sidebar")
            yield ChatArea(id="chat-area")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize on mount."""
        self.query_one("#msg-input", TextArea).focus()
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write(
            Markdown(
                "## Welcome to VeriFlow-Agent\n\n"
                "Describe the digital circuit you want to design.\n\n"
                "Example: *\"Design a 4-bit ALU supporting ADD, SUB, AND, OR "
                'with zero and carry flags, targeting 100MHz\"*\n\n'
                "---\n"
            )
        )

    # ── Message handling ─────────────────────────────────────────────

    class ChatSubmitted(Message):
        """Posted when user submits a chat message."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Handle text changes — not used for submission."""
        pass

    def action_send_message(self) -> None:
        """Send the current message."""
        input_area = self.query_one("#msg-input", TextArea)
        text = input_area.text.strip()
        if not text:
            return

        # Clear input
        input_area.clear()

        # Process the message
        self._process_message(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        btn_id = event.button.id
        if btn_id == "send-btn":
            self.action_send_message()
        elif btn_id == "new-btn":
            self.action_new_design()

    # ── Message processing (async) ───────────────────────────────────

    @work(exclusive=True)
    async def _process_message(self, text: str) -> None:
        """Process user message in a background worker."""
        chat_log = self.query_one("#chat-log", RichLog)
        sidebar = self.query_one(PipelineSidebar)

        # Show user message
        user_text = Text(f"  {text}", style="bold #7aa2f7")
        chat_log.write(user_text)
        chat_log.write(Text(""))  # blank line

        # Stream response
        response_text = ""
        try:
            for chunk in self._handler.handle_message(
                text, [], self._session_id
            ):
                response_text = chunk
                # Write the full response as markdown (replace last assistant block)
                # RichLog doesn't support in-place update, so we append delta
                chat_log.write(Markdown(response_text))
                await asyncio.sleep(0.05)  # Yield to UI

        except Exception as e:
            chat_log.write(
                Markdown(f"\n**Error:** {e}\n\nPlease check your LLM settings.")
            )

    # ── Actions ──────────────────────────────────────────────────────

    def action_new_design(self) -> None:
        """Clear chat and reset for new design."""
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.clear()
        chat_log.write(
            Markdown(
                "## New Design Session\n\n"
                "Describe the digital circuit you want to design.\n\n"
                "---\n"
            )
        )
        # Reset sidebar stages
        sidebar = self.query_one(PipelineSidebar)
        sidebar.reset_stages()

    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility."""
        sidebar = self.query_one("#sidebar")
        sidebar.toggle_class("hidden")


# ── Launch helper ───────────────────────────────────────────────────────


def launch_tui() -> None:
    """Launch the VeriFlow-Agent TUI."""
    app = VeriFlowApp()
    app.run()
