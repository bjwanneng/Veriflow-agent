"""Debugger Status Panel Component.

Shows DebuggerAgent's current status during execution.
Displays files being analyzed, context, progress, LLM backend, and real-time logs.

Raw Terminal Aesthetic:
- Dark background with terminal-like styling
- Monospace fonts
- ANSI color inspired palette
"""

from typing import List, Optional

import streamlit as st


def render_debugger_status_panel(
    current_stage: Optional[str],
    feedback_source: Optional[str],
    retry_counts: dict,
    debugger_output: Optional[str] = None,
    files_being_analyzed: Optional[List[str]] = None,
    llm_backend: str = "claude_cli"
):
    """Render the debugger status panel.

    Args:
        current_stage: Currently executing stage ID
        feedback_source: Which checkpoint triggered debugger (lint/sim/synth)
        retry_counts: Dict of checkpoint ID -> retry count
        debugger_output: Real-time log output from debugger
        files_being_analyzed: List of RTL files being analyzed
        llm_backend: LLM backend being used (claude_cli/anthropic/langchain)
    """
    # Check if debugger is active
    is_debugger_active = (
        current_stage == "debugger" or
        (feedback_source and feedback_source in ["lint", "sim", "synth"])
    )

    # CSS for Raw Terminal Aesthetic
    st.markdown("""
    <style>
    .debugger-panel-container {
        background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
        border-radius: 8px;
        padding: 15px;
        margin: 10px 0;
        border: 1px solid #30363d;
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }

    .debugger-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
        padding-bottom: 10px;
        border-bottom: 1px solid #30363d;
    }

    .debugger-title {
        font-size: 11px;
        font-weight: bold;
        color: #00d4ff;
        text-transform: uppercase;
        letter-spacing: 1px;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .debugger-status {
        font-size: 9px;
        padding: 3px 8px;
        border-radius: 3px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    .debugger-status.active {
        background: rgba(0, 212, 255, 0.15);
        color: #00d4ff;
        border: 1px solid rgba(0, 212, 255, 0.3);
    }

    .debugger-status.idle {
        background: rgba(102, 102, 102, 0.1);
        color: #888888;
        border: 1px solid rgba(102, 102, 102, 0.2);
    }

    .debugger-info-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
        margin-bottom: 12px;
    }

    .info-item {
        background: rgba(0, 0, 0, 0.2);
        padding: 8px 10px;
        border-radius: 4px;
        border-left: 2px solid #30363d;
    }

    .info-label {
        font-size: 8px;
        color: #888888;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 3px;
    }

    .info-value {
        font-size: 10px;
        color: #e6edf3;
        font-weight: 500;
    }

    .info-value.highlight {
        color: #00d4ff;
    }

    .files-section {
        margin-bottom: 12px;
    }

    .section-label {
        font-size: 8px;
        color: #888888;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 6px;
    }

    .files-list {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
    }

    .file-tag {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 8px;
        background: rgba(0, 212, 255, 0.1);
        border: 1px solid rgba(0, 212, 255, 0.2);
        border-radius: 3px;
        font-size: 9px;
        color: #00d4ff;
    }

    .terminal-window {
        background: #0d1117;
        border-radius: 4px;
        border: 1px solid #30363d;
        overflow: hidden;
    }

    .terminal-header {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 10px;
        background: #161b22;
        border-bottom: 1px solid #30363d;
    }

    .terminal-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
    }

    .terminal-dot.red { background: #ff5f56; }
    .terminal-dot.yellow { background: #ffbd2e; }
    .terminal-dot.green { background: #27c93f; }

    .terminal-title {
        font-size: 9px;
        color: #888888;
        margin-left: 6px;
    }

    .terminal-body {
        padding: 10px;
        max-height: 120px;
        overflow-y: auto;
        font-size: 9px;
        line-height: 1.5;
        color: #e6edf3;
    }

    .terminal-line {
        margin: 2px 0;
        white-space: pre-wrap;
        word-break: break-all;
    }

    .terminal-line.prompt {
        color: #00d4ff;
    }

    .terminal-line.output {
        color: #888888;
    }

    .terminal-line.error {
        color: #ff4444;
    }

    .terminal-line.success {
        color: #00ff88;
    }

    .spinner-inline {
        display: inline-block;
        width: 10px;
        height: 10px;
        border: 2px solid #00d4ff;
        border-right-color: transparent;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-right: 6px;
        vertical-align: middle;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }

    .empty-state {
        text-align: center;
        padding: 20px;
        color: #666666;
        font-size: 10px;
    }
    </style>
    """, unsafe_allow_html=True)

    # Only render if debugger is active or has been active
    if not is_debugger_active and not debugger_output:
        return

    # Get current attempt number
    current_retry = retry_counts.get(feedback_source, 0) if feedback_source else 0

    # Container
    st.markdown('<div class="debugger-panel-container">', unsafe_allow_html=True)

    # Header
    status_class = "active" if is_debugger_active else "idle"
    status_text = "ACTIVE" if is_debugger_active else "IDLE"

    st.markdown(f"""
    <div class="debugger-header">
        <div class="debugger-title">
            <span>🔧</span> DEBUGGER AGENT
        </div>
        <div class="debugger-status {status_class}">
            {status_text}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Info grid
    st.markdown('<div class="debugger-info-grid">', unsafe_allow_html=True)

    # Feedback source
    source_display = feedback_source.upper() if feedback_source else "N/A"
    st.markdown(f"""
    <div class="info-item">
        <div class="info-label">Feedback Source</div>
        <div class="info-value highlight">{source_display}</div>
    </div>
    """, unsafe_allow_html=True)

    # Retry count
    st.markdown(f"""
    <div class="info-item">
        <div class="info-label">Retry Progress</div>
        <div class="info-value">{current_retry} / 3</div>
    </div>
    """, unsafe_allow_html=True)

    # LLM Backend
    st.markdown(f"""
    <div class="info-item">
        <div class="info-label">LLM Backend</div>
        <div class="info-value">{llm_backend}</div>
    </div>
    """, unsafe_allow_html=True)

    # Error history count
    error_count = len(error_history) if error_history else 0
    st.markdown(f"""
    <div class="info-item">
        <div class="info-label">Error History</div>
        <div class="info-value">{error_count} entries</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)  # Close info grid

    # Files being analyzed
    if files_being_analyzed:
        st.markdown('<div class="files-section">', unsafe_allow_html=True)
        st.markdown('<div class="section-label">Files Being Analyzed</div>', unsafe_allow_html=True)
        st.markdown('<div class="files-list">', unsafe_allow_html=True)

        for file in files_being_analyzed[:5]:  # Show max 5 files
            file_name = file.split('/')[-1].split('\\')[-1]  # Get just filename
            st.markdown(f'<span class="file-tag">📄 {file_name}</span>', unsafe_allow_html=True)

        if len(files_being_analyzed) > 5:
            st.markdown(f'<span class="file-tag">+{len(files_being_analyzed) - 5} more</span>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)  # Close files list
        st.markdown('</div>', unsafe_allow_html=True)  # Close files section

    # Terminal window with logs
    if debugger_output or is_debugger_active:
        st.markdown('<div class="terminal-window">', unsafe_allow_html=True)

        # Terminal header
        st.markdown("""
        <div class="terminal-header">
            <div class="terminal-dot red"></div>
            <div class="terminal-dot yellow"></div>
            <div class="terminal-dot green"></div>
            <div class="terminal-title">debugger.log</div>
        </div>
        """, unsafe_allow_html=True)

        # Terminal body with logs
        st.markdown('<div class="terminal-body">', unsafe_allow_html=True)

        if debugger_output:
            # Parse and format log lines
            log_lines = debugger_output.strip().split('\n')[-15:]  # Last 15 lines

            for line in log_lines:
                line = line.strip()
                if not line:
                    continue

                # Determine line type based on content
                if line.startswith('$') or line.startswith('>'):
                    line_class = "prompt"
                elif 'error' in line.lower() or 'fail' in line.lower():
                    line_class = "error"
                elif 'success' in line.lower() or 'pass' in line.lower():
                    line_class = "success"
                else:
                    line_class = "output"

                st.markdown(f'<div class="terminal-line {line_class}">{line}</div>', unsafe_allow_html=True)

        elif is_debugger_active:
            # Show spinner when active but no output yet
            st.markdown("""
            <div class="terminal-line output">
                <span class="spinner-inline"></span>Initializing debugger agent...
            </div>
            <div class="terminal-line output">Loading error context from previous attempts...</div>
            <div class="terminal-line output">Preparing fix strategy...</div>
            """, unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)  # Close terminal body
        st.markdown('</div>', unsafe_allow_html=True)  # Close terminal window

    st.markdown('</div>', unsafe_allow_html=True)  # Close container
