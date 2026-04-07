"""Project Setup page - configure RTL design project."""

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

st.set_page_config(page_title="Project Setup - VeriFlow-Agent", page_icon="📁")

st.title("📁 项目设置")
st.markdown("配置 RTL 设计项目目录和需求")

st.divider()

# Project directory selection
col1, col2 = st.columns([3, 1])

with col1:
    project_dir = st.text_input(
        "项目目录",
        value=st.session_state.get("project_dir", ""),
        placeholder="输入项目绝对路径，例如: C:\\Users\\...\\my_alu",
        help="项目目录必须包含 requirement.md 文件",
    )

with col2:
    if st.button("📂 浏览", use_container_width=True):
        st.info("请直接在文本框中输入路径（浏览器不支持直接浏览文件系统）")

st.session_state["project_dir"] = project_dir

# Validate project directory
if project_dir and Path(project_dir).exists():
    req_file = Path(project_dir) / "requirement.md"
    workspace_dir = Path(project_dir) / "workspace"

    col_status1, col_status2, col_status3 = st.columns(3)

    with col_status1:
        if req_file.exists():
            st.success("✅ requirement.md")
        else:
            st.error("❌ requirement.md 缺失")

    with col_status2:
        if workspace_dir.exists():
            st.success("✅ workspace/ 目录")
        else:
            st.warning("⚠️ workspace/ 目录将自动创建")

    with col_status3:
        checkpoint_file = Path(project_dir) / ".veriflow" / "checkpoint.json"
        if checkpoint_file.exists():
            st.info(f"💾 存在检查点 ({checkpoint_file.stat().st_size} bytes)")
        else:
            st.info("📝 新运行（无检查点）")

    # Show requirement preview
    if req_file.exists():
        with st.expander("📄 查看 requirement.md"):
            st.markdown(req_file.read_text(encoding="utf-8"))

elif project_dir:
    st.error(f"❌ 目录不存在: {project_dir}")

st.divider()

# Pipeline flow info
st.subheader("⚙️ 流水线流程")
st.info("**完整 RTL 设计流程**  — architect → microarch → timing → coder → skill_d → lint → sim → synth | 含 lint/sim/synth 反馈回路")

st.divider()

# Next steps
st.subheader("🎯 下一步")

if not project_dir or not Path(project_dir).exists():
    st.warning("⚠️ 请先输入有效的项目目录")
elif not (Path(project_dir) / "requirement.md").exists():
    st.warning("⚠️ 项目目录缺少 requirement.md 文件")
    st.markdown("""
    **快速创建示例:**
    ```powershell
    # Windows
    $project = "{0}"
    New-Item -ItemType File -Path "$project\requirement.md" -Force
    # 编辑 requirement.md 添加设计需求
    ```
    """.format(project_dir))
else:
    col_run, col_status = st.columns([1, 2])
    with col_run:
        if st.button("▶️ 运行流水线", type="primary", use_container_width=True):
            st.switch_page("pages/02_▶️_pipeline_execution.py")
    with col_status:
        st.success("✅ 项目配置完成")
        st.markdown(f"- 📁 项目: `{project_dir}`")
        st.markdown("- 📝 requirement.md: 存在")
        st.markdown("- ⚙️ 流程: 全阶段执行（含反馈回路）")
