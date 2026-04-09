"""VeriFlow-Agent Gateway package.

Usage:
    from veriflow_agent.gateway import launch_gateway
    launch_gateway(host="127.0.0.1", port=18789)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from veriflow_agent.gateway.log import L, Log, resolve_level, setup_logging

logger = logging.getLogger("veriflow")


def launch_gateway(
    host: str = "127.0.0.1",
    port: int = 18789,
    enable_telegram: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    workspace: str | None = None,
) -> None:
    """Launch the VeriFlow-Agent Gateway daemon.

    Starts:
    - FastAPI + uvicorn server (WebSocket + REST + static WebChat)
    - Optional Telegram bot channel
    """
    import uvicorn

    # Configure logging
    level = resolve_level(cli_verbose=verbose, cli_quiet=quiet)
    setup_logging(level=level, prefix="Gateway")

    config = VeriFlowConfig.load()

    # Apply CLI workspace override
    if workspace:
        config.workspace_dir = str(Path(workspace).resolve())
        config.save()
        Log.info(L.CONN, "Workspace set from CLI", path=config.workspace_dir)

    # Set default workspace on handler
    from veriflow_agent.gateway.server import _handler
    _handler.set_default_workspace(config.workspace_dir or None)

    # Start Telegram if requested and configured
    telegram_channel = None
    if enable_telegram:
        from veriflow_agent.chat.handler import PipelineChatHandler
        from veriflow_agent.gateway.telegram_bot import TelegramChannel

        handler = _get_handler()
        telegram_channel = TelegramChannel(config, handler)
        if telegram_channel.available:
            pass
        else:
            Log.warning(L.ERR, "Telegram requested but no bot token configured")
            telegram_channel = None

    app = create_app()

    # Inject telegram startup into the app lifecycle
    if telegram_channel and telegram_channel.available:
        _tc = telegram_channel

        @app.on_event("startup")
        async def start_telegram() -> None:
            loop = asyncio.get_event_loop()
            _tc.start(loop)

        @app.on_event("shutdown")
        async def stop_telegram() -> None:
            await _tc.stop()

    Log.info(L.CONN, "Gateway starting", host=host, port=port)

    # Use INFO for uvicorn's own logging regardless of verbose flag.
    # Verbose mode is handled by our setup_logging() above which controls
    # the "veriflow" namespace. This prevents websockets.protocol debug
    # noise (ping/pong keepalive) from flooding the console.
    uvicorn.run(app, host=host, port=port, log_level="info")


def _get_handler():
    """Get the shared PipelineChatHandler singleton."""
    from veriflow_agent.gateway.server import _handler
    return _handler


# Late imports to avoid circular dependencies
from veriflow_agent.gateway.config import VeriFlowConfig
from veriflow_agent.gateway.server import create_app
