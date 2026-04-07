"""Feedback Loop Visualizer Component.

Visualizes the 9-stage RTL pipeline with dynamic feedback loops.
Shows how lint/sim/synth failures route through Debugger back to Lint.

Raw Terminal Aesthetic:
- Dark background (#1a1a2e)
- Monospace fonts
- High contrast colors: green=pass, red=fail, yellow=retry, blue=active
"""

from typing import Optional

import streamlit as st

# Stage definitions with positions and connections
STAGES = [
    {"id": "architect", "name": "Architect", "icon": "🏗️", "x": 0, "y": 0},
    {"id": "microarch", "name": "MicroArch", "icon": "📐", "x": 1, "y": 0},
    {"id": "timing", "name": "Timing", "icon": "⏱️", "x": 2, "y": 0},
    {"id": "coder", "name": "Coder", "icon": "💻", "x": 3, "y": 0},
    {"id": "skill_d", "name": "SkillD", "icon": "🔍", "x": 4, "y": 0},
    {"id": "lint", "name": "Lint", "icon": "🔎", "x": 5, "y": 0, "checkpoint": True},
    {"id": "sim", "name": "Sim", "icon": "🧪", "x": 6, "y": 0, "checkpoint": True},
    {"id": "synth", "name": "Synth", "icon": "🔧", "x": 7, "y": 0, "checkpoint": True},
]

FEEDBACK_CHECKPOINTS = ["lint", "sim", "synth"]


def get_stage_color(stage_id: str, current_stage: Optional[str],
                    completed_stages: list, failed_stages: list,
                    retry_counts: dict) -> str:
    """Get color for stage based on state."""
    if stage_id == current_stage:
        return "#00d4ff"  # Cyan blue for active
    elif stage_id in failed_stages:
        if stage_id in FEEDBACK_CHECKPOINTS and retry_counts.get(stage_id, 0) < 3:
            return "#ffd700"  # Gold yellow for retry
        return "#ff4444"  # Red for fail
    elif stage_id in completed_stages:
        return "#00ff88"  # Green for pass
    else:
        return "#666666"  # Gray for pending


def render_feedback_loop_viz(
    current_stage: Optional[str],
    completed_stages: list,
    failed_stages: list,
    retry_counts: dict,
    feedback_source: Optional[str] = None
):
    """Render the feedback loop visualization.

    Args:
        current_stage: Currently executing stage ID
        completed_stages: List of completed stage IDs
        failed_stages: List of failed stage IDs
        retry_counts: Dict of checkpoint ID -> retry count
        feedback_source: Which checkpoint triggered debugger (lint/sim/synth)
    """
    # CSS for Raw Terminal Aesthetic
    st.markdown("""
    <style>
    .feedback-loop-container {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-radius: 8px;
        padding: 20px;
        margin: 10px 0;
        border: 1px solid #0f3460;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
    }

    .pipeline-title {
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
        font-size: 12px;
        color: #00d4ff;
        text-transform: uppercase;
        letter-spacing: 2px;
        margin-bottom: 15px;
        border-bottom: 1px solid #0f3460;
        padding-bottom: 8px;
    }

    .stage-container {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px;
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
        font-size: 10px;
    }

    .stage-box {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 6px 10px;
        border-radius: 4px;
        border: 1px solid;
        transition: all 0.3s ease;
        white-space: nowrap;
    }

    .stage-box.active {
        background: rgba(0, 212, 255, 0.15);
        border-color: #00d4ff;
        box-shadow: 0 0 10px rgba(0, 212, 255, 0.3);
    }

    .stage-box.pass {
        background: rgba(0, 255, 136, 0.1);
        border-color: #00ff88;
    }

    .stage-box.fail {
        background: rgba(255, 68, 68, 0.15);
        border-color: #ff4444;
    }

    .stage-box.retry {
        background: rgba(255, 215, 0, 0.15);
        border-color: #ffd700;
    }

    .stage-box.pending {
        background: rgba(102, 102, 102, 0.1);
        border-color: #666666;
        opacity: 0.6;
    }

    .arrow {
        color: #666666;
        font-size: 12px;
        padding: 0 2px;
    }

    .retry-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: #ffd700;
        color: #1a1a2e;
        font-size: 9px;
        font-weight: bold;
        margin-left: 4px;
    }

    .feedback-loop-indicator {
        margin-top: 15px;
        padding: 10px;
        background: rgba(255, 215, 0, 0.05);
        border-left: 3px solid #ffd700;
        border-radius: 0 4px 4px 0;
        font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
        font-size: 10px;
        color: #ffd700;
    }
    </style>
    """, unsafe_allow_html=True)

    # Render container
    st.markdown('<div class="feedback-loop-container">', unsafe_allow_html=True)
    st.markdown('<div class="pipeline-title">RTL PIPELINE EXECUTION</div>', unsafe_allow_html=True)

    # Build stage pipeline HTML
    stages_html = '<div class="stage-container">'

    for i, stage in enumerate(STAGES):
        # Get stage state
        stage_id = stage["id"]
        color = get_stage_color(stage_id, current_stage, completed_stages,
                                failed_stages, retry_counts)

        # Determine CSS class
        if stage_id == current_stage:
            css_class = "active"
        elif stage_id in failed_stages:
            if stage_id in FEEDBACK_CHECKPOINTS and retry_counts.get(stage_id, 0) < 3:
                css_class = "retry"
            else:
                css_class = "fail"
        elif stage_id in completed_stages:
            css_class = "pass"
        else:
            css_class = "pending"

        # Build stage box
        retry_badge = ""
        if stage_id in retry_counts and retry_counts[stage_id] > 0:
            retry_badge = f'<span class="retry-badge">{retry_counts[stage_id]}</span>'

        stage_html = f'''
        <div class="stage-box {css_class}" style="border-color: {color}; color: {color};">
            {stage['icon']} {stage['name']}{retry_badge}
        </div>
        '''
        stages_html += stage_html

        # Add arrow if not last stage
        if i < len(STAGES) - 1:
            stages_html += '<span class="arrow">→</span>'

    stages_html += '</div>'
    st.markdown(stages_html, unsafe_allow_html=True)

    # Show feedback loop indicator if in retry
    if feedback_source and feedback_source in FEEDBACK_CHECKPOINTS:
        retry_count = retry_counts.get(feedback_source, 0)
        st.markdown(f'''
        <div class="feedback-loop-indicator">
            ⚠️ FEEDBACK LOOP ACTIVE: {feedback_source.upper()} failed (attempt {retry_count}/3) →
            Debugger analyzing → Full rollback to Lint
        </div>
        ''', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
