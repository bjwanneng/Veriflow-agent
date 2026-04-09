"""VeriFlow-Agent Gateway configuration.

Reads and writes ~/.veriflow/config.json.
Environment variables take precedence over config file values.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from veriflow_agent.chat.llm import LLMConfig

CONFIG_PATH = Path.home() / ".veriflow" / "config.json"


@dataclass
class VeriFlowConfig:
    """Gateway + LLM + Telegram configuration."""

    # LLM
    llm_backend: str = "openai"  # openai | langchain  (OpenAI-compatible format)
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.1

    # Gateway
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 18789

    # Workspace (output directory for pipeline projects)
    # Empty = OS temp dir. Each design creates a subfolder: <workspace>/<slug>/
    workspace_dir: str = ""

    # Pipeline control
    stage_mode: str = "auto"        # "auto" | "step" (step-by-step confirmation)
    max_retries: int = 3
    quality_threshold: float = 0.5
    token_budget: int = 1_000_000

    # EDA Tools manual paths override, e.g. {"yosys": "C:/path/to/yosys.exe"}
    tool_paths: dict[str, str] = field(default_factory=dict)

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_users: list[int] = field(default_factory=list)

    # ── Persistence ────────────────────────────────────────────────────

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> VeriFlowConfig:
        """Load from disk, applying env-var overrides."""
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass
        return cfg._apply_env()

    def _apply_env(self) -> VeriFlowConfig:
        """Override fields from environment variables."""
        if v := os.getenv("VERIFLOW_LLM_BACKEND"):
            if v == "claude_cli":
                import logging as _logging
                _logging.getLogger("veriflow").warning(
                    "VERIFLOW_LLM_BACKEND=claude_cli is disabled; falling back to 'openai'"
                )
                self.llm_backend = "openai"
            else:
                self.llm_backend = v
        if v := os.getenv("VERIFLOW_API_KEY"):
            self.api_key = v
        if v := os.getenv("VERIFLOW_BASE_URL"):
            self.base_url = v
        if v := os.getenv("VERIFLOW_MODEL"):
            self.model = v
        if v := os.getenv("TELEGRAM_BOT_TOKEN"):
            self.telegram_bot_token = v
        if v := os.getenv("VERIFLOW_WORKSPACE_DIR"):
            self.workspace_dir = v
        if v := os.getenv("VERIFLOW_STAGE_MODE"):
            self.stage_mode = v
        if v := os.getenv("VERIFLOW_TOKEN_BUDGET"):
            try:
                self.token_budget = int(v)
            except ValueError:
                pass
        return self

    # ── Conversion ─────────────────────────────────────────────────────

    def to_llm_config(self) -> LLMConfig:
        return LLMConfig(
            backend=self.llm_backend,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
        )

    # Writable fields through the REST/WS API (security: explicit whitelist)
    _WRITABLE_FIELDS = frozenset({
        "llm_backend", "api_key", "base_url", "model",
        "max_tokens", "temperature",
        "gateway_host", "gateway_port",
        "workspace_dir",
        "stage_mode", "max_retries", "quality_threshold", "token_budget",
        "tool_paths",
    })

    def apply_dict(self, data: dict) -> None:
        """Apply a dict of updates, limited to _WRITABLE_FIELDS (safe API update)."""
        for key, value in data.items():
            if key in self._WRITABLE_FIELDS and value is not None:
                # Type coercion for numeric fields
                field_type = type(getattr(self, key))
                try:
                    setattr(self, key, field_type(value))
                except (ValueError, TypeError):
                    pass

    def masked(self) -> dict:
        """Return config dict with api_key masked for API responses."""
        d = asdict(self)
        if d.get("api_key"):
            d["api_key"] = d["api_key"][:4] + "****" + d["api_key"][-4:] if len(d["api_key"]) > 8 else "****"
        if d.get("telegram_bot_token"):
            d["telegram_bot_token"] = "****"
        return d
