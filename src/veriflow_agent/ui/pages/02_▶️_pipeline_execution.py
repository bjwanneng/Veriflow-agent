"""Pipeline Execution page - run RTL design stages with progress tracking."""

import json
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

st.set_page_config(page_title="Pipeline Execution - VeriFlow-Agent", page_icon="▶️")

st.title("▶️ 流水线执行")
st.markdown("运行 RTL 设计流程的各个阶段")

st.divider()

# Check if project is configured
if "project_dir" not in st.session_state or not st.session_state["project_dir"]:
    st.error("❌ 请先配置项目目录")
    st.button("📁 前往项目设置", on_click=lambda: st.switch_page("pages/01_🏠_project_setup.py"))
    st.stop()

project_dir = st.session_state["project_dir"]
mode = st.session_state.get("pipeline_mode", "standard")

# Validate project
if not Path(project_dir).exists():
    st.error(f"❌ 项目目录不存在: {project_dir}")
    st.stop()

if not (Path(project_dir) / "requirement.md").exists():
    st.warning("⚠️ 缺少 requirement.md")

# Initialize session state for execution tracking
if "execution_state" not in st.session_state:
    st.session_state["execution_state"] = {
        "running": False,
        "current_stage": None,
        "completed_stages": [],
        "failed_stages": [],
        "logs": [],
        "results": {},
    }

exec_state = st.session_state["execution_state"]

# Stage definitions
STAGES = [
    {"num": 1, "name": "architect", "display": "🏗️ Architect", "output": "spec.json"},
    {"num": 1.5, "name": "microarch", "display": "📐 MicroArch", "output": "micro_arch.md"},
    {"num": 2, "name": "timing", "display": "⏱️ Timing", "output": "timing_model.yaml"},
    {"num": 3, "name": "coder", "display": "💻 Coder", "output": "*.v"},
    {"num": 3.5, "name": "skill_d", "display": "🔍 Skill D", "output": "lint report"},
    {"num": 4, "name": "sim_loop", "display": "🧪 Sim Loop", "output": "sim results"},
    {"num": 5, "name": "synth", "display": "🔧 Synthesis", "output": "synth_report.json"},
]

# Filter stages based on mode
if mode == "quick":
    ACTIVE_STAGES = [s for s in STAGES if s["name"] not in ["timing", "sim_loop"]]
else:
    ACTIVE_STAGES = STAGES

# UI Layout
col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("📋 执行控制")

    st.markdown(f"**项目:** `{Path(project_dir).name}`")
    st.markdown(f"**模式:** `{mode}`")
    st.markdown(f"**阶段数:** {len(ACTIVE_STAGES)}")

    st.divider()

    # Execution controls
    if not exec_state["running"] and not exec_state["completed_stages"]:
        if st.button("▶️ 开始执行", type="primary", use_container_width=True):
            exec_state["running"] = True
            exec_state["current_stage"] = ACTIVE_STAGES[0]["name"]
            st.rerun()

    elif exec_state["running"]:
        st.info(f"⏳ 正在执行: {exec_state['current_stage']}")
        if st.button("⏸️ 停止", use_container_width=True):
            exec_state["running"] = False
            st.rerun()

    elif exec_state["completed_stages"]:
        if st.button("🔄 重新执行", use_container_width=True):
            exec_state["running"] = True
            exec_state["current_stage"] = ACTIVE_STAGES[0]["name"]
            exec_state["completed_stages"] = []
            exec_state["failed_stages"] = []
            exec_state["logs"] = []
            st.rerun()

    st.divider()

    # Checkpoint management
    checkpoint_path = Path(project_dir) / ".veriflow" / "checkpoint.json"
    if checkpoint_path.exists():
        st.info(f"💾 存在检查点")
        if st.button("🗑️ 清除检查点", use_container_width=True):
            checkpoint_path.unlink()
            st.success("检查点已清除")
            st.rerun()

with col_right:
    st.subheader("📊 执行状态")

    # Stage progress bars
    for stage in ACTIVE_STAGES:
        stage_name = stage["name"]
        display = stage["display"]

        col_status, col_bar = st.columns([1, 3])

        with col_status:
            if stage_name in exec_state["failed_stages"]:
                st.error(f"{display} ❌")
            elif stage_name in exec_state["completed_stages"]:
                st.success(f"{display} ✅")
            elif stage_name == exec_state.get("current_stage"):
                st.info(f"{display} ⏳")
            else:
                st.markdown(f"{display} ⏸️")

        with col_bar:
            if stage_name in exec_state["completed_stages"]:
                st.progress(100)
            elif stage_name == exec_state.get("current_stage"):
                # Simulated progress
                import random
                                progress = random.randint(20, 80)
                st.progress(progress)
            else:
                st.progress(0)

    # Execution log
    with st.expander("📜 执行日志", expanded=False):
        if exec_state["logs"]:
            for log in exec_state["logs"]:
                st.text(log)
        else:
            st.markdown("*暂无日志*")

    # Quick actions for completed stages
    if exec_state["completed_stages"]:
        st.divider()
        st.subheader("🔍 快速查看")

        cols = st.columns(4)
        with cols[0]:
            if "architect" in exec_state["completed_stages"]:
                if st.button("📄 查看 spec.json"):
                    spec_path = Path(project_dir) / "workspace" / "docs" / "spec.json"
                    if spec_path.exists():
                        st.json(json.loads(spec_path.read_text()))
        with cols[1]:
            if "coder" in exec_state["completed_stages"]:
                if st.button("💻 查看 RTL"):
                    rtl_dir = Path(project_dir) / "workspace" / "rtl"
                    if rtl_dir.exists():
                        files = list(rtl_dir.glob("*.v"))
                        st.markdown(f"**RTL 文件 ({len(files)}):**")
                        for f in files:
                            st.markdown(f"- `{f.name}`")
        with cols[2]:
            if "synth" in exec_state["completed_stages"]:
                if st.button("📊 查看综合报告"):
                    report_path = Path(project_dir) / "workspace" / "docs" / "synth_report.json"
                    if report_path.exists():
                        st.json(json.loads(report_path.read_text()))

# Simulate execution progress (for demo purposes)
if exec_state["running"] and exec_state["current_stage"]:
    # In real implementation, this would execute the actual pipeline
    import time

    current = exec_state["current_stage"]
    active_names = [s["name"] for s in ACTIVE_STAGES]

    if current in active_names:
        idx = active_names.index(current)

        # Simulate stage completion after delay
        time.sleep(0.5)  # Demo delay

        # Mark current as completed
        if current not in exec_state["completed_stages"]:
            exec_state["completed_stages"].append(current)
            exec_state["logs"].append(f"✅ {current} completed")

        # Move to next stage or finish
        if idx + 1 < len(active_names):
            exec_state["current_stage"] = active_names[idx + 1]
            exec_state["logs"].append(f"⏳ Starting {active_names[idx + 1]}...")
            st.rerun()
        else:
            exec_state["running"] = False
            exec_state["current_stage"] = None
            exec_state["logs"].append("🎉 Pipeline completed!")
            st.rerun()
