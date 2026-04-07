"""Error History Timeline Component.

Shows the last 3 error attempts for the current checkpoint in a vertical timeline.
Part of the Raw Terminal Aesthetic design system.
"""

from typing import List, Optional

import streamlit as st


def render_error_history_timeline(
    error_history: List[str],
    checkpoint_name: str,
    current_attempt: int = 0,
    max_attempts: int = 3
):
    """Render the error history timeline.

    Args:
        error_history: List of error messages from previous attempts
        checkpoint_name: Name of current checkpoint (lint/sim/synth)
        current_attempt: Current attempt number (0 if not in retry)
        max_attempts: Maximum retry attempts (default 3)
    """
    # CSS for Raw Terminal Aesthetic
    st.markdown("""
    <style>
    .error-history-container {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-radius: 8px;
        padding: 15px;
        margin: 10px 0;
        border: 1px solid #0f3460;
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    }

    .error-history-title {
        font-size: 11px;
        color: #00d4ff;
        text-transform: uppercase;
        letter-spacing: 2px;
        margin-bottom: 12px;
        border-bottom: 1px solid #0f3460;
        padding-bottom: 6px;
    }

    .timeline {
        position: relative;
        padding-left: 20px;
    }

    .timeline::before {
        content: '';
        position: absolute;
        left: 5px;
        top: 0;
        bottom: 0;
        width: 2px;
        background: linear-gradient(180deg, #00d4ff 0%, #0f3460 100%);
    }

    .timeline-item {
        position: relative;
        margin-bottom: 12px;
        padding: 8px 10px;
        background: rgba(0, 0, 0, 0.2);
        border-radius: 4px;
        border-left: 3px solid;
    }

    .timeline-item::before {
        content: '';
        position: absolute;
        left: -17px;
        top: 12px;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: currentColor;
        border: 2px solid #1a1a2e;
    }

    .timeline-item.error {
        border-left-color: #ff4444;
        color: #ff4444;
    }

    .timeline-item.retry {
        border-left-color: #ffd700;
        color: #ffd700;
    }

    .timeline-item.resolved {
        border-left-color: #00ff88;
        color: #00ff88;
    }

    .timeline-item.current {
        border-left-color: #00d4ff;
        color: #00d4ff;
        animation: pulse 2s infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
    }

    .attempt-header {
        font-size: 9px;
        font-weight: bold;
        margin-bottom: 4px;
        display: flex;
        justify-content: space-between;
    }

    .error-summary {
        font-size: 9px;
        color: #aaaaaa;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .error-detail {
        font-size: 8px;
        color: #888888;
        margin-top: 4px;
        padding-top: 4px;
        border-top: 1px dashed #333;
        white-space: pre-wrap;
        word-break: break-all;
        max-height: 100px;
        overflow-y: auto;
    }

    .no-errors {
        text-align: center;
        color: #00ff88;
        font-size: 10px;
        padding: 20px;
    }

    .spinner {
        display: inline-block;
        width: 8px;
        height: 8px;
        border: 2px solid currentColor;
        border-right-color: transparent;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-right: 6px;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    </style>
    """, unsafe_allow_html=True)

    # Check if there's error history to display
    if not error_history and current_attempt == 0:
        st.markdown('<div class="error-history-container">', unsafe_allow_html=True)
        st.markdown('<div class="error-history-title">Error History</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="no-errors">✓ No errors for {checkpoint_name}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        return

    # Render timeline
    st.markdown('<div class="error-history-container">', unsafe_allow_html=True)
    st.markdown(f'<div class="error-history-title">Error History - {checkpoint_name.upper()}</div>', unsafe_allow_html=True)

    st.markdown('<div class="timeline">', unsafe_allow_html=True)

    # Show previous attempts
    for i, error_msg in enumerate(error_history[-3:], 1):  # Last 3 errors
        # Determine status based on position
        if i < len(error_history):
            status_class = "error"
            status_label = "FAILED"
        else:
            status_class = "resolved"
            status_label = "RESOLVED"

        # Truncate error for summary
        error_summary = error_msg[:80].replace('\n', ' ') + "..." if len(error_msg) > 80 else error_msg.replace('\n', ' ')

        st.markdown(f"""
        <div class="timeline-item {status_class}">
            <div class="attempt-header">
                <span>Attempt {i}</span>
                <span>{status_label}</span>
            </div>
            <div class="error-summary">{error_summary}</div>
            <div class="error-detail">{error_msg}</div>
        </div>
        """, unsafe_allow_html=True)

    # Show current attempt if in progress
    if current_attempt > 0:
        st.markdown(f"""
        <div class="timeline-item current">
            <div class="attempt-header">
                <span><span class="spinner"></span>Attempt {current_attempt}</span>
                <span>IN PROGRESS</span>
            </div>
            <div class="error-summary">Debugger analyzing and applying fixes...</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)  # Close timeline
    st.markdown('</div>', unsafe_allow_html=True)  # Close container
