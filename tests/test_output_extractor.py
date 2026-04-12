"""Tests for StreamingOutputExtractor.

Validates the thinking vs structured output separation logic.
"""

import pytest

from veriflow_agent.agents.output_extractor import StreamingOutputExtractor


class TestCodeFences:
    """Test code-fence extraction mode."""

    def test_simple_verilog_fence(self):
        """Extract Verilog from a simple ```verilog block."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        thinking, output = extractor.feed(
            "Okay, let me generate the Verilog.\n"
            "```verilog\nmodule test; endmodule\n```\n"
            "Done!"
        )

        assert "Okay, let me generate" in thinking
        assert "```verilog" in output
        assert "module test; endmodule" in output
        assert "```" in output
        assert extractor.has_output()

    def test_thinking_only_no_fences(self):
        """No code fences present — all content is thinking."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        thinking, output = extractor.feed("Just some thinking text here.")

        assert "Just some thinking" in thinking
        assert output == ""
        assert not extractor.has_output()

    def test_multiple_code_blocks(self):
        """Extract from multiple code fences."""
        extractor = StreamingOutputExtractor(fence_types=["yaml", "json"])

        text = (
            "Here's the YAML:\n"
            "```yaml\ndesign: test\n```\n"
            "And JSON:\n"
            "```json\n{\"key\": \"value\"}\n```\n"
            "Done."
        )
        thinking, output = extractor.feed(text)

        # Both blocks should be in output
        assert "```yaml" in output
        assert "```json" in output
        assert "design: test" in output
        assert "key" in output
        # The narrative between blocks should be in thinking
        assert "Here's the YAML" in thinking or "And JSON" in thinking

    def test_fence_split_across_chunks(self):
        """Fence marker split across delta chunks — should still detect."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        # Simulate split: chunk1 ends with ```ve, chunk2 starts with rilog\n...
        thinking1, output1 = extractor.feed("Some thinking before ```ve")
        thinking2, output2 = extractor.feed("rilog\nmodule test; endmodule\n```")

        # When we flush, the fence should be detected
        extractor.flush()

        full_output = extractor.get_output()
        assert "module test; endmodule" in full_output
        assert extractor.has_output()

    def test_unmatched_fence_type(self):
        """Fence types not in our list should be treated as thinking."""
        extractor = StreamingOutputExtractor(fence_types=["json"])

        thinking, output = extractor.feed(
            "```yaml\nkey: value\n```\n"
            "```json\n{\"key\": \"value\"}\n```"
        )

        # YAML fence should be in thinking, JSON in output
        full_thinking = extractor.get_thinking()
        full_output = extractor.get_output()
        assert "yaml" in full_thinking or "yaml" not in full_output
        assert "```json" in full_output

    def test_close_fence_in_middle(self):
        """Closing fence in the middle should transition back to thinking."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        text = (
            "Start\n"
            "```verilog\nmodule a; endmodule\n```\n"
            "After code\n"
            "```verilog\nmodule b; endmodule\n```\n"
            "End"
        )
        thinking, output = extractor.feed(text)
        extractor.flush()

        full_output = extractor.get_output()
        full_thinking = extractor.get_thinking()
        # Both code blocks should be in output
        assert "module a" in full_output
        assert "module b" in full_output
        # Narrative should be in thinking
        assert "Start" in full_thinking or "After code" in full_thinking or "End" in full_thinking


class TestMarkdownAfterHeading:
    """Test markdown_after_heading extraction mode."""

    def test_heading_detection(self):
        """Everything from first # heading onward is output."""
        extractor = StreamingOutputExtractor(extract_mode="markdown_after_heading")

        thinking, output = extractor.feed(
            "Let me plan this.\n"
            "First, I'll outline the sections.\n"
            "# Micro-Architecture\n\n"
            "## Module: test\nContent here."
        )

        assert "Let me plan" in thinking
        assert "First, I'll outline" in thinking
        assert "# Micro-Architecture" in output
        assert "## Module" in output
        assert extractor.has_output()

    def test_no_heading_all_thinking(self):
        """If no heading found, everything is thinking."""
        extractor = StreamingOutputExtractor(extract_mode="markdown_after_heading")

        extractor.feed("Just some planning text.")
        extractor.flush()

        assert not extractor.has_output()
        assert "planning text" in extractor.get_thinking()

    def test_heading_at_start(self):
        """Heading at very start — everything is output."""
        extractor = StreamingOutputExtractor(extract_mode="markdown_after_heading")

        thinking, output = extractor.feed("# Heading\n\nContent")

        assert thinking == ""  # Nothing before heading
        assert "# Heading" in output
        assert "Content" in output


class TestExtractModeAll:
    """Test 'all' passthrough mode."""

    def test_all_mode_passthrough(self):
        """'all' mode treats everything as output."""
        extractor = StreamingOutputExtractor(extract_mode="all")

        thinking, output = extractor.feed("All of this is output.")

        assert thinking == ""
        assert "All of this is output." in output
        assert extractor.has_output()


class TestFlush:
    """Test flush behavior at end of stream."""

    def test_flush_pending_fence_buffer(self):
        """Fence detection buffer should be flushed on stream end."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        # Partial fence at end of stream
        extractor.feed("```verilo")  # incomplete
        extractor.flush()

        # The partial marker should be classified (as thinking since incomplete)
        full_thinking = extractor.get_thinking()
        assert "```verilo" in full_thinking

    def test_flush_pending_heading_buffer(self):
        """Heading search buffer should be flushed on stream end."""
        extractor = StreamingOutputExtractor(extract_mode="markdown_after_heading")

        extractor.feed("Short text without heading")
        extractor.flush()

        # All should be thinking (no heading found)
        assert not extractor.has_output()
        assert "Short text" in extractor.get_thinking()


class TestMultipleChunks:
    """Test streaming across multiple chunks."""

    def test_incremental_feed(self):
        """Feeding chunk by chunk should accumulate correctly."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        chunks = [
            "Okay, ",
            "let me ",
            "start.\n",
            "```verilog\n",
            "module ",
            "test; ",
            "endmodule\n",
            "```",
        ]

        all_thinking = []
        all_output = []
        for chunk in chunks:
            t, o = extractor.feed(chunk)
            all_thinking.append(t)
            all_output.append(o)

        extractor.flush()

        full_output = extractor.get_output()
        assert "module test; endmodule" in full_output
        assert "```verilog" in full_output
        assert "```" in full_output  # closing fence


class TestCaseInsensitivity:
    """Test case-insensitive fence type matching."""

    def test_uppercase_fence_type(self):
        """Fence types should be matched case-insensitively."""
        extractor = StreamingOutputExtractor(fence_types=["Verilog", "JSON"])

        thinking, output = extractor.feed(
            "```verilog\nmodule test; endmodule\n```\n"  # lowercase in text
            "```json\n{}\n```"
        )

        assert "verilog" in output.lower()
        assert "json" in output.lower()


class TestEmptyAndEdgeCases:
    """Test empty and edge cases."""

    def test_empty_string(self):
        """Empty string should be handled gracefully."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        thinking, output = extractor.feed("")

        assert thinking == ""
        assert output == ""
        assert not extractor.has_output()

    def test_only_whitespace(self):
        """Whitespace-only string should be handled."""
        extractor = StreamingOutputExtractor(fence_types=["verilog"])

        thinking, output = extractor.feed("   \n\n   ")

        # Whitespace is accumulated but not classified as meaningful
        extractor.flush()
        # Should end up in thinking (no fence detected)
        assert not extractor.has_output()

    def test_bare_fences_no_type(self):
        """Bare ``` fences without type specifier."""
        extractor = StreamingOutputExtractor(fence_types=[""])

        thinking, output = extractor.feed(
            "Before\n```\nplain text\n```\nAfter"
        )

        assert "plain text" in output
        assert "```" in output
