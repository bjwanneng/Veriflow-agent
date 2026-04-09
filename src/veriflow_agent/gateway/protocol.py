"""WebSocket wire protocol — aligned with OpenClaw frame format.

Three frame types:
  - WSRequest:  client → server  (type="req" or "connect")
  - WSResponse: server → client  (type="res")
  - WSEvent:    server → client  (type="event", server-push)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WSRequest:
    """Inbound client frame."""

    type: str = "req"  # "req" | "connect"
    id: str = ""
    method: str = ""  # "send" | "stop" | "new_design" | "status" | "get_config" | "set_config"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict | str) -> WSRequest:
        if isinstance(raw, str):
            raw = json.loads(raw)
        return cls(
            type=raw.get("type", "req"),
            id=raw.get("id", ""),
            method=raw.get("method", ""),
            params=raw.get("params", {}),
        )


@dataclass
class WSResponse:
    """Outbound response frame."""

    type: str = "res"
    id: str = ""
    ok: bool = True
    payload: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"type": self.type, "id": self.id, "ok": self.ok}
        if self.payload is not None:
            d["payload"] = self.payload
        if self.error is not None:
            d["error"] = self.error
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


@dataclass
class WSEvent:
    """Server-push event frame."""

    type: str = "event"
    event: str = ""  # "chunk" | "stage_update" | "error"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "event": self.event, "payload": self.payload}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)
