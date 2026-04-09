"""Telegram channel — aligned with OpenClaw's Telegram integration.

Uses python-telegram-bot to receive messages and route them through
the ChannelAdapter (same as the WebSocket path).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from veriflow_agent.chat.handler import PipelineChatHandler
from veriflow_agent.gateway.adapter import ChannelAdapter
from veriflow_agent.gateway.config import VeriFlowConfig

logger = logging.getLogger("veriflow")


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split text into chunks respecting Telegram's message size limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at a newline
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    return chunks


class TelegramChannel:
    """Telegram bot channel for VeriFlow-Agent."""

    def __init__(
        self,
        config: VeriFlowConfig,
        handler: PipelineChatHandler,
    ) -> None:
        self._token = config.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._allowed_users: list[int] = list(config.telegram_allowed_users)
        self._adapter = ChannelAdapter(handler, config)
        self._bot: Any = None
        self._app: Any = None

    @property
    def available(self) -> bool:
        return bool(self._token)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the Telegram bot in the given event loop."""
        if not self.available:
            logger.warning("Telegram bot token not set, skipping Telegram channel")
            return

        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters
        except ImportError:
            logger.error("python-telegram-bot not installed. pip install python-telegram-bot")
            return

        self._app = (
            ApplicationBuilder()
            .token(self._token)
            .build()
        )

        async def on_message(update: Any, context: Any) -> None:
            await self._handle_message(update, context)

        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, on_message)
        )

        # Start polling in background
        loop.create_task(self._run_polling())

    async def _run_polling(self) -> None:
        """Run the Telegram polling loop."""
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot started polling")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass

    async def _handle_message(self, update: Any, context: Any) -> None:
        """Handle an incoming Telegram message."""
        text = update.message.text
        chat_id = update.message.chat_id
        user_id = update.message.from_user.id

        # Auth check
        if self._allowed_users and user_id not in self._allowed_users:
            await update.message.reply_text("Unauthorized.")
            return

        session_id = f"telegram-{chat_id}"

        # Send "typing" indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Process through adapter
        full_response_parts: list[str] = []
        try:
            for tag, data in self._adapter.process(session_id, text):
                if tag == "chunk":
                    full_response_parts.append(data)
        except Exception as e:
            logger.exception("Telegram handler error")
            await update.message.reply_text(f"Error: {e}")
            return

        full_response = "".join(full_response_parts)
        if not full_response:
            await update.message.reply_text("No response generated.")
            return

        # Send in chunks (Telegram limit: 4096 chars)
        for segment in _split_message(full_response):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=segment,
                    parse_mode="Markdown",
                )
            except Exception:
                # Fallback without markdown
                try:
                    await context.bot.send_message(chat_id=chat_id, text=segment)
                except Exception as e2:
                    logger.error("Failed to send Telegram message: %s", e2)
