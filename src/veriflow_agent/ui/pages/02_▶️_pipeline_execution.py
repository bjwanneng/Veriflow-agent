"""Pipeline Execution page - run RTL design stages with progress tracking."""

import json
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from veriflow_agent.ui.components.feedback_loop_viz import render_feedback_loop_viz
from veriflow_agent.ui.components.error_history_timeline import render_error_history_timeline
from veriflow_agent.ui.components.debugger_status_panel import render_debugger_status_panel

st.set_page_config(page_title="Pipeline Execution - VeriFlow-Agent", page_icon="▶️")

# Raw Terminal Aesthetic CSS
st.markdown("""
<style>
/* Global Raw Terminal Aesthetic */
.stApp {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
}

.main .block-container {
    background: transparent;
    max-width: 1400px;
    padding: 2rem;
}

/* Headers */
h1, h2, h3 {
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace !important;
    color: #00d4ff !important;
    text-transform: uppercase;
    letter-spacing: 2px;
}

h1 {
    font-size: 18px !important;
    border-bottom: 2px solid #00d4ff;
    padding-bottom: 10px;
}

h2 {
    font-size: 14px !important;
    border-bottom: 1px solid #30363d;
    padding-bottom: 8px;
}

h3 {
    font-size: 12px !important;
}

/* Text and markdown */
p, span, div {
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
}

/* Buttons */
.stButton > button {
    background: linear-gradient(135deg, #1f6feb 0%, #388bfd 100%);
    color: white;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    transition: all 0.2s ease;
}

.stButton > button:hover {
    background: linear-gradient(135deg, #388bfd 0%, #58a6ff 100%);
    box-shadow: 0 0 15px rgba(56, 139, 253, 0.4);
    transform: translateY(-1px);
}

.stButton > button:active {
    transform: translateY(0);
}

/* Primary button variant */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #238636 0%, #2ea043 100%);
}

.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #2ea043 0%, #3fb950 100%);
    box-shadow: 0 0 15px rgba(46, 160, 67, 0.4);
}

/* Info boxes */
.stAlert {
    background: rgba(31, 111, 235, 0.1);
    border: 1px solid rgba(31, 111, 235, 0.3);
    border-radius: 6px;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 11px;
}

.stAlert[data-baseweb="notification"] {
    background: rgba(31, 111, 235, 0.1);
}

/* Error/Warning/Success variants */
.element-container .stAlert:nth-child(1) {
    border-color: #ff4444;
    background: rgba(255, 68, 68, 0.1);
}

.element-container .stAlert:nth-child(2) {
    border-color: #ffd700;
    background: rgba(255, 215, 0, 0.1);
}

.element-container .stAlert:nth-child(3) {
    border-color: #00ff88;
    background: rgba(0, 255, 136, 0.1);
}

/* Expander/Details */
.streamlit-expanderHeader {
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 11px;
    color: #888888;
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 8px 12px;
}

.streamlit-expanderContent {
    background: rgba(0, 0, 0, 0.1);
    border: 1px solid #30363d;
    border-top: none;
    border-radius: 0 0 4px 4px;
    padding: 12px;
}

/* Progress bars */
.stProgress > div > div {
    background: linear-gradient(90deg, #238636 0%, #2ea043 100%);
    border-radius: 2px;
}

.stProgress > div {
    background: #30363d;
    border-radius: 2px;
}

/* Text inputs and selects */
.stTextInput > div > div > input,
.stSelectbox > div > div > select {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 4px;
    color: #e6edf3;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 11px;
    padding: 8px 12px;
}

.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus {
    border-color: #58a6ff;
    box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.1);
}

/* Dividers */
hr {
    border: none;
    border-top: 1px solid #30363d;
    margin: 20px 0;
}

/* Scrollbars */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: #0d1117;
}

::-webkit-scrollbar-thumb {
    background: #30363d;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: #484f58;
}
</style>
""", unsafe_allow_html=True)

    # Render the feedback loop visualization
    render_feedback_loop_viz(
        current_stage=current_stage,
        completed_stages=completed_stages,
        failed_stages=failed_stages,
        retry_counts=retry_counts,
        feedback_source=feedback_source
    )

    # Render error history if in retry loop
    if feedback_source and current_stage in ["debugger", feedback_source]:
        # Get error history from session state
        exec_state = st.session_state.get("execution_state", {})
        error_history = exec_state.get("error_history", {}).get(feedback_source, [])
        current_retry = retry_counts.get(feedback_source, 0)

        render_error_history_timeline(
            error_history=error_history,
            checkpoint_name=feedback_source,
            current_attempt=current_retry + 1 if current_stage == "debugger" else current_retry,
            max_attempts=3
        )

    # Render debugger status panel
    files_analyzed = []  # Could be passed from debugger agent
    debugger_logs = ""  # Could be passed from debugger agent

    render_debugger_status_panel(
        current_stage=current_stage,
        feedback_source=feedback_source,
        retry_counts=retry_counts,
        debugger_output=debugger_logs,
        files_being_analyzed=files_analyzed,
        llm_backend="claude_cli"
    )


# Wrapper function for easy import
__all__ = ['render_pipeline_viz']
