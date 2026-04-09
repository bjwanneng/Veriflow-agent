"""Session management for the Gateway.

Tracks active WebSocket connections and per-session state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

logger = logging.getLogger("veriflow")


@dataclass
class Session:
    """Per-connection session state."""

    session_id: str
    channel: str  # "websocket" | "telegram"
    created_at: datetime = field(default_factory=datetime.now)
    pipeline_running: bool = False
    project_dir: Path | None = None


class SessionManager:
    """Manages active sessions and WebSocket connections."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._ws_connections: dict[str, WebSocket] = {}

    def get_or_create(
        self,
        session_id: str | None = None,
        channel: str = "websocket",
    ) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]

        sid = session_id or uuid4().hex[:8]
        session = Session(session_id=sid, channel=channel)
        self._sessions[sid] = session
        logger.info("Session created: %s (channel=%s)", sid, channel)
        return session

    def register_ws(self, session_id: str, ws: WebSocket) -> None:
        self._ws_connections[session_id] = ws

    def unregister_ws(self, session_id: str) -> None:
        self._ws_connections.pop(session_id, None)

    def get_ws(self, session_id: str) -> WebSocket | None:
        return self._ws_connections.get(session_id)

    def get_by_telegram_chat(self, chat_id: int) -> Session:
        sid = f"telegram-{chat_id}"
        return self.get_or_create(session_id=sid, channel="telegram")

    def set_running(self, session_id: str, running: bool) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.pipeline_running = running

    def get_status(self) -> dict[str, Any]:
        return {
            "active_sessions": len(self._sessions),
            "ws_connections": len(self._ws_connections),
            "sessions": [
                {
                    "session_id": s.session_id,
                    "channel": s.channel,
                    "pipeline_running": s.pipeline_running,
                    "created_at": s.created_at.isoformat(),
                }
                for s in self._sessions.values()
            ],
        }
