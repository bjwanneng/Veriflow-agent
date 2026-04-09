"""VeriFlow-Agent Gateway — FastAPI server with WebSocket control plane.

Aligned with OpenClaw's Gateway architecture:
  - WebSocket /ws  → control plane (req/res/event wire protocol)
  - REST /api/*    → config & status endpoints
  - Static /       → WebChat HTML client

New in this version:
  - WSLogHandler: real-time log streaming to connected WebSocket clients
  - detect_tools WS method: EDA tool probe results
  - test_claude_cli WS method: Claude CLI connectivity test
  - set_log_level WS method: dynamic log-level change
  - next_stage WS method: step-mode pipeline continuation signal
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from veriflow_agent.chat.handler import PipelineChatHandler
from veriflow_agent.gateway.adapter import ChannelAdapter
from veriflow_agent.gateway.config import VeriFlowConfig
from veriflow_agent.gateway.log import L, Log
from veriflow_agent.gateway.protocol import WSEvent, WSRequest, WSResponse
from veriflow_agent.gateway.session import SessionManager
from veriflow_agent.gateway.tools_detector import detect_tools, test_claude_cli

logger = logging.getLogger("veriflow")

# Sentinel for sync→async generator bridge
_DONE = object()

# ── Thread-local session context ─────────────────────────────────────────
# Allows WSLogHandler (called from executor threads) to route events to the
# specific WebSocket session that triggered the pipeline, not broadcast to all.

_session_local = threading.local()


def set_current_session(session_id: str) -> None:
    """Set active session_id on the current thread (call before run_in_executor)."""
    _session_local.session_id = session_id


def get_current_session() -> str | None:
    """Return the session_id stored on this thread (None if not set)."""
    return getattr(_session_local, "session_id", None)

STATIC_DIR = Path(__file__).parent / "static"

# ── Shared singletons ────────────────────────────────────────────────────

_sessions = SessionManager()
_handler = PipelineChatHandler()

# Active WebSocket connections for log broadcasting
_active_ws: dict[str, WebSocket] = {}   # session_id → websocket
_active_ws_lock = asyncio.Lock()

# Per-session step-mode continuation events
_step_events: dict[str, asyncio.Event] = {}  # session_id → Event
_run_all_flags: dict[str, bool] = {}          # session_id → run_all flag

# Reference to the main asyncio event loop — needed by WSLogHandler which
# is called from executor threads where asyncio.get_event_loop() fails.
_main_loop: asyncio.AbstractEventLoop | None = None


def get_adapter() -> ChannelAdapter:
    return ChannelAdapter(_handler, VeriFlowConfig.load())


# ── Stream record parser ─────────────────────────────────────────────────

def _parse_stream_record(msg: str) -> tuple[str, dict]:
    """Parse a prefixed veriflow.stream log message → (event_type, payload).

    _consume_streaming() prefixes every record it emits:
      "TEXT:<chunk>"        → ("llm_stream",  {"content": chunk})
      "TOOL_START:<json>"   → ("tool_start",  {...})
      "TOOL_END:<json>"     → ("tool_end",    {...})
      "STAGE_START:<json>"  → ("stage_start", {...})
    Legacy plain-text records (no prefix) fall back to "llm_stream".
    """
    if msg.startswith("TEXT:"):
        return "llm_stream", {"content": msg[5:]}
    elif msg.startswith("TOOL_START:"):
        try:
            data = _json.loads(msg[11:])
        except Exception:
            data = {"raw": msg[11:]}
        return "tool_start", data
    elif msg.startswith("TOOL_END:"):
        try:
            data = _json.loads(msg[9:])
        except Exception:
            data = {"raw": msg[9:]}
        return "tool_end", data
    elif msg.startswith("STAGE_START:"):
        try:
            data = _json.loads(msg[12:])
        except Exception:
            data = {"raw": msg[12:]}
        return "stage_start", data
    else:
        # Legacy fallback (plain text token from old backends)
        return "llm_stream", {"content": msg}


# ── WebSocket Log Handler ────────────────────────────────────────────────

class WSLogHandler(logging.Handler):
    """Push log records to all active WebSocket connections.

    Installed on the root veriflow logger once. Each connected
    client receives log events in real-time without polling.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()

        loop = _main_loop
        if loop is None or not loop.is_running():
            return

        if record.name == "veriflow.stream":
            # Parse the structured prefix to get event type + payload
            event_type, payload = _parse_stream_record(msg)
            # Route to the specific session that triggered this stream.
            # get_current_session() reads the thread-local set by _next_item_with_ctx.
            sid = get_current_session()
            if sid:
                loop.call_soon_threadsafe(
                    lambda s=sid, et=event_type, p=payload:
                        loop.create_task(_send_to_session(s, et, p))
                )
            else:
                # No session context (e.g. Gradio/CLI path) — fall back to broadcast
                loop.call_soon_threadsafe(
                    lambda et=event_type, p=payload:
                        loop.create_task(_broadcast_ws_event(et, p))
                )
        else:
            # Regular log line → broadcast to all (for the Logs drawer)
            event_type = "log"
            payload = {
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
                "ts": record.created,
            }
            loop.call_soon_threadsafe(
                lambda et=event_type, p=payload:
                    loop.create_task(_broadcast_ws_event(et, p))
            )


async def _safe_send(websocket: WebSocket, data: dict) -> bool:
    """Send JSON to a WebSocket, swallowing send errors (disconnected clients).

    Returns True if the send succeeded, False if the client was gone.
    Never raises — callers should not crash when a client disconnects.
    """
    try:
        await websocket.send_json(data)
        return True
    except Exception:
        return False


async def _broadcast_ws_event(event_type: str, payload: dict) -> None:
    """Broadcast an event to all active WebSocket sessions."""
    async with _active_ws_lock:
        dead = []
        for sid, ws in _active_ws.items():
            try:
                await ws.send_json(
                    WSEvent(event=event_type, payload=payload).to_dict()
                )
            except Exception:
                dead.append(sid)
        for sid in dead:
            _active_ws.pop(sid, None)


async def _send_to_session(session_id: str, event_type: str, payload: dict) -> None:
    """Send an event to one specific WebSocket session only."""
    async with _active_ws_lock:
        ws = _active_ws.get(session_id)
        if ws is None:
            return
        try:
            await ws.send_json(
                WSEvent(event=event_type, payload=payload).to_dict()
            )
        except Exception:
            _active_ws.pop(session_id, None)


def _install_ws_log_handler() -> None:
    """Attach WSLogHandler to the veriflow logger (idempotent)."""
    root = logging.getLogger("veriflow")
    for h in root.handlers:
        if isinstance(h, WSLogHandler):
            return  # Already installed
    handler = WSLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)


# ── App factory ──────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    global _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass  # Will be set later when the loop starts
    _install_ws_log_handler()

    app = FastAPI(title="VeriFlow-Agent Gateway", version="0.1.0")

    @app.on_event("startup")
    async def _capture_loop() -> None:
        global _main_loop
        _main_loop = asyncio.get_running_loop()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── WebSocket control plane ─────────────────────────────────────────

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        client = websocket.client.host if websocket.client else "unknown"
        Log.info(L.CONN, "WebSocket accepted", addr=client)
        adapter = get_adapter()
        session_id: str | None = None

        try:
            # Handshake: expect connect frame
            raw = await websocket.receive_json()
            connect_req = WSRequest.from_raw(raw)
            Log.debug(L.MSG_IN, "Connect frame received", type=connect_req.type, id=connect_req.id)

            if connect_req.type != "connect":
                await _safe_send(websocket, WSResponse(
                    id=connect_req.id, ok=False, error="First frame must be connect"
                ).to_dict())
                await websocket.close()
                return

            session_id = (
                connect_req.params.get("session_id")
                or _sessions.get_or_create().session_id
            )
            session = _sessions.get_or_create(session_id)
            _sessions.register_ws(session_id, websocket)

            # Register for log broadcasting
            async with _active_ws_lock:
                _active_ws[session_id] = websocket

            await _safe_send(websocket, WSResponse(
                id=connect_req.id,
                ok=True,
                payload={"session_id": session_id},
            ).to_dict())
            Log.info(L.CONN, "WebSocket connected", session=session_id, addr=client)

            # Message loop
            while True:
                raw = await websocket.receive_json()
                req = WSRequest.from_raw(raw)
                Log.debug(L.MSG_IN, "Request received", method=req.method, id=req.id)

                try:
                    if req.method == "send":
                        await _handle_send(websocket, req, session_id, adapter)
                    elif req.method == "stop":
                        adapter.stop_pipeline(session_id)
                        _sessions.set_running(session_id, False)
                        # Also cancel any pending step event
                        if session_id in _step_events:
                            _step_events[session_id].set()
                        await _safe_send(websocket, WSResponse(id=req.id, ok=True).to_dict())
                        Log.info(L.MSG_OUT, "Stop acknowledged", session=session_id)
                    elif req.method == "new_design":
                        adapter.new_design(session_id)
                        _sessions.set_running(session_id, False)
                        await _safe_send(websocket, WSResponse(id=req.id, ok=True).to_dict())
                        Log.info(L.MSG_OUT, "New design session", session=session_id)
                    elif req.method == "status":
                        await _handle_status(websocket, req, session_id)
                    elif req.method == "get_config":
                        await _handle_get_config(websocket, req)
                    elif req.method == "set_config":
                        await _handle_set_config(websocket, req)
                    elif req.method == "set_workspace":
                        await _handle_set_workspace(websocket, req, session_id)
                    elif req.method == "get_workspace":
                        await _handle_get_workspace(websocket, req, session_id)
                    elif req.method == "detect_tools":
                        await _handle_detect_tools(websocket, req)
                    elif req.method == "test_claude_cli":
                        await _handle_test_claude_cli(websocket, req)
                    elif req.method == "set_log_level":
                        await _handle_set_log_level(websocket, req)
                    elif req.method == "next_stage":
                        await _handle_next_stage(websocket, req, session_id)
                    elif req.method == "run_all_stages":
                        await _handle_run_all_stages(websocket, req, session_id)
                    elif req.method == "explore_path":
                        await _handle_explore_path(websocket, req)
                    else:
                        await _safe_send(websocket, WSResponse(
                            id=req.id, ok=False, error=f"Unknown method: {req.method}"
                        ).to_dict())
                        Log.warning(L.ERR, "Unknown method", method=req.method, session=session_id)
                except WebSocketDisconnect:
                    # Client disconnected mid-request — propagate to outer handler
                    raise
                except Exception as e:
                    Log.error(L.ERR, "Request handler failed", method=req.method, error=str(e), session=session_id)
                    # Best-effort error reply; ignore send failure if client is gone
                    await _safe_send(websocket, WSResponse(id=req.id, ok=False, error=str(e)).to_dict())

        except WebSocketDisconnect:
            Log.info(L.CONN, "WebSocket disconnected", session=session_id)
        except Exception as e:
            Log.error(L.ERR, "WebSocket error", error=str(e), session=session_id)
        finally:
            # Always clean up session state regardless of how we got here
            if session_id:
                adapter.stop_pipeline(session_id)          # signal any running pipeline to stop
                _sessions.set_running(session_id, False)
                _sessions.unregister_ws(session_id)
                # Unblock any step-mode wait so the pipeline thread can exit
                step_evt = _step_events.pop(session_id, None)
                if step_evt:
                    step_evt.set()
                _run_all_flags.pop(session_id, None)
                async with _active_ws_lock:
                    _active_ws.pop(session_id, None)
            Log.debug(L.CONN, "Session cleanup complete", session=session_id)

    # ── REST API ────────────────────────────────────────────────────────

    @app.get("/api/config")
    async def api_get_config() -> dict:
        config = VeriFlowConfig.load()
        return config.masked()

    @app.put("/api/config")
    async def api_set_config(body: dict) -> dict:
        config = VeriFlowConfig.load()
        config.apply_dict(body)
        config.save()
        return config.masked()

    @app.get("/api/status")
    async def api_status() -> dict:
        return _sessions.get_status()

    @app.get("/api/tools")
    async def api_detect_tools() -> dict:
        loop = asyncio.get_running_loop()
        config = VeriFlowConfig.load()
        results = await loop.run_in_executor(None, detect_tools, config.tool_paths)
        return results

    # ── Static files (WebChat) ──────────────────────────────────────────

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


# ── Method handlers ──────────────────────────────────────────────────────


async def _handle_send(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
    adapter: ChannelAdapter,
) -> None:
    """Handle "send" method: stream pipeline/chat response."""
    message = req.params.get("message", "").strip()
    if not message:
        Log.warning(L.ERR, "Empty message rejected", session=session_id)
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error="Empty message").to_dict())
        return

    _sessions.set_running(session_id, True)

    # Acknowledge
    await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"status": "processing"}).to_dict())
    Log.debug(L.MSG_OUT, "Ack sent", id=req.id, status="processing")

    # Initialize step-mode event for this session
    _step_events[session_id] = asyncio.Event()
    _run_all_flags[session_id] = False

    # Stream chunks
    chunk_count = 0
    total_chars = 0
    t_start = time.perf_counter()

    try:
        Log.info(L.STREAM, "Stream started", session=session_id, msg_len=len(message))

        # Run synchronous generator in a thread so the event loop stays alive
        gen = adapter.process(session_id, message)
        loop = asyncio.get_running_loop()

        def _next_item_with_ctx():
            """Get next item from generator, returning _DONE when exhausted.

            Sets the thread-local session_id so WSLogHandler can route
            veriflow.stream events to this specific session, not broadcast.
            """
            set_current_session(session_id)
            return next(gen, _DONE)

        while True:
            # Submit next generator step to the thread pool.
            # We keep a reference to the Future so we can send heartbeats while
            # waiting WITHOUT cancelling the underlying thread (which would cause
            # "generator already executing" if the previous call hadn't finished).
            fut = loop.run_in_executor(None, _next_item_with_ctx)

            # Poll with short sleeps; send heartbeat every 10 s of silence.
            heartbeat_interval = 10.0
            elapsed_since_hb = 0.0
            poll_interval = 0.1
            while not fut.done():
                await asyncio.sleep(poll_interval)
                elapsed_since_hb += poll_interval
                if elapsed_since_hb >= heartbeat_interval:
                    elapsed_since_hb = 0.0
                    await _safe_send(websocket, WSEvent(event="heartbeat", payload={
                        "status": "processing",
                        "elapsed_s": round(time.perf_counter() - t_start, 1),
                    }).to_dict())

            item = await fut  # retrieve result (re-raises any exception from the thread)

            if item is _DONE:
                break

            tag, data = item
            if tag == "chunk":
                chunk_len = len(data) if data else 0
                chunk_count += 1
                total_chars += chunk_len
                preview = (data[:30] + "...") if len(data) > 30 else data
                Log.debug(L.CHUNK, f"#{chunk_count}", length=chunk_len, preview=repr(preview))
                await _safe_send(websocket, WSEvent(event="chunk", payload={"content": data}).to_dict())

            elif tag == "event":
                event_type = data.get("event", "unknown")
                payload = data.get("payload", {})
                Log.info(L.STAGE, f"Event: {event_type}", **{k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool))})
                await _safe_send(websocket, WSEvent(event=event_type, payload=payload).to_dict())

                # Handle step-mode pause: wait for user to click "Next Stage"
                if event_type == "stage_paused":
                    step_event = _step_events.get(session_id)
                    if step_event:
                        step_event.clear()
                        await _safe_send(websocket, WSEvent(event="awaiting_next_stage", payload={
                            "stage": payload.get("stage", ""),
                            "next_stage": payload.get("next_stage", ""),
                        }).to_dict())
                        # Wait until user clicks next_stage or run_all
                        await step_event.wait()

            await asyncio.sleep(0)

        elapsed = time.perf_counter() - t_start
        Log.info(
            L.STREAM, "Stream complete",
            session=session_id, chunks=chunk_count, chars=total_chars,
            elapsed=f"{elapsed:.2f}s",
        )

    except WebSocketDisconnect:
        # Client disconnected during streaming — stop the pipeline and propagate
        Log.info(L.CONN, "Client disconnected during stream", session=session_id)
        adapter.stop_pipeline(session_id)
        raise
    except Exception as e:
        Log.error(L.ERR, "Stream error", session=session_id, error=str(e))
        await _safe_send(websocket, WSEvent(event="error", payload={"message": str(e)}).to_dict())
    finally:
        _sessions.set_running(session_id, False)
        _step_events.pop(session_id, None)
        _run_all_flags.pop(session_id, None)

    # Done signal — best-effort, client may have already disconnected
    await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"status": "done"}).to_dict())
    Log.debug(L.MSG_OUT, "Done signal sent", id=req.id)


async def _handle_status(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
) -> None:
    session = _sessions.get_or_create(session_id)
    workspace = _handler.get_workspace(session_id)
    config = VeriFlowConfig.load()
    await _safe_send(websocket, WSResponse(
        id=req.id,
        ok=True,
        payload={
            "pipeline_running": session.pipeline_running,
            "session_id": session.session_id,
            "workspace_dir": str(workspace) if workspace else "",
            "stage_mode": config.stage_mode,
        },
    ).to_dict())


async def _handle_get_workspace(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
) -> None:
    session = _sessions.get_or_create(session_id)
    config = VeriFlowConfig.load()
    
    # Bootstrap default workspace from config if it exists and hasn't been set
    workspace = _handler.get_workspace(session_id)
    if workspace is None and config.workspace_dir:
        try:
            _handler.set_workspace(session_id, config.workspace_dir)
            workspace = _handler.get_workspace(session_id)
        except Exception:
            pass

    await _safe_send(websocket, WSResponse(
        id=req.id,
        ok=True,
        payload={
            "workspace_dir": str(workspace) if workspace else "",
        },
    ).to_dict())


async def _handle_get_config(websocket: WebSocket, req: WSRequest) -> None:
    config = VeriFlowConfig.load()
    await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload=config.masked()).to_dict())


async def _handle_set_config(websocket: WebSocket, req: WSRequest) -> None:
    config = VeriFlowConfig.load()
    config.apply_dict(req.params)
    config.save()
    await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload=config.masked()).to_dict())


async def _handle_set_workspace(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
) -> None:
    path = req.params.get("path", "").strip()
    if not path:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error="path is required").to_dict())
        return
    try:
        resolved = _handler.set_workspace(session_id, path)
        config = VeriFlowConfig.load()
        config.workspace_dir = resolved
        config.save()
        Log.info(L.MSG_OUT, "Workspace set and saved", session=session_id, path=resolved)
        await _safe_send(websocket, WSResponse(
            id=req.id,
            ok=True,
            payload={"workspace_dir": resolved},
        ).to_dict())
    except Exception as e:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error=str(e)).to_dict())


async def _handle_get_workspace(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
) -> None:
    workspace = _handler.get_workspace(session_id)
    config = VeriFlowConfig.load()
    await _safe_send(websocket, WSResponse(
        id=req.id,
        ok=True,
        payload={
            "workspace_dir": str(workspace) if workspace else "",
            "default_workspace": config.workspace_dir,
        },
    ).to_dict())


async def _handle_detect_tools(websocket: WebSocket, req: WSRequest) -> None:
    """Run EDA tool detection and return results."""
    loop = asyncio.get_running_loop()
    try:
        config = VeriFlowConfig.load()
        results = await loop.run_in_executor(None, detect_tools, config.tool_paths)
        await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"tools": results}).to_dict())
    except Exception as e:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error=str(e)).to_dict())


async def _handle_test_claude_cli(websocket: WebSocket, req: WSRequest) -> None:
    """Test Claude CLI connectivity."""
    # Find the CLI path from tool detection
    loop = asyncio.get_running_loop()
    try:
        config = VeriFlowConfig.load()
        tools = await loop.run_in_executor(None, detect_tools, config.tool_paths)
        claude_info = tools.get("claude_cli", {})
        claude_path = claude_info.get("path") or ""
        result = await test_claude_cli(claude_path)
        await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload=result).to_dict())
    except Exception as e:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error=str(e)).to_dict())


async def _handle_set_log_level(websocket: WebSocket, req: WSRequest) -> None:
    """Dynamically change the veriflow logger level."""
    level_str = req.params.get("level", "INFO").upper()
    numeric = getattr(logging, level_str, None)
    if numeric is None:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error=f"Unknown log level: {level_str}").to_dict())
        return
    logging.getLogger("veriflow").setLevel(numeric)
    Log.info(L.MSG_OUT, f"Log level changed to {level_str}")
    await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"level": level_str}).to_dict())


async def _handle_next_stage(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
) -> None:
    """Signal the paused pipeline to advance to the next stage."""
    event = _step_events.get(session_id)
    if event:
        event.set()
        await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"status": "continued"}).to_dict())
    else:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error="No pipeline is paused").to_dict())


async def _handle_run_all_stages(
    websocket: WebSocket,
    req: WSRequest,
    session_id: str,
) -> None:
    """Signal the paused pipeline to run all remaining stages without pausing."""
    _run_all_flags[session_id] = True
    event = _step_events.get(session_id)
    if event:
        event.set()
        await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"status": "running_all"}).to_dict())
    else:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error="No pipeline is paused").to_dict())


async def _handle_explore_path(websocket: WebSocket, req: WSRequest) -> None:
    """Explore the local filesystem for directory/file picking."""
    path_str = req.params.get("path", "").strip()
    try:
        import os
        from pathlib import Path
        
        # If empty on Windows, list root drives
        if not path_str and os.name == "nt":
            import ctypes
            drives = []
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if bitmask & (1 << i):
                    drive = chr(65 + i) + ":\\"
                    drives.append({"name": drive, "is_dir": True, "path": drive})
            await _safe_send(websocket, WSResponse(id=req.id, ok=True, payload={"parent": "", "current": "", "contents": drives}).to_dict())
            return
            
        p = Path(path_str) if path_str else Path("/").resolve()
        if not p.exists() or not p.is_dir():
             p = Path.home()
        
        contents = []
        try:
            for child in p.iterdir():
                try:
                    is_dir = child.is_dir()
                    contents.append({
                        "name": child.name,
                        "is_dir": is_dir,
                        "path": str(child.absolute())
                    })
                except OSError:
                    pass
        except OSError:
            pass # Permission error etc
            
        # Sort dirs first, then files alphabetically
        contents.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        
        parent = str(p.parent.absolute()) if p.parent != p else ""
        if os.name == "nt" and p.parent == p:
            parent = "" # Going up from C:\ goes to Drives view
            
        await _safe_send(websocket, WSResponse(
            id=req.id,
            ok=True,
            payload={"parent": parent, "current": str(p), "contents": contents}
        ).to_dict())
    except Exception as e:
        await _safe_send(websocket, WSResponse(id=req.id, ok=False, error=str(e)).to_dict())
