"""Channel adapter — unified interface from channels to PipelineChatHandler.

Both WebSocket and Telegram channels route through this adapter,
which applies the current config and delegates to the handler.

Yields tagged tuples:
  ("chunk", str)   — text content to display
  ("event", dict)  — structured event (stage_update, etc.)
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from veriflow_agent.chat.handler import PipelineChatHandler
from veriflow_agent.gateway.config import VeriFlowConfig
from veriflow_agent.gateway.log import L, Log

logger = logging.getLogger("veriflow")


class ChannelAdapter:
    """Bridges any channel to PipelineChatHandler."""

    def __init__(
        self,
        handler: PipelineChatHandler,
        config: VeriFlowConfig,
    ) -> None:
        self._handler = handler
        self._config = config

    def refresh_config(self) -> None:
        """Reload config from disk."""
        self._config = VeriFlowConfig.load()

    def process(
        self,
        session_id: str,
        message: str,
    ) -> Generator[tuple[str, Any], None, None]:
        """Process a user message, yielding tagged tuples.

        Yields:
            ("chunk", str)   — incremental text content
            ("event", dict)  — structured event for client notification
        """
        self.refresh_config()
        llm_config = self._config.to_llm_config()
        self._handler.set_llm_config(session_id, llm_config)
        Log.debug(L.STREAM, "Adapter delegating to handler", session=session_id, backend=llm_config.backend)

        pending_events: list[dict] = []

        def event_callback(event_type: str, payload: dict) -> None:
            pending_events.append({"event": event_type, "payload": payload})

        chunk_count = 0
        for chunk in self._handler.handle_message(message, [], session_id, event_callback=event_callback):
            chunk_count += 1
            # Flush any events that were emitted before this chunk
            while pending_events:
                evt = pending_events.pop(0)
                yield ("event", evt)
            yield ("chunk", chunk)

        # Flush remaining events
        while pending_events:
            evt = pending_events.pop(0)
            yield ("event", evt)

        Log.debug(L.STREAM, "Adapter finished", session=session_id, chunks=chunk_count)

    def stop_pipeline(self, session_id: str) -> None:
        self._handler.stop_pipeline(session_id)

    def new_design(self, session_id: str) -> None:
        """Reset handler state for a new design session."""
        self._handler.stop_pipeline(session_id)
