"""Gradio ChatInterface application for VeriFlow-Agent.

Provides a ChatGPT-like interface for the RTL design pipeline.
Users describe their design requirements in natural language,
and the agent runs the full pipeline with real-time progress streaming.
"""

from __future__ import annotations

import logging
import uuid

import gradio as gr

from veriflow_agent.chat.handler import PipelineChatHandler

logger = logging.getLogger("veriflow")

# Global handler instance (thread-safe per user via session_id)
_handler = PipelineChatHandler()

SYSTEM_MESSAGE = (
    "Welcome to **VeriFlow-Agent** \u2014 your RTL design assistant.\n\n"
    "Describe the digital circuit you want to design, and I will:\n"
    "1. Analyze the architecture\n"
    "2. Generate a micro-architecture specification\n"
    "3. Create timing models and testbenches\n"
    "4. Write synthesizable Verilog RTL code\n"
    "5. Run lint checks, simulation, and synthesis\n\n"
    "Example: *\"Design a 4-bit ALU supporting ADD, SUB, AND, OR operations "
    "with zero and carry flags, targeting 100MHz\"*"
)


def _chat_fn(message: str, history: list[dict]):
    """Gradio chat handler — yields streaming responses."""
    session_id = _get_session_id()
    yield from _handler.handle_message(message, history, session_id)


def _get_session_id() -> str:
    """Generate or retrieve a session ID.

    In Gradio, each browser session gets its own event handler context,
    but there's no built-in session ID. We use a per-request UUID
    stored in Gradio's session state.
    """
    return str(uuid.uuid4())[:8]


def _build_interface() -> gr.ChatInterface:
    """Build the Gradio ChatInterface."""
    return gr.ChatInterface(
        fn=_chat_fn,
        type="messages",
        title="\u2699\ufe0f VeriFlow-Agent",
        description=SYSTEM_MESSAGE,
        examples=[
            ["Design a 4-bit ALU supporting ADD, SUB, AND, OR with zero and carry flags, targeting 100MHz"],
            ["Create a pipelined UART transmitter with configurable baud rate and 8-N-1 format"],
            ["Design a 32-bit synchronous FIFO with programmable depth and full/empty/almost-full flags"],
            ["Implement a PWM controller with configurable frequency and duty cycle registers"],
        ],
        theme=gr.themes.Soft(
            primary_hue="blue",
            secondary_hue="slate",
        ),
        retry_btn=None,
        undo_btn=None,
        clear_btn="\U0001f5d1 New Design",
        fill_height=True,
    )


def launch_chat(
    host: str = "0.0.0.0",
    port: int = 7860,
    share: bool = False,
) -> None:
    """Launch the VeriFlow-Agent chat interface.

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
    )
