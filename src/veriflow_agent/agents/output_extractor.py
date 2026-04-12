"""Streaming output extractor — separates LLM thinking from structured output.

Operates at the TEXT_DELTA level to classify chunks as "thinking" (outside
code fences) or "structured output" (inside code fences, or after a markdown
heading in markdown_after_heading mode).

Design decisions:
- Code fences are PRESERVED in the output so downstream agent regex extraction
  continues to work as a fallback.
- ALL text is still streamed to the TUI (thinking + output) — the user sees
  progress regardless.
- If no structured output is detected, get_output() returns empty string and
  the caller falls back to using the full accumulated text.
"""

from __future__ import annotations

import re

# Pattern to detect opening code fences: ```verilog, ```yaml, ```json, etc.
# Captures the fence type (verilog, yaml, json, v, yml) or empty for bare ```
_OPEN_FENCE_RE = re.compile(r"```(\w*)\s*\n")
_CLOSE_FENCE_RE = re.compile(r"\n```")
# Maximum chars we need to buffer to detect a fence marker across chunk boundaries.
# Longest possible: ```yaml\n = 7 chars, plus some whitespace = ~15
_FENCE_BUF_SIZE = 20


class StreamingOutputExtractor:
    """Tracks code-fence boundaries during streaming to separate thinking from output.

    Usage::

        extractor = StreamingOutputExtractor(fence_types=["verilog"])
        for chunk in stream:
            thinking, output = extractor.feed(chunk)
        result = extractor.get_output()  # clean structured output
    """

    def __init__(
        self,
        fence_types: list[str] | None = None,
        extract_mode: str = "code_fences",
    ) -> None:
        """Initialize the extractor.

        Args:
            fence_types: Which code fence types to extract.
                e.g., ["verilog", "yaml", "json"]. None = extract all fences.
            extract_mode:
                "code_fences" — extract content inside matching code fences.
                "markdown_after_heading" — extract everything from the first
                    ``# `` markdown heading onward (for microarch).
                "all" — passthrough: everything is output (fallback).
        """
        self._fence_types: set[str] | None = (
            set(t.lower() for t in fence_types) if fence_types else None
        )
        self._extract_mode = extract_mode
        # State machine: "thinking" | "in_fence" | "output"
        self._state = "thinking"
        # Buffer for detecting fence markers split across deltas
        self._fence_buf = ""
        # Accumulated parts
        self._output_parts: list[str] = []
        self._thinking_parts: list[str] = []
        # For markdown_after_heading mode: track if heading was found
        self._heading_found = False
        self._heading_search_buf = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        """Feed a delta chunk and classify it.

        Returns:
            (thinking_text, output_text) — the portions of this chunk
            classified as thinking and output respectively.
        """
        if self._extract_mode == "all":
            self._output_parts.append(chunk)
            return ("", chunk)

        if self._extract_mode == "markdown_after_heading":
            return self._feed_markdown(chunk)

        return self._feed_code_fences(chunk)

    # ── Code-fence mode ──────────────────────────────────────────────────

    def _feed_code_fences(self, chunk: str) -> tuple[str, str]:
        """Process chunk in code_fences mode."""
        thinking_parts: list[str] = []
        output_parts: list[str] = []

        # Append to fence buffer for cross-chunk detection
        self._fence_buf += chunk
        # Keep buffer bounded
        if len(self._fence_buf) > _FENCE_BUF_SIZE * 2:
            # Flush excess — we only need the tail for fence detection
            excess = self._fence_buf[:-_FENCE_BUF_SIZE]
            self._fence_buf = self._fence_buf[-_FENCE_BUF_SIZE:]
            # Classify the excess based on current state
            if self._state == "in_fence":
                self._output_parts.append(excess)
                output_parts.append(excess)
            else:
                self._thinking_parts.append(excess)
                thinking_parts.append(excess)

        # Scan the buffer for fence transitions
        pos = 0
        buf = self._fence_buf

        while pos < len(buf):
            if self._state == "thinking":
                # Look for opening fence
                match = _OPEN_FENCE_RE.search(buf, pos)
                if match:
                    fence_type = match.group(1).lower()
                    # Check if this fence type matches our filter
                    if self._fence_types is None or fence_type in self._fence_types or fence_type == "":
                        # Emit everything before the fence as thinking
                        before = buf[pos : match.start()]
                        if before:
                            self._thinking_parts.append(before)
                            thinking_parts.append(before)
                        # The fence opening itself is output (preserve it)
                        fence_opening = buf[match.start() : match.end()]
                        self._output_parts.append(fence_opening)
                        output_parts.append(fence_opening)
                        self._state = "in_fence"
                        pos = match.end()
                    else:
                        # Fence type doesn't match — treat as thinking
                        before = buf[pos : match.end()]
                        self._thinking_parts.append(before)
                        thinking_parts.append(before)
                        pos = match.end()
                        # Don't change state — this isn't a fence we care about
                else:
                    # No fence found — everything from pos onward is thinking
                    # But keep the tail in the buffer in case a fence is split
                    remaining = buf[pos:]
                    if len(remaining) > _FENCE_BUF_SIZE:
                        # Safe to emit the head, keep the tail
                        safe = remaining[:-_FENCE_BUF_SIZE]
                        tail = remaining[-_FENCE_BUF_SIZE:]
                        self._thinking_parts.append(safe)
                        thinking_parts.append(safe)
                        self._fence_buf = tail
                    else:
                        # Keep all in buffer — might be start of a fence
                        # Don't emit yet, will be resolved on next chunk
                        self._fence_buf = remaining
                    break

            elif self._state == "in_fence":
                # Look for closing fence (bare ```)
                match = _CLOSE_FENCE_RE.search(buf, pos)
                if match:
                    # Everything before the closing is output
                    before = buf[pos : match.start()]
                    if before:
                        self._output_parts.append(before)
                        output_parts.append(before)
                    # The closing fence itself is output (preserve it)
                    closing = buf[match.start() : match.end()]
                    self._output_parts.append(closing)
                    output_parts.append(closing)
                    self._state = "thinking"
                    pos = match.end()
                else:
                    # No closing fence — everything from pos onward is output
                    # Keep tail in buffer for split detection
                    remaining = buf[pos:]
                    if len(remaining) > _FENCE_BUF_SIZE:
                        safe = remaining[:-_FENCE_BUF_SIZE]
                        tail = remaining[-_FENCE_BUF_SIZE:]
                        self._output_parts.append(safe)
                        output_parts.append(safe)
                        self._fence_buf = tail
                    else:
                        self._fence_buf = remaining
                    break

        return ("".join(thinking_parts), "".join(output_parts))

    # ── Markdown-after-heading mode ──────────────────────────────────────

    def _feed_markdown(self, chunk: str) -> tuple[str, str]:
        """Process chunk in markdown_after_heading mode.

        Everything before the first ``# `` heading is thinking.
        Everything from the first heading onward is output.
        """
        if self._heading_found:
            self._output_parts.append(chunk)
            return ("", chunk)

        # Search for first markdown heading
        self._heading_search_buf += chunk
        heading_match = re.search(r"\n# |^# ", self._heading_search_buf)

        if heading_match:
            self._heading_found = True
            before = self._heading_search_buf[: heading_match.start()]
            after = self._heading_search_buf[heading_match.start() :]
            if before:
                self._thinking_parts.append(before)
            self._output_parts.append(after)
            self._heading_search_buf = ""
            return (before, after)
        else:
            # No heading found yet — check if we can emit safe prefix as thinking
            # Keep last ~30 chars as buffer (heading could start mid-chunk)
            if len(self._heading_search_buf) > 30:
                safe = self._heading_search_buf[:-30]
                tail = self._heading_search_buf[-30:]
                self._thinking_parts.append(safe)
                self._heading_search_buf = tail
                return (safe, "")
            return ("", "")

    # ── Accessors ────────────────────────────────────────────────────────

    def get_output(self) -> str:
        """Return accumulated structured output."""
        return "".join(self._output_parts)

    def get_thinking(self) -> str:
        """Return accumulated thinking text."""
        return "".join(self._thinking_parts)

    def has_output(self) -> bool:
        """Whether any structured output was captured."""
        return len(self._output_parts) > 0

    def flush(self) -> None:
        """Flush any remaining buffered text.

        Call this after the stream ends to ensure nothing is stuck in
        the fence buffer.
        """
        if self._extract_mode == "code_fences" and self._fence_buf:
            # Whatever is in the buffer belongs to the current state
            if self._state == "in_fence":
                self._output_parts.append(self._fence_buf)
            else:
                self._thinking_parts.append(self._fence_buf)
            self._fence_buf = ""

        if self._extract_mode == "markdown_after_heading" and self._heading_search_buf:
            if not self._heading_found:
                # Never found a heading — treat all as thinking
                self._thinking_parts.append(self._heading_search_buf)
            else:
                self._output_parts.append(self._heading_search_buf)
            self._heading_search_buf = ""
