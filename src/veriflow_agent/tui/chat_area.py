"""Chat area — message display and input."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, RichLog, TextArea


class ChatArea(Vertical):
    """Main chat area with message log and input."""

    DEFAULT_CSS = """
    ChatArea {
        width: 1fr;
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        # Chat log (scrollable message area)
        yield RichLog(
            id="chat-log",
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=True,
            classes="chat-log",
        )

        # Input row
        with Vertical(classes="input-row"):
            yield TextArea(
                id="msg-input",
                classes="msg-input",
            )
            yield Button(
                "Send  ↵",
                variant="success",
                id="send-btn",
                classes="send-btn",
            )
