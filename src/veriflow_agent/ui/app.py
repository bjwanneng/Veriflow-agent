"""Main Streamlit app entry point for VeriFlow-Agent Web UI."""

import streamlit as st

st.set_page_config(
    page_title="VeriFlow-Agent",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🔷 VeriFlow-Agent")
st.markdown("**RTL Design Pipeline using LangGraph**")
st.markdown("---")

st.sidebar.title("导航")
st.sidebar.markdown("选择页面:")

pages = {
    "🏠 主页": "Welcome to VeriFlow-Agent Web UI.",
    "📁 项目设置": "Configure your RTL design project.",
    "▶️ 流水线执行": "Run the RTL design pipeline stages.",
    "📊 结果查看": "View generated outputs and metrics.",
}

for page_name, description in pages.items():
    with st.sidebar.expander(page_name):
        st.markdown(description)

st.markdown("""
### 快速开始

1. **项目设置** - 配置项目目录和设计需求
2. **流水线执行** - 运行 RTL 设计流程（架构 → RTL → 综合）
3. **结果查看** - 查看生成的 spec.json、RTL 代码、综合报告

### 流水线阶段

| 阶段 | 名称 | 输出 |
|------|------|------|
| 1 | Architect | spec.json |
| 1.5 | MicroArch | micro_arch.md |
| 2 | Timing | timing_model.yaml + TB |
| 3 | Coder | RTL 代码 |
| 3.5 | Skill D | Lint 检查 |
| 4 | Sim Loop | 仿真验证 |
| 5 | Synth | 综合报告 |
""")

st.markdown("---")
st.markdown("**VeriFlow-Agent v0.1.0** | Built with Streamlit + LangGraph")
