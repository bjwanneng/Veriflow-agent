"""Lightweight LLM caller for conversational chat mode.

Supports two backends:
- claude_cli: Claude CLI subprocess (default, zero-config)
- openai: OpenAI-compatible API (requires API key + base URL)

Used by the chat handler for general conversation before/after pipeline runs.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("veriflow")

# Security: --dangerously-skip-permissions is required for automated pipeline.
# Set VERIFLOW_SKIP_PERMISSIONS=false to disable in production.
_SKIP_PERMS = os.environ.get("VERIFLOW_SKIP_PERMISSIONS", "false").lower() in ("true", "1", "yes")

# Connection pooling: disable via VERIFLOW_CONNECTION_POOL=false
_ENABLE_POOL = os.environ.get("VERIFLOW_CONNECTION_POOL", "true").lower() in ("true", "1", "yes")
_KEEPALIVE_INTERVAL = int(os.environ.get("VERIFLOW_KEEPALIVE_SEC", "30"))

# Timeout configuration (in seconds) - can be overridden via environment
_TIMEOUT_CONNECT = float(os.environ.get("VERIFLOW_TIMEOUT_CONNECT", "15.0"))      # TCP connection
_TIMEOUT_READ = float(os.environ.get("VERIFLOW_TIMEOUT_READ", "600.0"))           # Read timeout (10 min for long LLM calls)
_TIMEOUT_WRITE = float(os.environ.get("VERIFLOW_TIMEOUT_WRITE", "60.0"))          # Write timeout
_TIMEOUT_POOL = float(os.environ.get("VERIFLOW_TIMEOUT_POOL", "15.0"))            # Pool acquire timeout

# Retry configuration
_RETRY_MAX_ATTEMPTS = int(os.environ.get("VERIFLOW_RETRY_MAX", "3"))              # Max retry attempts
_RETRY_BASE_DELAY = float(os.environ.get("VERIFLOW_RETRY_DELAY", "2.0"))          # Base delay for exponential backoff
_RETRY_MAX_DELAY = float(os.environ.get("VERIFLOW_RETRY_MAX_DELAY", "60.0"))      # Max delay cap

# ── Connection Pool Management ───────────────────────────────────────────

class LLMConnectionPool:
    """Manages persistent HTTP connections and keep-alive for LLM API.

    Reduces cold-start latency by:
    1. Reusing HTTP connections via httpx.Client
    2. Sending periodic keep-alive pings to keep the connection warm
    """

    _instance: LLMConnectionPool | None = None
    _lock = threading.Lock()

    def __new__(cls) -> LLMConnectionPool:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._http_client: httpx.Client | None = None
        self._http_client_lock = threading.Lock()
        self._last_keepalive: float = 0
        self._keepalive_thread: threading.Thread | None = None
        self._shutdown = False
        self._config: LLMConfig | None = None

    def get_http_client(self) -> httpx.Client | None:
        """Get or create a shared httpx.Client with connection pooling."""
        if not _ENABLE_POOL:
            return None

        with self._http_client_lock:
            if self._http_client is None:
                try:
                    import httpx

                    # Configure connection pool limits
                    # Note: For streaming LLM calls that can take 3+ minutes,
                    # we need longer timeouts and careful keep-alive settings
                    limits = httpx.Limits(
                        max_keepalive_connections=3,
                        max_connections=5,
                        keepalive_expiry=300.0,  # Keep connections alive for 5 min
                    )
                    # Configure timeout for long-running LLM calls
                    timeout = httpx.Timeout(
                        connect=_TIMEOUT_CONNECT,
                        read=_TIMEOUT_READ,
                        write=_TIMEOUT_WRITE,
                        pool=_TIMEOUT_POOL,
                    )
                    self._http_client = httpx.Client(
                        limits=limits,
                        timeout=timeout,
                        http2=False,  # Disable HTTP/2 for broader compatibility
                    )
                    logger.debug("LLMConnectionPool: Created new httpx.Client")
                except ImportError:
                    logger.warning("httpx not installed, connection pooling disabled")
                    return None
            return self._http_client

    def configure(self, config: LLMConfig) -> None:
        """Configure the pool with LLM settings for keep-alive pings."""
        self._config = config
        self._start_keepalive()

    def _start_keepalive(self) -> None:
        """Start background thread for periodic keep-alive pings."""
        if self._keepalive_thread is not None and self._keepalive_thread.is_alive():
            return
        if not _ENABLE_POOL or _KEEPALIVE_INTERVAL <= 0:
            return

        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            daemon=True,
            name="LLMKeepAlive",
        )
        self._keepalive_thread.start()
        logger.debug(f"LLMConnectionPool: Keep-alive started ({_KEEPALIVE_INTERVAL}s interval)")

    def start_background_warmup(self, config: LLMConfig) -> None:
        """Start a background thread to warm up the connection without blocking."""
        self._config = config
        thread = threading.Thread(
            target=self._background_warmup_task,
            daemon=True,
            name="LLMWarmup",
        )
        thread.start()

    def _background_warmup_task(self) -> None:
        """Background task to warm up connection."""
        try:
            logger.debug("LLMConnectionPool: Background warmup starting...")
            self._send_keepalive_ping()
        except Exception as e:
            logger.debug(f"LLMConnectionPool: Background warmup error: {e}")

    def _keepalive_loop(self) -> None:
        """Background loop that sends periodic keep-alive requests."""
        while not self._shutdown:
            time.sleep(1)  # Check every second

            if self._shutdown:
                break

            # Check if it's time for a keep-alive ping
            now = time.monotonic()
            if now - self._last_keepalive < _KEEPALIVE_INTERVAL:
                continue

            # Skip if no config or client
            if self._config is None:
                continue

            self._send_keepalive_ping()

    def _send_keepalive_ping(self) -> None:
        """Send a lightweight ping to keep the connection warm."""
        import json

        if self._config is None:
            return

        api_key = self._config.api_key or os.environ.get("OPENAI_API_KEY", "")
        base_url = self._config.base_url or os.environ.get("OPENAI_BASE_URL", "")

        if not api_key or not base_url:
            return

        # Use /models endpoint (lightweight, no LLM computation)
        ping_url = base_url.rstrip("/") + "/models"

        try:
            client = self.get_http_client()
            if client is None:
                return

            headers = {"Authorization": f"Bearer {api_key}"}
            start = time.monotonic()
            response = client.get(ping_url, headers=headers, timeout=10.0)
            elapsed = (time.monotonic() - start) * 1000

            if response.status_code == 200:
                self._last_keepalive = time.monotonic()
                logger.debug(f"LLMConnectionPool: Keep-alive ping OK ({elapsed:.1f}ms)")
            else:
                logger.warning(f"LLMConnectionPool: Keep-alive ping failed: {response.status_code}")
        except Exception as e:
            logger.debug(f"LLMConnectionPool: Keep-alive ping error: {e}")

    def warmup(self, config: LLMConfig) -> None:
        """Explicitly warm up the connection by making a lightweight request."""
        self._config = config
        logger.debug("LLMConnectionPool: Warming up connection...")

        # First, establish HTTP connection via /models endpoint
        self._send_keepalive_ping()

        # Then optionally send a minimal completion to warm up LLM endpoint
        # This is disabled by default as it consumes tokens
        if os.environ.get("VERIFLOW_WARMUP_LLM", "false").lower() == "true":
            self._warmup_llm_endpoint(config)

    def _warmup_llm_endpoint(self, config: LLMConfig) -> None:
        """Send a minimal completion request to warm up the LLM endpoint."""
        import json

        api_key = config.api_key or os.environ.get("OPENAI_API_KEY", "")
        base_url = config.base_url or os.environ.get("OPENAI_BASE_URL", "")

        if not api_key or not base_url:
            return

        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": config.get_effective_model(),
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
            "stream": False,
        }

        try:
            client = self.get_http_client()
            if client is None:
                return

            start = time.monotonic()
            response = client.post(url, headers=headers, json=data, timeout=30.0)
            elapsed = (time.monotonic() - start) * 1000

            if response.status_code == 200:
                logger.debug(f"LLMConnectionPool: LLM warmup OK ({elapsed:.1f}ms)")
            else:
                logger.debug(f"LLMConnectionPool: LLM warmup failed: {response.status_code}")
        except Exception as e:
            logger.debug(f"LLMConnectionPool: LLM warmup error: {e}")

    def close(self) -> None:
        """Close the connection pool and stop keep-alive thread."""
        self._shutdown = True

        with self._http_client_lock:
            if self._http_client is not None:
                try:
                    self._http_client.close()
                except Exception:
                    pass
                self._http_client = None

        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2.0)

        logger.debug("LLMConnectionPool: Closed")


# ── Circuit Breaker ──────────────────────────────────────────────────────

class CircuitBreaker:
    """Circuit breaker pattern to prevent cascading failures.

    After a threshold of consecutive failures, the circuit opens and
    subsequent calls fail fast without attempting the operation.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._failures = 0
        self._last_failure_time: float | None = None
        self._state = "closed"  # closed, open, half-open
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """Check if operation can proceed."""
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                # Check if recovery timeout has elapsed
                if self._last_failure_time and (
                    time.monotonic() - self._last_failure_time >= self.recovery_timeout
                ):
                    self._state = "half-open"
                    logger.info("CircuitBreaker: Transitioning to half-open state")
                    return True
                return False
            return True  # half-open allows limited calls

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            if self._state == "half-open":
                self._state = "closed"
                logger.info("CircuitBreaker: Transitioning to closed state")
            self._failures = 0
            self._last_failure_time = None

    def record_failure(self) -> bool:
        """Record a failed operation. Returns True if circuit opened."""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == "half-open":
                self._state = "open"
                logger.warning("CircuitBreaker: Transitioning to open state (half-open failure)")
                return True

            if self._failures >= self.failure_threshold:
                if self._state != "open":
                    self._state = "open"
                    logger.warning(
                        f"CircuitBreaker: Transitioning to open state ({self._failures} failures)"
                    )
                return True
            return False

    @property
    def state(self) -> str:
        return self._state


# Global circuit breaker for LLM calls
_llm_circuit_breaker = CircuitBreaker(
    failure_threshold=int(os.environ.get("VERIFLOW_CB_THRESHOLD", "5")),
    recovery_timeout=float(os.environ.get("VERIFLOW_CB_RECOVERY", "60.0")),
)


# Global connection pool instance
_connection_pool = LLMConnectionPool()


def get_connection_pool() -> LLMConnectionPool:
    """Get the global connection pool instance."""
    return _connection_pool


# ── Windows PATH fix ────────────────────────────────────────────────────

_WINDOWS_EXTRA_PATHS = [
    str(Path.home() / "AppData" / "Roaming" / "npm"),
    r"C:\Program Files\nodejs",
]


def _get_enriched_env() -> dict[str, str]:
    """Return os.environ with Windows-specific PATH additions."""
    env = dict(os.environ)
    extra = ";".join(_WINDOWS_EXTRA_PATHS)
    env["PATH"] = env.get("PATH", "") + ";" + extra
    return env


def _find_claude_cli() -> str | None:
    """Find claude CLI, with Windows-aware PATH enrichment."""
    import shutil

    env = _get_enriched_env()
    for name in ["claude.cmd", "claude.bat", "claude.exe", "claude"]:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found

    # Fallback: check known paths directly
    for p_str in _WINDOWS_EXTRA_PATHS:
        for ext in [".cmd", ".bat", ".exe", ""]:
            candidate = Path(p_str) / f"claude{ext}"
            if candidate.exists():
                return str(candidate)
    return None


# ── Configuration ───────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """LLM backend configuration. Persisted in Gradio session state."""

    backend: str = "claude_cli"  # "claude_cli" | "openai"
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def get_effective_model(self) -> str:
        """Return the model name, falling back to defaults."""
        if self.model:
            return self.model
        # Default depends on backend
        default = "claude-sonnet-4-6" if self.backend == "claude_cli" else "gpt-4o"
        return os.environ.get(
            "VERIFLOW_MODEL",
            os.environ.get("OPENAI_MODEL", default),
        )


# ── System prompt ───────────────────────────────────────────────────────

CHAT_SYSTEM_PROMPT = """\
You are VeriFlow-Agent, an expert RTL design assistant. You help engineers \
design digital circuits by analyzing requirements, generating Verilog code, \
and running verification.

Your capabilities:
- Architecture analysis and micro-architecture design
- Timing model creation
- Synthesizable Verilog RTL code generation
- Lint checking (Icarus Verilog)
- Functional simulation
- Logic synthesis (Yosys)

Conversation guidelines:
- When the user describes a specific circuit they want designed, tell them you'll \
start the RTL design pipeline. Use phrases like "I'll start the design pipeline" \
so the system can detect it.
- For general questions about digital design, Verilog, FPGA, or EDA tools, answer directly.
- Be concise and technical. Use code examples when helpful.
- If the user's request is ambiguous, ask clarifying questions before starting the pipeline.
"""


# ── LLM callers ─────────────────────────────────────────────────────────


def call_llm(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str = CHAT_SYSTEM_PROMPT,
) -> str:
    """Call the configured LLM backend and return the response text.

    Args:
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
        config: LLM configuration.
        system_prompt: System prompt to prepend.

    Returns:
        LLM response text.

    Raises:
        RuntimeError: If the LLM call fails.
    """
    if config.backend == "claude_cli":
        return _call_claude_cli(messages, config, system_prompt)
    else:
        # All backends (openai, anthropic alias, etc.) use OpenAI-compatible format
        return _call_openai(messages, config, system_prompt)


def call_llm_stream(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str = CHAT_SYSTEM_PROMPT,
    tools: list[dict] | None = None,
) -> Generator[str | dict, None, None]:
    """Stream LLM response, yielding text chunks or tool call dicts.

    When tools is provided, the LLM may return tool_calls instead of text.
    In streaming mode, tool calls are accumulated and yielded as complete
    dicts when the stream ends.

    Yields:
        str: Text content chunks
        dict: Tool call with keys: id, name, arguments (JSON string)
    """
    if config.backend == "claude_cli":
        yield from _stream_claude_cli(messages, config, system_prompt)
    else:
        # All backends use OpenAI-compatible streaming format
        yield from _stream_openai(messages, config, system_prompt, tools=tools)


# ── Backend implementations ─────────────────────────────────────────────


# ── Claude CLI backend ──────────────────────────────────────────────────


def _build_claude_cli_prompt(
    messages: list[dict[str, str]],
    system_prompt: str = "",
) -> str:
    """Concatenate chat messages into a single prompt string for claude -p."""
    parts: list[str] = []
    if system_prompt:
        parts.append(f"[System]\n{system_prompt}")
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"[System]\n{content}")
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
        else:
            parts.append(f"[User]\n{content}")
    return "\n\n".join(parts)


def _call_claude_cli(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str = CHAT_SYSTEM_PROMPT,
) -> str:
    """Call Claude CLI in non-streaming mode. Returns full response text.

    Pipes prompt via stdin to avoid Windows command-line length limits.
    """
    import json as _json

    cli_path = _find_claude_cli()
    if not cli_path:
        raise RuntimeError(
            "Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code\n"
            "Or set VERIFLOW_LLM_BACKEND=openai"
        )

    prompt = _build_claude_cli_prompt(messages, system_prompt)
    model = config.get_effective_model() or "claude-sonnet-4-6"

    cmd = [
        cli_path,
        "-p",
        "--output-format", "json",
        "--model", model,
    ]

    logger.info("Claude CLI call: %s --model %s (%d chars, stdin)", cli_path, model, len(prompt))

    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude CLI timed out after 600s")
    except FileNotFoundError:
        raise RuntimeError(f"Claude CLI not found at: {cli_path}")

    if result.returncode != 0:
        raise RuntimeError(
            f"Claude CLI error (exit {result.returncode}): {result.stderr[:500]}"
        )

    # Parse JSON output
    try:
        obj = _json.loads(result.stdout.strip())
        return obj.get("result", result.stdout)
    except _json.JSONDecodeError:
        # Fallback: return raw stdout
        return result.stdout.strip()


def _stream_claude_cli(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str = CHAT_SYSTEM_PROMPT,
) -> Generator[str, None, None]:
    """Stream Claude CLI output using stream-json format. Yields text chunks.

    Pipes prompt via stdin to avoid Windows command-line length limits.
    """
    import json as _json

    cli_path = _find_claude_cli()
    if not cli_path:
        raise RuntimeError(
            "Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code\n"
            "Or set VERIFLOW_LLM_BACKEND=openai"
        )

    prompt = _build_claude_cli_prompt(messages, system_prompt)
    model = config.get_effective_model() or "claude-sonnet-4-6"

    cmd = [
        cli_path,
        "-p",
        "--output-format", "stream-json",
        "--model", model,
        "--verbose",
    ]

    logger.info("Claude CLI stream: %s --model %s (%d chars, stdin)", cli_path, model, len(prompt))

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        # Pipe prompt via stdin
        proc.stdin.write(prompt)
        proc.stdin.close()

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError:
                yield line
                continue

            msg_type = obj.get("type", "")

            if msg_type == "content_block_delta":
                text = obj.get("delta", {}).get("text", "")
                if text:
                    yield text

            elif msg_type == "result":
                result_text = obj.get("result", "")
                if result_text:
                    yield result_text

            elif msg_type == "error":
                error_msg = obj.get("error", {}).get("message", str(obj))
                raise RuntimeError(f"Claude CLI error: {error_msg}")

        proc.wait()
        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(
                f"Claude CLI exited {proc.returncode}: {stderr[:500]}"
            )

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Claude CLI streaming failed: {e}")


# ── OpenAI backend ──────────────────────────────────────────────────────


def _make_openai_client(config: LLMConfig, *, warmup: bool = False):
    """Build an OpenAI client from config + env vars.

    Uses a bounded client cache to avoid ~1s init overhead per call.

    Args:
        config: LLM configuration
        warmup: If True, start background keep-alive for the connection
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    api_key = (
        config.api_key
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        raise RuntimeError(
            "API key not set. Set OPENAI_API_KEY or configure it in Settings."
        )
    base_url = config.base_url or os.environ.get("OPENAI_BASE_URL") or None

    # ── Client cache (avoids ~1s init per call) ──────────────────────
    cache_key = (api_key, base_url)
    client = _openai_client_cache.get(cache_key)
    if client is None:
        if len(_openai_client_cache) >= _MAX_CLIENT_CACHE_SIZE:
            oldest_key = next(iter(_openai_client_cache))
            del _openai_client_cache[oldest_key]
        # Use configurable timeout - longer for long LLM calls
        # Note: This is a fallback; streaming calls use httpx with _TIMEOUT_READ
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=_TIMEOUT_READ,  # Use read timeout (default 10 min)
        )
        _openai_client_cache[cache_key] = client

    # Configure connection pooling for keep-alive
    pool = get_connection_pool()
    pool.configure(config)

    if warmup:
        pool.start_background_warmup(config)

    return client


# Bounded client cache (keyed by api_key + base_url)
_MAX_CLIENT_CACHE_SIZE = 8
_openai_client_cache: dict[tuple, Any] = {}


def _call_openai(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str,
) -> str:
    """Call LLM via OpenAI-compatible API (non-streaming) with retry logic.

    Uses the same retry and circuit breaker mechanism as _stream_openai.
    """
    # Check circuit breaker
    if not _llm_circuit_breaker.can_execute():
        raise RuntimeError(
            f"LLM circuit breaker is {_llm_circuit_breaker.state}. "
            "Too many consecutive failures. Please wait before retrying."
        )

    last_exception: Exception | None = None

    for attempt in range(_RETRY_MAX_ATTEMPTS + 1):
        client = _make_openai_client(config, warmup=(attempt == 0))

        api_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            msg_copy = {"role": m["role"], "content": m.get("content", "")}
            if "tool_calls" in m:
                msg_copy["tool_calls"] = m["tool_calls"]
            if "tool_call_id" in m:
                msg_copy["tool_call_id"] = m["tool_call_id"]
            if "name" in m:
                msg_copy["name"] = m["name"]
            api_messages.append(msg_copy)

        try:
            response = client.chat.completions.create(
                model=config.get_effective_model(),
                messages=api_messages,
                max_tokens=4096,
            )
            _llm_circuit_breaker.record_success()
            return response.choices[0].message.content or ""

        except Exception as e:
            last_exception = e
            is_retryable, error_category = _is_retryable_error(e)

            logger.warning(
                f"LLM call failed (attempt {attempt + 1}/{_RETRY_MAX_ATTEMPTS + 1}): "
                f"category={error_category}, type={type(e).__name__}, error={e}"
            )

            if is_retryable and attempt < _RETRY_MAX_ATTEMPTS:
                retry_delay = _calculate_retry_delay(attempt)
                logger.info(f"Retrying LLM call in {retry_delay:.2f}s")

                pool = get_connection_pool()
                pool.close()
                time.sleep(retry_delay)
                continue
            else:
                break

    # All retries exhausted
    _llm_circuit_breaker.record_failure()
    raise RuntimeError(
        f"LLM call failed after {_RETRY_MAX_ATTEMPTS + 1} attempts: {last_exception}"
    ) from last_exception


def _calculate_retry_delay(attempt: int, *, jitter: bool = True) -> float:
    """Calculate retry delay with exponential backoff and optional jitter.

    Args:
        attempt: Current retry attempt (0-indexed)
        jitter: Add randomness to prevent thundering herd

    Returns:
        Delay in seconds
    """
    import random

    # Exponential backoff: base * 2^attempt
    delay = _RETRY_BASE_DELAY * (2 ** attempt)
    delay = min(delay, _RETRY_MAX_DELAY)

    if jitter:
        # Add ±25% jitter
        delay *= random.uniform(0.75, 1.25)

    return delay


def _is_retryable_error(e: Exception) -> tuple[bool, str]:
    """Classify if an error is retryable and return the error category.

    Returns:
        Tuple of (is_retryable, error_category)
    """
    import errno

    error_str = str(e).lower()
    error_type = type(e).__name__.lower()

    # Connection errors - always retryable
    if any(kw in error_str for kw in [
        "connection", "connect", "reset", "refused", "aborted",
        "broken pipe", "closed", "network", "unreachable"
    ]):
        # Check for specific error codes
        winerror = getattr(e, 'winerror', None)
        errno_code = getattr(e, 'errno', None)

        if winerror in [10054, 10053, 10060, 10061, 10065]:  # Windows socket errors
            return True, "connection"
        if errno_code in [errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT,
                          errno.ECONNABORTED, errno.EPIPE, errno.ENETUNREACH]:
            return True, "connection"
        return True, "connection"

    # Timeout errors - retryable
    if any(kw in error_str for kw in ["timeout", "timed out"]):
        return True, "timeout"

    # HTTP 5xx errors - retryable
    if "429" in error_str or "too many requests" in error_str:
        return True, "rate_limit"
    if any(code in error_str for code in ["500", "502", "503", "504"]):
        return True, "server_error"

    # SSL errors - retryable (might be transient)
    if any(kw in error_str for kw in ["ssl", "tls", "certificate", "handshake"]):
        return True, "ssl"

    # OpenAI specific errors
    if "apierror" in error_type or "api_connection_error" in error_str:
        return True, "api"

    return False, "unknown"


def _stream_openai(
    messages: list[dict[str, str]],
    config: LLMConfig,
    system_prompt: str,
    tools: list[dict] | None = None,
) -> Generator[str | dict, None, None]:
    """Stream via OpenAI-compatible API with enhanced retry logic.

    Features:
    - Circuit breaker pattern to prevent cascading failures
    - Exponential backoff with jitter
    - Comprehensive error classification
    - Connection pool reset on failure

    When tools is provided, tool calls are accumulated from streaming deltas
    and yielded as complete dicts after the stream ends.

    Yields:
        str: Text content chunks
        dict: Tool call with keys: id, function.name, function.arguments
    """
    # Check circuit breaker
    if not _llm_circuit_breaker.can_execute():
        raise RuntimeError(
            f"LLM circuit breaker is {_llm_circuit_breaker.state}. "
            "Too many consecutive failures. Please wait before retrying."
        )

    last_exception: Exception | None = None

    for attempt in range(_RETRY_MAX_ATTEMPTS + 1):
        client = _make_openai_client(config, warmup=(attempt == 0))

        api_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            # Preserve all keys (tool_calls, tool_call_id, name, etc.)
            msg_copy = {"role": m["role"], "content": m.get("content", "")}
            if "tool_calls" in m:
                msg_copy["tool_calls"] = m["tool_calls"]
            if "tool_call_id" in m:
                msg_copy["tool_call_id"] = m["tool_call_id"]
            if "name" in m:
                msg_copy["name"] = m["name"]
            api_messages.append(msg_copy)

        # Build kwargs — only pass tools if provided
        create_kwargs = dict(
            model=config.get_effective_model(),
            messages=api_messages,
            max_tokens=4096,
            stream=True,
        )
        if tools:
            create_kwargs["tools"] = tools
            create_kwargs["tool_choice"] = "auto"

        try:
            stream = client.chat.completions.create(**create_kwargs)

            # Accumulate tool calls from streaming deltas
            tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}
            chunk_count = 0

            for chunk in stream:
                chunk_count += 1
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # Text content
                if delta.content:
                    yield delta.content

                # Tool calls — accumulate fragments
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {
                                    "name": tc.function.name if tc.function and tc.function.name else "",
                                    "arguments": tc.function.arguments if tc.function and tc.function.arguments else "",
                                },
                            }
                        else:
                            # Append argument fragments
                            if tc.function and tc.function.arguments:
                                tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id

            # Yield accumulated tool calls
            for idx in sorted(tool_calls_acc.keys()):
                tc = tool_calls_acc[idx]
                # Fallback: generate a deterministic ID if API didn't provide one
                if not tc["id"]:
                    tc["id"] = f"call_{idx}_{id(tc)}"
                    logger.debug("Generated fallback tool_call_id: %s", tc["id"])
                logger.debug(
                    "Tool call accumulated: id=%s name=%s args_len=%d",
                    tc["id"],
                    tc["function"]["name"],
                    len(tc["function"]["arguments"]),
                )
                yield {
                    "type": "tool_call",
                    "id": tc["id"],
                    "function": tc["function"],
                }

            # Success! Record it and return
            _llm_circuit_breaker.record_success()
            logger.debug(f"LLM stream completed successfully ({chunk_count} chunks)")
            return

        except Exception as e:
            last_exception = e
            is_retryable, error_category = _is_retryable_error(e)

            # Log detailed error info
            logger.warning(
                f"LLM call failed (attempt {attempt + 1}/{_RETRY_MAX_ATTEMPTS + 1}): "
                f"category={error_category}, type={type(e).__name__}, error={e}"
            )

            # Should we retry?
            if is_retryable and attempt < _RETRY_MAX_ATTEMPTS:
                retry_delay = _calculate_retry_delay(attempt)
                logger.info(
                    f"Retrying LLM call in {retry_delay:.2f}s "
                    f"(attempt {attempt + 2}/{_RETRY_MAX_ATTEMPTS + 1})"
                )

                # Close and recreate the connection pool for fresh connection
                pool = get_connection_pool()
                pool.close()

                time.sleep(retry_delay)
                continue
            else:
                # Not retryable or exhausted retries
                break

    # All retries exhausted or non-retryable error
    _llm_circuit_breaker.record_failure()

    # Provide helpful error message based on error type
    is_retryable, error_category = _is_retryable_error(last_exception)

    if error_category == "timeout":
        raise RuntimeError(
            f"LLM request timed out after {_RETRY_MAX_ATTEMPTS + 1} attempts. "
            f"The server may be overloaded or the request is too complex. "
            f"Consider reducing max_tokens or simplifying the prompt. "
            f"Original error: {last_exception}"
        ) from last_exception
    elif error_category == "rate_limit":
        raise RuntimeError(
            f"LLM rate limit exceeded after {_RETRY_MAX_ATTEMPTS + 1} attempts. "
            f"Please wait a moment before retrying. "
            f"Original error: {last_exception}"
        ) from last_exception
    elif error_category == "connection":
        raise RuntimeError(
            f"LLM connection failed after {_RETRY_MAX_ATTEMPTS + 1} attempts. "
            f"Please check your network connection and API endpoint. "
            f"Original error: {last_exception}"
        ) from last_exception
    else:
        raise RuntimeError(
            f"LLM call failed after {_RETRY_MAX_ATTEMPTS + 1} attempts: {last_exception}"
        ) from last_exception
