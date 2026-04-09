"""VeriFlow-Agent TUI Client - WebSocket terminal client for Gateway.

Aligned with OpenClaw's architecture:
  - TUI is a WebSocket client, not a standalone app
  - Connects to an already-running Gateway
  - Provides terminal-based chat interface with Rich progress display

Usage:
    # Terminal 1: Start Gateway
    veriflow-agent gateway

    # Terminal 2: Connect TUI client
    veriflow-agent tui
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from typing import Any

import websockets
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from veriflow_agent.gateway.log import L, Log, resolve_level, setup_logging

console = Console()

# Stage display names
_STAGE_NAMES = {
    "architect": "Arch", "microarch": "uArch", "timing": "Timing",
    "coder": "Coder", "skill_d": "SkillD", "lint": "Lint",
    "sim": "Sim", "synth": "Synth", "debugger": "Debug",
}


class TUIClient:
    """WebSocket TUI client for VeriFlow-Agent Gateway."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18789,
        session_id: str | None = None,
        workspace: str | None = None,
    ):
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/ws"
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.workspace = workspace
        self.ws: Any = None
        self.running = False
        self.response_event = asyncio.Event()
        self.last_response: dict[str, Any] | None = None
        # Progress tracking
        self._response_start: float = 0
        self._last_event_time: float = 0
        self._chunk_count: int = 0
        self._waiting_printed: bool = False

    async def connect(self) -> bool:
        """Connect to Gateway WebSocket."""
        Log.info(L.CONN, "Connecting to Gateway", url=self.ws_url)
        try:
            self.ws = await websockets.connect(self.ws_url)

            connect_frame = {
                "type": "connect", "id": "connect-1", "method": "",
                "params": {"session_id": self.session_id},
            }
            await self.ws.send(json.dumps(connect_frame))

            response_raw = await self.ws.recv()
            response = json.loads(response_raw)

            if response.get("ok"):
                self.session_id = response.get("payload", {}).get(
                    "session_id", self.session_id
                )
                Log.info(L.CONN, "Session established", session=self.session_id)

                # Send workspace if configured
                if self.workspace:
                    ws_req = {
                        "type": "req", "id": "ws-init",
                        "method": "set_workspace",
                        "params": {"path": self.workspace},
                    }
                    await self.ws.send(json.dumps(ws_req))
                    ws_resp = await self.ws.recv()
                    ws_data = json.loads(ws_resp)
                    if ws_data.get("ok"):
                        Log.info(L.CONN, "Workspace set", path=self.workspace)
                    else:
                        Log.warning(L.ERR, "Workspace set failed", error=ws_data.get("error"))

                return True
            else:
                Log.error(L.ERR, "Connect rejected", error=response.get("error"))
                console.print(f"[red]Connection failed: {response.get('error')}")
                return False

        except Exception as e:
            Log.error(L.ERR, "Connection failed", error=str(e))
            console.print(f"[red]Failed to connect to Gateway at {self.ws_url}")
            console.print(f"[red]Error: {e}")
            console.print("\n[yellow]Hint: Make sure the Gateway is running:")
            console.print("  veriflow-agent gateway")
            return False

    async def send_message(self, message: str) -> bool:
        """Send a message to the Gateway."""
        if not self.ws:
            return False
        request = {
            "type": "req", "id": f"req-{uuid.uuid4().hex[:8]}",
            "method": "send", "params": {"message": message},
        }
        try:
            await self.ws.send(json.dumps(request))
            Log.debug(L.MSG_OUT, "Message sent", id=request["id"], msg_len=len(message))
            return True
        except Exception as e:
            Log.error(L.ERR, "Send failed", error=str(e))
            return False

    async def _reconnect(self) -> bool:
        """Attempt to reconnect to Gateway."""
        console.print("\n[yellow]Connection lost. Reconnecting...")
        self.ws = None
        if await self.connect():
            # Restart receive loop
            asyncio.create_task(self.receive_loop())
            return True
        return False

    async def receive_loop(self) -> None:
        """Background task to receive messages from Gateway."""
        try:
            while self.running and self.ws:
                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=0.5)
                    data = json.loads(raw)
                    await self._handle_message(data)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    Log.info(L.CONN, "Connection closed by server")
                    break
        except Exception as e:
            if self.running:
                Log.error(L.ERR, "Receive loop error", error=str(e))

    def _clear_waiting(self) -> None:
        """Clear the waiting indicator if it was printed."""
        if self._waiting_printed:
            sys.stdout.write("\r" + " " * 60 + "\r")
            sys.stdout.flush()
            self._waiting_printed = False

    async def _handle_message(self, data: dict) -> None:
        """Handle incoming WebSocket message."""
        msg_type = data.get("type")

        if msg_type == "res":
            payload = data.get("payload", {})
            status = payload.get("status", "")

            if status == "processing":
                return  # Don't signal event yet

            # Final response (done / error)
            self.last_response = data
            self.response_event.set()

        elif msg_type == "event":
            self._last_event_time = time.time()
            event_type = data.get("event")
            payload = data.get("payload", {})

            if event_type == "chunk" or event_type == "llm_stream":
                content = payload.get("content", "") or payload.get("text", "")
                if content:
                    self._clear_waiting()
                    self._chunk_count += 1
                    console.print(content, end="", highlight=False)

            elif event_type == "heartbeat":
                # Gateway heartbeat — keep the connection alive during long LLM calls
                pass

            elif event_type == "stage_start":
                self._clear_waiting()
                stage = payload.get("stage", "unknown")
                display_name = _STAGE_NAMES.get(stage, stage)
                console.print(f"\n[cyan]>> [bold]{display_name}[/] started...[/]")

            elif event_type == "tool_start":
                self._clear_waiting()
                tool_name = payload.get("tool_name", "?")
                stage = payload.get("stage", "")
                prefix = f"[{stage}] " if stage else ""
                console.print(f"  [dim cyan]{prefix}↳ tool:[/] [cyan]{tool_name}[/cyan]")

            elif event_type == "tool_end":
                self._clear_waiting()
                success = payload.get("success", True)
                error = payload.get("error", "")
                stage = payload.get("stage", "")
                prefix = f"[{stage}] " if stage else ""
                if success:
                    console.print(f"  [dim green]{prefix}↳ [done][/dim green]")
                else:
                    console.print(f"  [dim red]{prefix}↳ [FAILED] {error}[/dim red]")

            elif event_type == "stage_update":
                self._clear_waiting()
                self._render_stage_update(payload)

            elif event_type == "error":
                self._clear_waiting()
                error_msg = payload.get("message", "Unknown error")
                console.print(f"\n[red]Error: {error_msg}[/]")

    def _render_stage_update(self, payload: dict) -> None:
        """Render a stage_update event as a Rich notification."""
        stage = payload.get("stage", "unknown")
        status = payload.get("status", "unknown")
        display_name = _STAGE_NAMES.get(stage, stage)

        if stage == "pipeline" and status == "started":
            console.print()
            console.print(Panel(
                "[bold cyan]Pipeline started[/]",
                border_style="cyan", padding=(0, 2),
            ))
            return

        if stage == "pipeline" and status == "done":
            completed = payload.get("completed", [])
            failed = payload.get("failed", [])
            total = len(completed) + len(failed)
            elapsed = time.perf_counter() - self._response_start
            color = "green" if not failed else "red"
            console.print()
            console.print(Panel(
                f"[bold {color}]Pipeline complete[/]  "
                f"{total} stages  {elapsed:.1f}s\n"
                f"  pass: {len(completed)}  fail: {len(failed)}",
                border_style=color, padding=(0, 2),
            ))
            return

        if stage == "pipeline" and status == "error":
            error = payload.get("error", "Unknown")
            console.print()
            console.print(Panel(
                f"[bold red]Pipeline error[/]\n  {error}",
                border_style="red", padding=(0, 2),
            ))
            return

        if stage == "debugger":
            source = payload.get("source", "?")
            retry = payload.get("retry_count", 0)
            target = payload.get("rollback_target", "?")
            console.print()
            console.print(Panel(
                f"[bold yellow]Debugger retry #{retry}[/]\n"
                f"  source: {source}  rollback: {target}",
                border_style="yellow", padding=(0, 2),
            ))
            return

        # Regular stage completion
        stage_num = payload.get("stage_num", "?")
        total_stages = payload.get("total_stages", "?")
        duration = payload.get("duration_s", 0)
        artifacts = payload.get("artifacts", [])
        completed = payload.get("completed", [])

        if status == "pass":
            icon = "[bold green]PASS[/]"
            border = "green"
        else:
            icon = "[bold red]FAIL[/]"
            border = "red"

        # Progress line: [1/8] Arch PASS 2.3s
        progress = f"[{stage_num}/{total_stages}]" if isinstance(stage_num, int) else ""
        artifact_str = ", ".join(a.split("/")[-1] for a in artifacts[:3]) if artifacts else "-"
        duration_str = f"{duration:.1f}s" if duration else "-"

        console.print()
        console.print(Panel(
            f"{icon}  {progress} [bold]{display_name}[/]\n"
            f"  duration: {duration_str}  artifacts: {artifact_str}",
            border_style=border, padding=(0, 2),
        ))

    async def _waiting_indicator(self) -> None:
        """Show a waiting indicator when pipeline is quiet for >2 seconds."""
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spin_idx = 0
        while self.running and not self.response_event.is_set():
            silence_dur = time.time() - self._last_event_time
            if silence_dur > 2.0:
                self._waiting_printed = True
                sys.stdout.write(
                    f"\r  [dim]{spinner[spin_idx]} Working... ({silence_dur:.1f}s quiet)[/dim]   "
                )
                sys.stdout.flush()
                spin_idx = (spin_idx + 1) % len(spinner)
            await asyncio.sleep(0.1)
        self._clear_waiting()

    async def run_interactive(self) -> None:
        """Run the interactive TUI."""
        self.running = True
        receive_task = asyncio.create_task(self.receive_loop())

        console.print(Panel(
            f"[bold cyan]VeriFlow-Agent TUI Client[/]\n"
            f"[dim]Session: {self.session_id}[/]\n"
            f"[dim]Gateway: {self.host}:{self.port}[/]\n"
            f"[dim]Workspace: {self.workspace or 'temp'}[/]",
            title="Connected", border_style="green"
        ))

        console.print("[dim]Commands: /quit  /new  /status[/]")
        console.print("")

        try:
            while self.running:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Prompt.ask("[bold cyan]You[/]")
                )
                user_input = user_input.strip()

                if not user_input:
                    continue

                if user_input == "/quit":
                    console.print("[dim]Disconnecting...")
                    self.running = False
                    break

                elif user_input == "/new":
                    request = {
                        "type": "req", "id": f"req-{uuid.uuid4().hex[:8]}",
                        "method": "new_design", "params": {},
                    }
                    await self.ws.send(json.dumps(request))
                    console.print("[green]New design session started.")

                elif user_input == "/status":
                    request = {
                        "type": "req", "id": f"req-{uuid.uuid4().hex[:8]}",
                        "method": "status", "params": {},
                    }
                    await self.ws.send(json.dumps(request))
                    console.print("[dim]Checking status...")

                else:
                    # Send message
                    console.print("\n[bold magenta]Assistant[/]")
                    console.print("\u2500" * 50, style="dim")

                    self._response_start = time.perf_counter()
                    self._last_event_time = time.time()
                    self._chunk_count = 0
                    self._waiting_printed = False

                    success = await self.send_message(user_input)
                    if not success:
                        # Try reconnecting once
                        reconnected = await self._reconnect()
                        if reconnected:
                            success = await self.send_message(user_input)
                        if not success:
                            console.print("[red]Failed to send message. Check if Gateway is running.")
                            console.print("\u2500" * 50, style="dim")
                            console.print()
                            continue

                    # Start waiting indicator in background
                    wait_task = asyncio.create_task(self._waiting_indicator())

                    self.response_event.clear()
                    try:
                        await asyncio.wait_for(self.response_event.wait(), timeout=600.0)
                    except asyncio.TimeoutError:
                        console.print("\n[dim](Response timeout — pipeline may still be running)")

                    wait_task.cancel()
                    self._clear_waiting()

                    elapsed = time.perf_counter() - self._response_start
                    console.print()
                    console.print(
                        f"[dim]  {self._chunk_count} chunks, {elapsed:.1f}s[/]"
                    )

                    console.print("\u2500" * 50, style="dim")
                    console.print()

        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted by user.")
            self.running = False

        finally:
            self.running = False
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
            if self.ws:
                await self.ws.close()
            Log.info(L.CONN, "Disconnected")
            console.print("[dim]Disconnected.")


def launch_tui(
    host: str = "127.0.0.1",
    port: int = 18789,
    session_id: str | None = None,
    workspace: str | None = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Launch the TUI client (synchronous entry point)."""
    level = resolve_level(cli_verbose=verbose, cli_quiet=quiet)
    setup_logging(level=level, prefix="TUI", mode="file")

    client = TUIClient(host=host, port=port, session_id=session_id, workspace=workspace)

    async def main():
        if await client.connect():
            await client.run_interactive()
        else:
            sys.exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[dim]Goodbye!")


if __name__ == "__main__":
    launch_tui()
