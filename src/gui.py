import streamlit as st
import subprocess
import os
import time
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# Set working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)

st.set_page_config(
    page_title="Moonlight Control Center",
    page_icon="🌕",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for Moonlight theme
st.markdown("""
<style>
    .reportview-container {
        background: #0e1117;
    }
    .main {
        color: #e0e0e0;
    }
    .stButton>button {
        width: 100%;
        border-radius: 5px;
        height: 3em;
        background-color: #2e3b4e;
        color: white;
    }
    .stButton>button:hover {
        background-color: #3e4b5e;
        border-color: #4a90e2;
    }
    .luna-quote {
        font-style: italic;
        color: #a0a0a0;
        border-left: 5px solid #4a90e2;
        padding-left: 15px;
        margin: 20px 0;
    }
</style>
""", unsafe_allow_html=True)

st.title("🌕 Moonlight Control Center")
st.markdown('<div class="luna-quote">“既然你向本公主求助，我就绝不允许你的系统里存在这种低级丑陋的错误。” —— 露娜</div>', unsafe_allow_html=True)

# Helper function to run commands
def run_command(cmd, live_output=True):
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    output_container = st.empty()
    full_output = ""
    
    for line in process.stdout:
        full_output += line
        if live_output:
            output_container.code(full_output)
    
    process.wait()
    return process.returncode, full_output

# Sidebar
with st.sidebar:
    st.image("https://img.icons8.com/color/96/000000/moon.png", width=80)
    st.header("⚙️ Environment")
    
    if os.path.exists(".env"):
        st.success("✅ .env Loaded")
        with st.expander("View .env"):
            st.text(Path(".env").read_text())
    else:
        st.error("❌ .env Missing")
        if st.button("Create Sample .env"):
            sample = """LEGACY_URL=http://legacy.example.com
NEW_URL=http://new.example.com
TEST_USERNAME=mika
TEST_PASSWORD=secret
"""
            Path(".env").write_text(sample)
            st.rerun()

    st.divider()
    st.markdown("### 📡 Quick Status")
    
    # Check for mapping files
    legacy_json = Path("mappings/legacy_elements.json")
    new_json = Path("mappings/new_elements.json")
    mapping_json = Path("generated/valid/page_mapping.json")
    
    col1, col2 = st.columns(2)
    col1.metric("Legacy Scan", "OK" if legacy_json.exists() else "Missing")
    col2.metric("New Scan", "OK" if new_json.exists() else "Missing")
    st.metric("Mapping Data", "Verified" if mapping_json.exists() else "Not Found")

# Tabs
tabs = st.tabs(["🚀 Regression", "🗺️ Route Mapping", "📡 Scanner & Mapper", "📊 Analysis"])

# --- TAB: Regression ---
with tabs[0]:
    st.header("Execute Regression Test")
    col1, col2 = st.columns([2, 1])
    
    with col1:
        target_page = st.text_input("Target Page (Optional)", placeholder="AbstListEdit.jsp", help="If empty, runs full/risk-only regression")
        login_entry = st.text_input("Login Entry", value="dev-a")
        
    with col2:
        risk_only = st.checkbox("Risk Only (High/Medium)", value=False)
        manual_mode = st.checkbox("Manual Takeover Mode", value=False)
        
    if st.button("🔥 Launch Regression Engine"):
        cmd = "pytest tests/test_migration.py"
        if target_page:
            cmd += f" --target-page={target_page}"
        if risk_only:
            cmd += " --risk-only"
        if manual_mode:
            cmd += " --manual"
        if login_entry:
            cmd += f" --login-entry={login_entry}"
        
        cmd += " --html=output/gui_report.html"
        
        st.info(f"Running: `{cmd}`")
        run_command(f"./venv/bin/{cmd}")
        
        if os.path.exists("output/gui_report.html"):
            st.success("Regression Complete. [Open Report](output/gui_report.html)")

# --- TAB: Route Mapping ---
with tabs[1]:
    st.header("Route Intelligence")
    
    sub_tabs = st.tabs(["1. Generate Candidates", "2. Verify Routes"])
    
    with sub_tabs[0]:
        r_target = st.text_input("Target JSP", key="rt_target", value="ProjectMemberUploadDisp.jsp")
        r_entry = st.text_input("Entry JSP", value="PatlicsMenu.jsp")
        r_limit = st.slider("Limit per target", 1, 20, 5)
        
        if st.button("🛰️ Scout Paths"):
            out_file = f"generated/valid/route_candidates_{r_target.replace('.jsp', '')}.json"
            cmd = f"./venv/bin/python3 -m src.route_catalog --target {r_target} --entry {r_entry} --limit-per-target {r_limit} --output {out_file}"
            st.info(f"Scouting candidates for {r_target}...")
            run_command(cmd)

    with sub_tabs[1]:
        v_side = st.selectbox("Verification Side", ["legacy", "new"])
        v_target = st.text_input("Target JSP", key="rv_target", value="ProjectMemberUploadDisp.jsp")
        
        if st.button("🛡️ Verify Route Consistency"):
            cand_file = f"generated/valid/route_candidates_{v_target.replace('.jsp', '')}.json"
            out_file = f"generated/valid/usable_route_map_{v_side}_{v_target.replace('.jsp', '')}.json"
            
            if not os.path.exists(cand_file):
                st.error(f"Candidate file not found: {cand_file}")
            else:
                cmd = f"./venv/bin/python3 -m src.route_map_runner --candidates {cand_file} --target {v_target} --side {v_side} --output {out_file} --manual-data"
                st.warning("Manual Data mode enabled. Check terminal for interactions if needed.")
                run_command(cmd)

# --- TAB: Scanner & Mapper ---
with tabs[2]:
    st.header("Static Analysis Pipeline")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Step 1: Scan JSP Assets")
        leg_path = st.text_input("Legacy JSP Root Path")
        new_path = st.text_input("New JSP Root Path")
        
        if st.button("🔍 Run Full Scan"):
            if leg_path:
                run_command(f"./venv/bin/python3 src/jsp_scanner.py {leg_path} -o mappings/legacy_elements.json")
            if new_path:
                run_command(f"./venv/bin/python3 src/jsp_scanner.py {new_path} -o mappings/new_elements.json")

    with col2:
        st.subheader("Step 2: Bridge Time & Space")
        if st.button("🌉 Generate Mapping"):
            cmd = "./venv/bin/python3 src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json -o generated/valid/page_mapping.json --md generated/valid/comparison_summary.md"
            run_command(cmd)

# --- TAB: Analysis ---
with tabs[3]:
    st.header("Intelligence Summary")
    
    if mapping_json.exists():
        data = json.loads(mapping_json.read_text())
        mappings = data.get("page_mappings", [])
        
        df = pd.DataFrame(mappings)
        if not df.empty:
            # Risk distribution
            st.subheader("Risk Distribution")
            risk_counts = df['risk_level'].value_counts()
            st.bar_chart(risk_counts)
            
            # Search / Filter
            st.subheader("Page Navigator")
            search = st.text_input("Filter by Page Name")
            if search:
                df = df[df['legacy_page'].str.contains(search, case=False)]
            
            st.dataframe(df[['legacy_page', 'risk_level', 'status', 'matched_elements_count', 'missing_elements_count']])
    else:
        st.info("No mapping data found. Run Scanner & Mapper first.")

st.divider()
st.caption(f"Moonlight Control Center v1.0 | Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
