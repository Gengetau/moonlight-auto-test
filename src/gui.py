import streamlit as st
import base64
import subprocess
import os
import time
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
import sys

# Set working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from src.config_parser import Config


def venv_executable(name):
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    executable = f"{name}.exe" if os.name == "nt" else name
    candidate = PROJECT_ROOT / "venv" / scripts_dir / executable
    if candidate.exists():
        return str(candidate)
    return sys.executable if name == "python" else name


def quote(value):
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def route_file_stem(target):
    text = str(target or "").replace("\\", "/").strip().strip("/")
    if text.lower().endswith(".jsp"):
        text = text[:-4]
    text = text.replace("/", "_")
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in text)
    return safe or "target"


def safe_upload_name(filename):
    name = Path(str(filename or "upload.bin").replace("\\", "/")).name or "upload.bin"
    invalid = '<>:"/\\|?*'
    return "".join("_" if char in invalid or ord(char) < 32 else char for char in name)


def save_uploaded_file(uploaded_file, *, subdir):
    upload_dir = Path("test_data/upload") / subdir
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / safe_upload_name(uploaded_file.name)
    upload_path.write_bytes(uploaded_file.getbuffer())
    return upload_path


def login_entry_names():
    try:
        names = [entry["name"] for entry in Config.login_entries() if entry.get("name")]
    except Exception:
        names = []
    return names or ["entry-1"]


LOGIN_ENTRY_NAMES = login_entry_names()
BROWSER_OPTIONS = {
    "Chrome portable": "chrome_port",
    "Microsoft Edge": "edge",
    "Firefox": "firefox",
}
PYTHON_CMD = quote(venv_executable("python"))
PYTEST_CMD = quote(venv_executable("pytest"))
REPORT_PATHS = (
    Path("output/regression/regression_report.html"),
    Path("output/gui_report.html"),
)


def latest_report_path():
    existing = [path for path in REPORT_PATHS if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def render_report_links(report_path, *, key_prefix):
    if not report_path or not report_path.exists():
        return
    report_bytes = report_path.read_bytes()
    encoded = base64.b64encode(report_bytes).decode("ascii")
    href = f"data:text/html;base64,{encoded}"
    st.markdown(
        f'<a href="{href}" target="_blank" rel="noopener noreferrer">打开 {report_path.name}</a>',
        unsafe_allow_html=True,
    )
    st.download_button(
        "下载报告",
        data=report_bytes,
        file_name=report_path.name,
        mime="text/html",
        key=f"{key_prefix}_download_{report_path.as_posix()}",
    )
    st.caption(str(report_path))

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

# Helper function to run commands
def run_command(cmd, live_output=True):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
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
            st.text(Path(".env").read_text(encoding="utf-8", errors="replace"))
    else:
        st.error("❌ .env Missing")
        if st.button("Create Sample .env", key="env_create_sample"):
            sample = """LEGACY_URL=http://legacy.example.com
NEW_URL=http://new.example.com
TEST_USERNAME=mika
TEST_PASSWORD=secret
"""
            Path(".env").write_text(sample, encoding="utf-8")
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
    current_report = latest_report_path()
    if current_report:
        st.subheader("Latest Report")
        render_report_links(current_report, key_prefix="latest")

    col1, col2 = st.columns([2, 1])
    
    with col1:
        target_page = st.text_input("Target Page (Optional)", placeholder="AbstListEdit.jsp", help="If empty, runs full/risk-only regression", key="reg_target_page")
        login_entry = st.selectbox("Login Entry", LOGIN_ENTRY_NAMES, key="reg_login_entry")
        browser_label = st.selectbox("Browser", list(BROWSER_OPTIONS.keys()), key="reg_browser")
        checklist_path = st.text_input("Checklist Path", value="generated/valid/migration_checklist.xlsx", key="reg_checklist_path")
        
    with col2:
        risk_only = st.checkbox("Risk Only (High/Medium)", value=False, key="reg_risk_only")
        manual_mode = st.checkbox("Manual Takeover Mode", value=False, key="reg_manual_mode")
        force_route_map = st.checkbox("Use Route Map", value=True, key="reg_force_route_map")
        use_upload_file = st.checkbox("Use Upload File", value=False, key="reg_use_upload_file")
        selected_upload_file = st.file_uploader("Upload File", key="reg_upload_file")
        
    if st.button("🔥 Launch Regression Engine", key="reg_launch"):
        cmd = f"tests/test_migration.py --run-migration --test-browser={BROWSER_OPTIONS[browser_label]}"
        if target_page:
            cmd += f" --target-page={target_page}"
        if risk_only:
            cmd += " --risk-only"
        if manual_mode:
            cmd += " --manual"
        if login_entry:
            cmd += f" --login-entry={login_entry}"
        if checklist_path:
            checklist = Path(checklist_path)
            if checklist.exists():
                cmd += f" --checklist-path={quote(checklist_path)}"
            else:
                st.warning(f"Checklist not found, using page mapping fallback: {checklist_path}")
        if force_route_map:
            cmd += " --force-route-map --route-map-path=generated/valid/route"
        if use_upload_file:
            if selected_upload_file is None:
                st.error("Please select an upload file first.")
                st.stop()
            upload_path = save_uploaded_file(selected_upload_file, subdir="gui_regression")
            cmd += f" --upload-file={quote(upload_path)}"
        
        cmd += " --html=output/gui_report.html"
        
        full_cmd = f"{PYTEST_CMD} {cmd}"
        st.info(f"Running: `{full_cmd}`")
        run_command(full_cmd)
        
        report_path = latest_report_path()
        if report_path:
            st.success("Regression Complete.")
            render_report_links(report_path, key_prefix="completed")

# --- TAB: Route Mapping ---
with tabs[1]:
    st.header("Route Intelligence")
    
    sub_tabs = st.tabs(["1. Generate Candidates", "2. Verify Routes"])
    
    with sub_tabs[0]:
        r_target = st.text_input("Target JSP", key="rt_target")
        r_entry = st.text_input("Entry JSP", value="PatlicsMenu.jsp", key="rt_entry")
        r_limit = st.slider("Limit per target", 1, 20, 5, key="rt_limit")
        
        if st.button("🛰️ Scout Paths", key="rt_scout_paths"):
            if not r_target.strip():
                st.error("Target JSP is required.")
            else:
                out_file = f"generated/valid/route/route_candidates_{route_file_stem(r_target)}.json"
                cmd = f"{PYTHON_CMD} -m src.route_catalog --target {quote(r_target)} --limit-per-target {r_limit} --output {quote(out_file)}"
                if r_entry:
                    cmd += f" --entry {quote(r_entry)}"
                st.info(f"Scouting candidates for {r_target}...")
                run_command(cmd)

    with sub_tabs[1]:
        v_side = st.selectbox("Verification Side", ["legacy", "new"], key="rv_side")
        v_target = st.text_input("Target JSP", key="rv_target")
        v_login_entry = st.selectbox("Login Entry", LOGIN_ENTRY_NAMES, key="rv_login_entry")
        v_browser_label = st.selectbox("Browser", list(BROWSER_OPTIONS.keys()), key="rv_browser")
        v_auto_login = st.checkbox("Auto Login", value=False, key="rv_auto_login")
        v_use_upload_file = st.checkbox("Use Upload File", value=False, key="rv_use_upload_file")
        v_selected_upload_file = st.file_uploader("Upload File", key="rv_upload_file")
        
        if st.button("🛡️ Verify Route Consistency", key="rv_verify_route"):
            if not v_target.strip():
                st.error("Target JSP is required.")
            else:
                stem = route_file_stem(v_target)
                cand_file = f"generated/valid/route/route_candidates_{stem}.json"
                out_file = f"generated/valid/route/usable_route_map_{v_side}_{stem}.json"

                if not os.path.exists(cand_file):
                    st.error(f"Candidate file not found: {cand_file}")
                else:
                    cmd = f"{PYTHON_CMD} -m src.route_map_runner --candidates {quote(cand_file)} --target {quote(v_target)} --side {v_side} --browser {BROWSER_OPTIONS[v_browser_label]} --output {quote(out_file)} --manual-data"
                    if v_login_entry:
                        cmd += f" --login-entry {quote(v_login_entry)}"
                    if v_auto_login:
                        cmd += " --auto-login"
                    if v_use_upload_file:
                        if v_selected_upload_file is None:
                            st.error("Please select an upload file first.")
                            st.stop()
                        upload_path = save_uploaded_file(v_selected_upload_file, subdir="gui_route")
                        cmd += f" --upload-file {quote(upload_path)}"
                    st.warning("Manual Data mode enabled. Check terminal for interactions if needed.")
                    run_command(cmd)

# --- TAB: Scanner & Mapper ---
with tabs[2]:
    st.header("Static Analysis Pipeline")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Step 1: Scan JSP Assets")
        leg_path = st.text_input("Legacy JSP Root Path", key="scan_legacy_root")
        new_path = st.text_input("New JSP Root Path", key="scan_new_root")
        
        if st.button("🔍 Run Full Scan", key="scan_run_full"):
            if leg_path:
                run_command(f"{PYTHON_CMD} src/jsp_scanner.py {quote(leg_path)} -o mappings/legacy_elements.json")
            if new_path:
                run_command(f"{PYTHON_CMD} src/jsp_scanner.py {quote(new_path)} -o mappings/new_elements.json")

    with col2:
        st.subheader("Step 2: Bridge Time & Space")
        if st.button("🌉 Generate Mapping", key="scan_generate_mapping"):
            cmd = f"{PYTHON_CMD} src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json -o generated/valid/page_mapping.json --md generated/valid/comparison_summary.md"
            run_command(cmd)

# --- TAB: Analysis ---
with tabs[3]:
    st.header("Intelligence Summary")
    
    if mapping_json.exists():
        data = json.loads(mapping_json.read_text(encoding="utf-8"))
        mappings = data.get("page_mappings", [])
        
        df = pd.DataFrame(mappings)
        if not df.empty:
            # Risk distribution
            st.subheader("Risk Distribution")
            risk_col = "risk" if "risk" in df.columns else "risk_level"
            page_col = "page_id" if "page_id" in df.columns else "legacy_page"
            risk_counts = df[risk_col].value_counts()
            st.bar_chart(risk_counts)
            
            # Search / Filter
            st.subheader("Page Navigator")
            search = st.text_input("Filter by Page Name", key="analysis_page_filter")
            if search:
                df = df[df[page_col].astype(str).str.contains(search, case=False)]
            
            preferred = [page_col, risk_col, "status", "matched_elements_count", "missing_elements_count"]
            visible_columns = [column for column in preferred if column in df.columns]
            st.dataframe(df[visible_columns] if visible_columns else df)
    else:
        st.info("No mapping data found. Run Scanner & Mapper first.")

st.divider()
st.caption(f"Moonlight Control Center v1.0 | Current Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
