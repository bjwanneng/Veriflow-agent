"""Pipeline sidebar — stage indicators, file listing, settings."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Collapsible,
    Header as THeader,
    Input,
    Select,
    Static,
)

if TYPE_CHECKING:
    pass

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

# Stage status constants
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PASS = "pass"
STATUS_FAIL = "fail"

# Status → Rich style mapping
STATUS_STYLES = {
    STATUS_PENDING: "dim #3b4261",
    STATUS_RUNNING: "bold #e0af68",
    STATUS_PASS: "#9ece6a",
    STATUS_FAIL: "#f7768e",
}

STATUS_ICONS = {
    STATUS_PENDING: "○",
    STATUS_RUNNING: "◉",
    STATUS_PASS: "●",
    STATUS_FAIL: "✕",
}


class StageIndicator(Static):
    """A single pipeline stage indicator row."""

    status: reactive[str] = reactive(STATUS_PENDING)
    duration: reactive[str] = reactive("")

    def __init__(self, stage_id: str, label: str, **kwargs):
        super().__init__(**kwargs)
        self.stage_id = stage_id
        self._label = label

    def watch_status(self, new_status: str) -> None:
        """Re-render when status changes."""
        self._render_stage()

    def watch_duration(self, new_dur: str) -> None:
        """Re-render when duration changes."""
        self._render_stage()

    def _render_stage(self) -> None:
        icon = STATUS_ICONS.get(self.status, "○")
        style = STATUS_STYLES.get(self.status, "dim")
        dur = f"  {self.duration}" if self.duration else ""

        text = Text()
        text.append(f"  {icon} ", style=style)
        text.append(self._label, style=style)
        if dur:
            text.append(dur, style="dim #565f89")
        self.update(text)


class PipelineSidebar(Vertical):
    """Left sidebar with pipeline stages, files, and settings."""

    DEFAULT_CSS = """
    PipelineSidebar {
        width: 28;
        min-width: 24;
        max-width: 36;
        background: $surface-darken-1;
        border-right: solid $primary-darken-3;
        padding: 1;
    }
    PipelineSidebar.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stage_widgets: dict[str, StageIndicator] = {}

    def compose(self) -> ComposeResult:
        # Section: Pipeline
        yield Static(Text("  PIPELINE", style="bold #565f89"), classes="section-header")

        for stage_id, label in STAGES:
            indicator = StageIndicator(stage_id, label, id=f"stage-{stage_id}")
            self._stage_widgets[stage_id] = indicator
            yield indicator

        yield Static("", classes="spacer")

        # Section: Files
        with Collapsible(title="Files", collapsed=True, classes="sidebar-section"):
            yield Static("No files yet", id="files-list")

        # Section: Settings
        with Collapsible(title="Settings", collapsed=True, classes="sidebar-section"):
            yield Select(
                [("Claude CLI", "claude_cli"), ("Anthropic API", "anthropic"), ("OpenAI Compatible", "openai")],
                value="claude_cli",
                id="llm-backend",
                prompt="LLM Backend",
            )
            yield Input(placeholder="API Key", password=True, id="api-key")
            yield Input(placeholder="Base URL (optional)", id="base-url")
            yield Input(placeholder="Model (e.g. claude-sonnet-4-6)", id="model-name")

        yield Static("", classes="spacer")
        yield Button("New Design", variant="primary", id="new-btn", classes="sidebar-btn")

    def reset_stages(self) -> None:
        """Reset all stage indicators to pending."""
        for stage_id in self._stage_widgets:
            widget = self._stage_widgets[stage_id]
            widget.status = STATUS_PENDING
            widget.duration = ""

    def update_stage(self, stage_id: str, status: str, duration: str = "") -> None:
        """Update a single stage indicator."""
        if stage_id in self._stage_widgets:
            self._stage_widgets[stage_id].status = status
            if duration:
                self._stage_widgets[stage_id].duration = duration

    def update_files(self, files: list[str]) -> None:
        """Update the file listing."""
        files_list = self.query_one("#files-list", Static)
        if not files:
            files_list.update("No files yet")
        else:
            text = Text()
            for f in files:
                text.append(f"  > {f}\n", style="#565f89")
            files_list.update(text)
