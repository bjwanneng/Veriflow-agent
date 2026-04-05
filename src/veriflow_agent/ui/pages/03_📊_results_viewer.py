"""Results Viewer page - view generated outputs and metrics."""

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

st.set_page_config(page_title="Results Viewer - VeriFlow-Agent", page_icon="📊")

st.title("📊 结果查看")
st.markdown("查看生成的输出文件和指标")

st.divider()

# Get project directory
if "project_dir" not in st.session_state or not st.session_state["project_dir"]:
    st.warning("⚠️ 请先配置项目目录")
    if st.button("📁 前往项目设置"):
        st.switch_page("pages/01_🏠_project_setup.py")
    st.stop()

project_dir = Path(st.session_state["project_dir"])
workspace_dir = project_dir / "workspace"

if not workspace_dir.exists():
    st.warning("⚠️ 未找到 workspace 目录，可能尚未运行流水线")
    st.stop()

# Tab layout for different file types
tabs = st.tabs(["📄 Spec", "📐 MicroArch", "⏱️ Timing", "💻 RTL", "🧪 Testbench", "📊 Synthesis"])

# Spec tab
with tabs[0]:
    st.header("📄 Architecture Specification (spec.json)")
    spec_path = workspace_dir / "docs" / "spec.json"

    if spec_path.exists():
        try:
            spec_data = json.loads(spec_path.read_text(encoding="utf-8"))

            col1, col2 = st.columns(2)
            with col1:
                st.subheader("基本信息")
                st.markdown(f"**设计名称:** {spec_data.get('design_name', 'N/A')}")
                st.markdown(f"**版本:** {spec_data.get('version', 'N/A')}")

            with col2:
                st.subheader("KPI 目标")
                kpis = spec_data.get('target_kpis', {})
                for k, v in kpis.items():
                    st.markdown(f"**{k}:** {v}")

            with st.expander("查看完整 spec.json"):
                st.json(spec_data)

            # Module summary
            modules = spec_data.get('modules', [])
            if modules:
                st.subheader(f"模块列表 ({len(modules)} 个)")
                for mod in modules:
                    with st.expander(f"📦 {mod.get('module_name', 'unnamed')} ({mod.get('module_type', 'unknown')})"):
                        st.json(mod)
        except json.JSONDecodeError:
            st.error("❌ spec.json 格式错误")
            st.code(spec_path.read_text(encoding="utf-8"), language="json")
    else:
        st.info("ℹ️ spec.json 不存在，请先运行 Architect 阶段")

# MicroArch tab
with tabs[1]:
    st.header("📐 Micro-Architecture (micro_arch.md)")
    micro_path = workspace_dir / "docs" / "micro_arch.md"

    if micro_path.exists():
        content = micro_path.read_text(encoding="utf-8")
        st.markdown(content)
    else:
        st.info("ℹ️ micro_arch.md 不存在，请先运行 MicroArch 阶段")

# Timing tab
with tabs[2]:
    st.header("⏱️ Timing Model")

    timing_path = workspace_dir / "docs" / "timing_model.yaml"
    if timing_path.exists():
        with st.expander("查看 timing_model.yaml"):
            st.code(timing_path.read_text(encoding="utf-8"), language="yaml")
    else:
        st.info("ℹ️ timing_model.yaml 不存在")

    # Testbench files
    tb_dir = workspace_dir / "tb"
    if tb_dir.exists():
        tb_files = list(tb_dir.glob("tb_*.v"))
        st.subheader(f"🧪 Testbench 文件 ({len(tb_files)})")
        for tb_file in tb_files:
            with st.expander(f"📄 {tb_file.name}"):
                st.code(tb_file.read_text(encoding="utf-8"), language="verilog")

# RTL tab
with tabs[3]:
    st.header("💻 RTL 代码")

    rtl_dir = workspace_dir / "rtl"
    if rtl_dir.exists():
        rtl_files = list(rtl_dir.glob("*.v"))

        col_info1, col_info2 = st.columns(2)
        with col_info1:
            st.metric("RTL 文件数", len(rtl_files))
        with col_info2:
            total_lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in rtl_files)
            st.metric("总行数", total_lines)

        # File browser
        selected_file = st.selectbox(
            "选择文件查看",
            options=[f.name for f in rtl_files],
            format_func=lambda x: f"📄 {x}",
        )

        if selected_file:
            file_path = rtl_dir / selected_file
            content = file_path.read_text(encoding="utf-8")

            # Show syntax highlighted code
            st.code(content, language="verilog")

            # Download button
            st.download_button(
                label="📥 下载文件",
                data=content,
                file_name=selected_file,
                mime="text/plain",
            )
    else:
        st.info("ℹ️ RTL 目录不存在，请先运行 Coder 阶段")

# Synthesis tab
with tabs[4]:
    st.header("🧪 Testbench")

    tb_dir = workspace_dir / "tb"
    if tb_dir.exists():
        tb_files = list(tb_dir.glob("*.v"))
        st.markdown(f"找到 {len(tb_files)} 个 testbench 文件")

        for tb_file in tb_files:
            with st.expander(f"📄 {tb_file.name}"):
                st.code(tb_file.read_text(encoding="utf-8"), language="verilog")

# Synthesis tab (renamed from tb)
with tabs[5]:
    st.header("📊 Synthesis Report")

    synth_report_path = workspace_dir / "docs" / "synth_report.json"

    if synth_report_path.exists():
        try:
            report_data = json.loads(synth_report_path.read_text(encoding="utf-8"))

            # Metrics display
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric(
                    "Cell Count",
                    report_data.get("cell_count", "N/A"),
                )
            with col2:
                st.metric(
                    "Area (um²)",
                    f"{report_data.get('area', 0):.2f}",
                )
            with col3:
                st.metric(
                    "Frequency (MHz)",
                    f"{report_data.get('frequency_mhz', 0):.1f}",
                )
            with col4:
                st.metric(
                    "Slack (ns)",
                    f"{report_data.get('slack', 0):.3f}",
                )

            with st.expander("查看完整报告"):
                st.json(report_data)

        except json.JSONDecodeError:
            st.error("❌ synth_report.json 格式错误")
    else:
        st.info("ℹ️ 综合报告不存在，请先运行 Synth 阶段")

st.divider()

# Quick actions
st.subheader("🚀 快速操作")

action_col1, action_col2, action_col3 = st.columns(3)

with action_col1:
    if st.button("🔄 重新运行流水线", use_container_width=True):
        st.switch_page("pages/02_▶️_pipeline_execution.py")

with action_col2:
    if st.button("📁 更改项目设置", use_container_width=True):
        st.switch_page("pages/01_🏠_project_setup.py")

with action_col3:
    if st.button("📊 刷新结果", use_container_width=True):
        st.rerun()
