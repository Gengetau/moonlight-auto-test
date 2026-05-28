import streamlit as st
import base64
import html
import mimetypes
import subprocess
import os
import time
import json
import pandas as pd
import re
import streamlit.components.v1 as components
import queue
import threading
from pathlib import Path
from datetime import datetime
import sys
from urllib.parse import unquote

# Set working directory to project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from src.config_parser import Config
from src.gui_command_builder import (
    DEFAULT_CHECKLIST_PATH,
    DEFAULT_ROUTE_MAP_PATH,
    build_regression_command,
    browser_key,
    html_report_path,
    load_negative_profile_options,
    load_upload_case_options,
    load_page_options,
    negative_profile_labels,
    page_option_labels,
    regression_output_dir,
    upload_case_option_labels,
    upload_profile_config_path,
)


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


def clean_path_input(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


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


def selected_page_from_label(label):
    if not label:
        return ""
    return str(label).split("    ", 1)[0].strip()


def clear_reg_page_card_state(index):
    page_suffix = f"_{index}"
    profile_marker = f"_{index}_"
    for key in list(st.session_state.keys()):
        if key == "reg_page_card_count":
            continue
        if key.startswith("reg_page_") and key.endswith(page_suffix):
            del st.session_state[key]
        elif key.startswith("reg_profile_") and profile_marker in key:
            del st.session_state[key]


def write_upload_profile_config(page_config):
    profiles = []
    for profile in page_config.get("upload_profiles") or []:
        file_path = profile.get("file")
        if not file_path:
            continue
        profiles.append(
            {
                "name": profile.get("name") or Path(str(file_path)).stem,
                "file": str(file_path),
                "case_id": profile.get("case_id") or "",
                "case_ids": [profile.get("case_id")] if profile.get("case_id") else [],
                "test_title": profile.get("test_title") or "",
                "page_patterns": [page_config["target_page"]],
                "case_types": [profile.get("case_type") or "upload_submit"],
                "locator": profile.get("locator") or "",
                "submit_locator": profile.get("submit_locator") or "",
                "negative": bool(profile.get("negative")),
            }
        )

    if not profiles:
        return None

    config_path = upload_profile_config_path(page_config["browser"], page_config["target_page"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"upload_profiles": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path


def login_entry_names():
    try:
        names = [entry["name"] for entry in Config.login_entries() if entry.get("name")]
    except Exception:
        names = []
    return names or ["entry-1"]


REG_QUEUE_RECORDS_PATH = Path("generated/gui/regression_queue_records.json")


def _page_option_label_for(page_id):
    target = str(page_id or "").strip().lower()
    if not target:
        return ""
    for label in PAGE_OPTION_LABELS:
        if selected_page_from_label(label).lower() == target:
            return label
    return str(page_id or "").strip()


def _negative_labels_for_profiles(options, profiles):
    wanted = {str(profile or "").strip() for profile in profiles if str(profile or "").strip()}
    labels = []
    for label in negative_profile_labels(options):
        if selected_page_from_label(label) in wanted:
            labels.append(label)
    return labels


def _upload_case_label_for(case_labels, case_id):
    target = str(case_id or "").strip().lower()
    if not target:
        return ""
    for label in case_labels:
        if selected_page_from_label(label).lower() == target:
            return label
    return ""


def load_reg_queue_records():
    if not REG_QUEUE_RECORDS_PATH.exists():
        return []
    try:
        payload = json.loads(REG_QUEUE_RECORDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    records = payload.get("records") if isinstance(payload, dict) else []
    return records if isinstance(records, list) else []


def save_reg_queue_records(records):
    REG_QUEUE_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REG_QUEUE_RECORDS_PATH.write_text(
        json.dumps(
            {
                "schema": "moonlight.gui.regression_queue_records.v1",
                "records": records[:50],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def collect_reg_card_state(index):
    target_page = (
        st.session_state.get(f"reg_page_target_value_{index}")
        or selected_page_from_label(st.session_state.get(f"reg_page_target_{index}", ""))
    )
    upload_profiles = []
    profile_count = int(st.session_state.get(f"reg_page_profile_count_{index}", 0) or 0)
    for profile_index in range(profile_count):
        case_id = selected_page_from_label(st.session_state.get(f"reg_profile_case_id_{index}_{profile_index}", ""))
        if case_id:
            upload_profiles.append({"case_id": case_id})
    negative_profiles = [
        selected_page_from_label(label)
        for label in st.session_state.get(f"reg_page_negative_profiles_{index}", [])
        if selected_page_from_label(label)
    ]
    return {
        "enabled": bool(st.session_state.get(f"reg_page_enabled_{index}", True)),
        "target_page": str(target_page or ""),
        "login_entry": st.session_state.get(f"reg_page_login_{index}", LOGIN_ENTRY_NAMES[0]),
        "checklist_path": st.session_state.get(f"reg_page_checklist_{index}", DEFAULT_CHECKLIST_PATH),
        "route_map_path": st.session_state.get(f"reg_page_route_{index}", DEFAULT_ROUTE_MAP_PATH),
        "force_route_map": bool(st.session_state.get(f"reg_page_force_route_{index}", True)),
        "manual": bool(st.session_state.get(f"reg_page_manual_{index}", False)),
        "risk_only": bool(st.session_state.get(f"reg_page_risk_{index}", False)),
        "include_semi_auto": bool(st.session_state.get(f"reg_page_semi_{index}", False)),
        "include_destructive": bool(st.session_state.get(f"reg_page_destructive_{index}", False)),
        "include_negative": bool(st.session_state.get(f"reg_page_negative_{index}", False)),
        "negative_profiles": negative_profiles,
        "upload_profiles": upload_profiles,
    }


def collect_reg_queue_state():
    count = int(st.session_state.get("reg_page_card_count", 1) or 1)
    return {
        "browser_label": st.session_state.get("reg_queue_browser", list(BROWSER_OPTIONS.keys())[0]),
        "card_count": count,
        "cards": [collect_reg_card_state(index) for index in range(1, count + 1)],
    }


def _set_state(key, value):
    if value is not None:
        st.session_state[key] = value


def apply_reg_card_state(index, card):
    target_page = str(card.get("target_page") or "")
    _set_state(f"reg_page_enabled_{index}", bool(card.get("enabled", True)))
    _set_state(f"reg_page_target_value_{index}", target_page)
    _set_state(f"reg_page_target_{index}", _page_option_label_for(target_page))
    _set_state(f"reg_page_login_{index}", card.get("login_entry") or LOGIN_ENTRY_NAMES[0])
    _set_state(f"reg_page_checklist_{index}", card.get("checklist_path") or DEFAULT_CHECKLIST_PATH)
    _set_state(f"reg_page_route_{index}", card.get("route_map_path") or DEFAULT_ROUTE_MAP_PATH)
    _set_state(f"reg_page_force_route_{index}", bool(card.get("force_route_map", True)))
    _set_state(f"reg_page_manual_{index}", bool(card.get("manual", False)))
    _set_state(f"reg_page_risk_{index}", bool(card.get("risk_only", False)))
    _set_state(f"reg_page_semi_{index}", bool(card.get("include_semi_auto", False)))
    _set_state(f"reg_page_destructive_{index}", bool(card.get("include_destructive", False)))
    _set_state(f"reg_page_negative_{index}", bool(card.get("include_negative", False)))
    st.session_state[f"reg_page_saved_negative_profiles_{index}"] = list(card.get("negative_profiles") or [])
    upload_profiles = list(card.get("upload_profiles") or [])
    _set_state(f"reg_page_profile_count_{index}", len(upload_profiles))
    for profile_index, profile in enumerate(upload_profiles):
        st.session_state[f"reg_profile_saved_case_id_{index}_{profile_index}"] = str(profile.get("case_id") or "")


def apply_reg_queue_state(record):
    queue = record.get("queue") if isinstance(record.get("queue"), dict) else record
    cards = list(queue.get("cards") or [])
    next_count = max(1, len(cards))
    current_count = int(st.session_state.get("reg_page_card_count", 1) or 1)
    for index in range(1, max(current_count, next_count) + 1):
        clear_reg_page_card_state(index)
    browser_label = queue.get("browser_label") if queue.get("browser_label") in BROWSER_OPTIONS else list(BROWSER_OPTIONS.keys())[0]
    _set_state("reg_queue_browser", browser_label)
    st.session_state["reg_page_card_count"] = next_count
    st.session_state["reg_focus_card_index"] = 1
    for index, card in enumerate(cards or [{}], start=1):
        apply_reg_card_state(index, card)


def save_current_reg_queue_record(name):
    records = load_reg_queue_records()
    record = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "name": name.strip() or datetime.now().strftime("Regression Queue %Y-%m-%d %H:%M"),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "queue": collect_reg_queue_state(),
    }
    records = [item for item in records if item.get("name") != record["name"]]
    save_reg_queue_records([record] + records)
    return record


def reg_queue_record_labels(records):
    labels = []
    for record in records:
        queue = record.get("queue") or {}
        cards = queue.get("cards") or []
        name = record.get("name") or record.get("id") or "record"
        labels.append(f"{record.get('id')}    {name} / {len(cards)} page(s) / {record.get('saved_at', '-')}")
    return labels


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
    Path("output/gui_regression_report.html"),
    Path("output/gui_report.html"),
)
IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=)(["\'])(.*?)(\2)', re.IGNORECASE)
PAGE_OPTIONS = load_page_options()
PAGE_OPTION_LABELS = page_option_labels(PAGE_OPTIONS)


def page_report_paths():
    regression_dir = Path("output/regression")
    if not regression_dir.exists():
        return []
    reports = [path for path in regression_dir.rglob("regression_report.html") if path.exists()]
    return sorted(reports, key=lambda path: path.stat().st_mtime, reverse=True)


def gui_report_paths():
    gui_dir = Path("output/gui")
    if not gui_dir.exists():
        return []
    reports = [path for path in gui_dir.rglob("gui_report.html") if path.exists()]
    return sorted(reports, key=lambda path: path.stat().st_mtime, reverse=True)


def recent_page_report_paths(limit=10):
    return page_report_paths()[:limit]


def latest_report_path():
    existing = [path for path in REPORT_PATHS if path.exists()]
    existing.extend(page_report_paths())
    existing.extend(gui_report_paths())
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def report_display_name(report_path):
    if not report_path:
        return "report.html"
    return report_path.parent.name if report_path.name == "regression_report.html" else report_path.name


def _asset_data_uri(asset_path):
    mime_type = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
    encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def portable_report_html(report_path):
    source = report_path.read_text(encoding="utf-8", errors="replace")
    base_dir = report_path.parent.resolve()

    def replace_src(match):
        prefix, quote_char, raw_src, suffix = match.groups()
        src = unquote(html.unescape(raw_src)).strip()
        if not src or re.match(r"^(?:data:|https?:|blob:|#)", src, re.IGNORECASE):
            return match.group(0)

        asset_ref = src.split("#", 1)[0].split("?", 1)[0]
        asset_path = Path(asset_ref)
        if not asset_path.is_absolute():
            asset_path = base_dir / asset_ref

        try:
            asset_path = asset_path.resolve()
            if not asset_path.is_file():
                return match.group(0)
            data_uri = _asset_data_uri(asset_path)
        except OSError:
            return match.group(0)

        return f"{prefix}{quote_char}{html.escape(data_uri, quote=True)}{quote_char}"

    return IMG_SRC_RE.sub(replace_src, source)


def portable_report_bytes(report_path):
    return portable_report_html(report_path).encode("utf-8")


def render_report_links(report_path, *, key_prefix):
    if not report_path or not report_path.exists():
        return
    report_bytes = portable_report_bytes(report_path)
    display_name = report_display_name(report_path)
    if st.button(f"预览 {display_name}", key=f"{key_prefix}_preview_{report_path.as_posix()}"):
        st.session_state["selected_report_path"] = str(report_path)
    st.download_button(
        "下载自包含报告",
        data=report_bytes,
        file_name=f"{Path(display_name).stem}_portable.html",
        mime="text/html",
        key=f"{key_prefix}_download_{report_path.as_posix()}",
    )
    st.caption(str(report_path))


def render_recent_page_reports(limit=10):
    reports = recent_page_report_paths(limit)
    st.divider()
    st.markdown("### 最近 10 个页面报告")
    if not reports:
        st.caption("暂无页面报告。")
        return

    for index, report_path in enumerate(reports, start=1):
        page_name = report_display_name(report_path)
        modified = datetime.fromtimestamp(report_path.stat().st_mtime).strftime("%m/%d %H:%M")
        if st.button(f"{index}. {page_name}", key=f"recent_report_{index}_{report_path.as_posix()}"):
            st.session_state["selected_report_path"] = str(report_path)
        st.caption(f"{modified}  {report_path}")


def render_selected_report_viewer():
    raw_path = st.session_state.get("selected_report_path")
    if not raw_path:
        return

    report_path = Path(raw_path)
    if not report_path.exists():
        st.warning(f"Report not found: {report_path}")
        return

    st.subheader(f"Report Preview: {report_display_name(report_path)}")
    report_html = portable_report_html(report_path)
    components.html(report_html, height=900, scrolling=True)
    st.download_button(
        "下载当前自包含报告",
        data=report_html.encode("utf-8"),
        file_name=f"{Path(report_display_name(report_path)).stem}_portable.html",
        mime="text/html",
        key=f"selected_report_download_{report_path.as_posix()}",
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


def run_command_in_powershell(cmd):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    st.code(cmd)
    st.info("请回到启动 Streamlit 的 PowerShell 窗口完成人工交互。Web UI 会等待命令结束。")
    process = subprocess.Popen(
        cmd,
        shell=True,
        env=env,
    )
    process.wait()
    return process.returncode, ""


def _interactive_env():
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _reader_thread(process, output_queue):
    try:
        while True:
            chunk = process.stdout.read(1)
            if chunk == "":
                break
            output_queue.put(chunk)
    finally:
        output_queue.put(None)


def start_interactive_command(session_key, cmd):
    stop_interactive_command(session_key)
    output_queue = queue.Queue()
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_interactive_env(),
    )
    thread = threading.Thread(target=_reader_thread, args=(process, output_queue), daemon=True)
    thread.start()
    st.session_state[session_key] = {
        "cmd": cmd,
        "process": process,
        "queue": output_queue,
        "thread": thread,
        "output": "",
        "closed": False,
        "postprocessed": False,
        "started_at": time.time(),
    }


def interactive_session(session_key):
    return st.session_state.get(session_key)


def drain_interactive_output(session_key):
    session = interactive_session(session_key)
    if not session:
        return None
    output_queue = session.get("queue")
    if output_queue is None:
        return session
    while True:
        try:
            item = output_queue.get_nowait()
        except queue.Empty:
            break
        if item is None:
            session["closed"] = True
            continue
        session["output"] += item
    return session


def send_interactive_input(session_key, value):
    session = drain_interactive_output(session_key)
    if not session:
        return
    process = session.get("process")
    if not process or process.poll() is not None or process.stdin is None:
        return
    try:
        process.stdin.write(f"{value}\n")
        process.stdin.flush()
        session["output"] += f"\n[WEB INPUT] {value if value else '<Enter>'}\n"
    except OSError as exc:
        session["output"] += f"\n[WEB INPUT ERROR] {exc}\n"


def stop_interactive_command(session_key):
    session = st.session_state.get(session_key)
    if not session:
        return
    process = session.get("process")
    if process and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


def render_interactive_console(session_key, *, output_path=None):
    session = drain_interactive_output(session_key)
    if not session:
        return None

    process = session.get("process")
    return_code = process.poll() if process else None
    if return_code is None:
        st.info("路径验证进程运行中。请在这里输入，不需要回到 PowerShell。")
    elif return_code == 0:
        st.success(f"路径验证进程已结束：exit={return_code}")
    else:
        st.error(f"路径验证进程失败：exit={return_code}")

    st.caption(f"Command: {session.get('cmd')}")
    st.caption(
        "页面接管列表说明：日志里的 [0]、[1] 是当前浏览器里可接管的页面或弹窗；"
        "输入序号会切到该页并刷新。frame 行只是页面内部 frame URL，不需要单独选择。"
    )
    st.code(session.get("output") or "(no output yet)")

    col_enter, col_retry, col_manual, col_skip, col_quit = st.columns(5)
    with col_enter:
        if st.button("Send Enter", key=f"{session_key}_send_enter", disabled=return_code is not None):
            send_interactive_input(session_key, "")
            st.rerun()
    with col_retry:
        if st.button("Send r", key=f"{session_key}_send_r", disabled=return_code is not None):
            send_interactive_input(session_key, "r")
            st.rerun()
    with col_manual:
        if st.button("Send m/save", key=f"{session_key}_send_m", disabled=return_code is not None):
            send_interactive_input(session_key, "m")
            st.rerun()
    with col_skip:
        if st.button("Send s", key=f"{session_key}_send_s", disabled=return_code is not None):
            send_interactive_input(session_key, "s")
            st.rerun()
    with col_quit:
        if st.button("Send q", key=f"{session_key}_send_q", disabled=return_code is not None):
            send_interactive_input(session_key, "q")
            st.rerun()

    col_input, col_send = st.columns([4, 1])
    with col_input:
        custom_input = st.text_input(
            "Custom input",
            value="",
            placeholder="页面序号、m、s、q，留空表示 Enter",
            key=f"{session_key}_custom_input",
            disabled=return_code is not None,
        )
    with col_send:
        if st.button("Send", key=f"{session_key}_send_custom", disabled=return_code is not None):
            send_interactive_input(session_key, custom_input)
            st.rerun()

    col_refresh, col_stop, col_clear = st.columns(3)
    with col_refresh:
        if st.button("Refresh output", key=f"{session_key}_refresh"):
            st.rerun()
    with col_stop:
        if st.button("Stop process", key=f"{session_key}_stop", disabled=return_code is not None):
            stop_interactive_command(session_key)
            st.rerun()
    with col_clear:
        if st.button("Clear session", key=f"{session_key}_clear", disabled=return_code is None):
            st.session_state.pop(session_key, None)
            st.rerun()

    if output_path and return_code == 0 and Path(output_path).exists():
        st.success(f"Route map saved: {output_path}")

    return return_code


def normalize_interactive_return_code(session, rendered_value):
    if rendered_value is None or isinstance(rendered_value, int):
        return rendered_value
    process = session.get("process") if session else None
    if process:
        return process.poll()
    return None

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
DOWNLOAD_DIR=~/Downloads
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
    render_recent_page_reports(limit=10)

render_selected_report_viewer()

# Tabs
tabs = st.tabs(["🚀 Regression", "🗺️ Route Mapping", "📡 Scanner & Mapper", "📊 Analysis"])

# --- TAB: Regression ---
with tabs[0]:
    st.header("Execute Regression Test")
    current_report = latest_report_path()
    if current_report:
        st.subheader("Latest Report")
        render_report_links(current_report, key_prefix="latest")

    st.markdown("### Page Cards")
    queue_browser_label = st.selectbox(
        "Browser for all page cards",
        list(BROWSER_OPTIONS.keys()),
        index=0,
        key="reg_queue_browser",
    )
    queue_browser = BROWSER_OPTIONS[queue_browser_label]

    if "reg_page_card_count" not in st.session_state:
        st.session_state["reg_page_card_count"] = 1
    if "reg_focus_card_index" not in st.session_state:
        st.session_state["reg_focus_card_index"] = 1

    st.markdown("#### Queue Records")
    if st.session_state.get("reg_queue_record_status"):
        st.success(st.session_state.pop("reg_queue_record_status"))
    records = load_reg_queue_records()
    record_labels = reg_queue_record_labels(records)
    record_by_id = {str(record.get("id")): record for record in records}
    rec_col1, rec_col2, rec_col3, rec_col4 = st.columns([2, 2, 1, 1])
    with rec_col1:
        record_name = st.text_input(
            "Record name",
            value="",
            placeholder="例：admin upload + error cases",
            key="reg_queue_record_name",
        )
    with rec_col2:
        selected_record_label = st.selectbox(
            "Saved records",
            record_labels,
            index=None,
            placeholder="Select saved queue",
            key="reg_queue_record_select",
        )
        selected_record_id = selected_page_from_label(selected_record_label)
    with rec_col3:
        if st.button("Save Queue", key="reg_queue_save"):
            record = save_current_reg_queue_record(record_name)
            st.session_state["reg_queue_record_status"] = f"Saved: {record['name']}"
            st.rerun()
    with rec_col4:
        load_disabled = not selected_record_id or selected_record_id not in record_by_id
        if st.button("Load", key="reg_queue_load", disabled=load_disabled):
            apply_reg_queue_state(record_by_id[selected_record_id])
            st.session_state["reg_queue_record_status"] = f"Loaded: {record_by_id[selected_record_id].get('name')}"
            st.rerun()

    if selected_record_id in record_by_id:
        selected_record = record_by_id[selected_record_id]
        q = selected_record.get("queue") or {}
        st.caption(
            f"Selected: {selected_record.get('name')} / browser={q.get('browser_label', '-')} / "
            f"cards={len(q.get('cards') or [])} / saved_at={selected_record.get('saved_at', '-')}"
        )
        st.caption("记录会保存所有卡片配置和上传 case 选择；上传文件本体需要在运行前重新选择。")
        if st.button("Delete Selected Record", key="reg_queue_delete"):
            save_reg_queue_records([record for record in records if str(record.get("id")) != selected_record_id])
            st.session_state["reg_queue_record_status"] = f"Deleted: {selected_record.get('name')}"
            st.rerun()

    card_col1, card_col2, card_col3 = st.columns([1, 1, 4])
    with card_col1:
        if st.button("＋ Add Page", key="reg_add_page_card"):
            st.session_state["reg_page_card_count"] += 1
            st.session_state["reg_focus_card_index"] = st.session_state["reg_page_card_count"]
            st.rerun()
    with card_col2:
        remove_disabled = st.session_state["reg_page_card_count"] <= 1
        if st.button("－ Remove Page", key="reg_remove_page_card", disabled=remove_disabled):
            clear_reg_page_card_state(st.session_state["reg_page_card_count"])
            st.session_state["reg_page_card_count"] = max(1, st.session_state["reg_page_card_count"] - 1)
            st.session_state["reg_focus_card_index"] = st.session_state["reg_page_card_count"]
            st.rerun()
    with card_col3:
        st.caption(f"{st.session_state['reg_page_card_count']} page card(s)")

    page_configs = []
    for index in range(1, int(st.session_state["reg_page_card_count"]) + 1):
        target_value_key = f"reg_page_target_value_{index}"
        target_select_key = f"reg_page_target_{index}"
        stable_target = (
            st.session_state.get(target_value_key)
            or selected_page_from_label(st.session_state.get(target_select_key, ""))
        )
        if stable_target and not selected_page_from_label(st.session_state.get(target_select_key, "")):
            st.session_state[target_select_key] = _page_option_label_for(stable_target)
        current_page_label = str(stable_target or "").strip() or "未选择 JSP"
        with st.expander(f"{index}. {current_page_label}", expanded=index == int(st.session_state.get("reg_focus_card_index", 1))):
            enabled = st.checkbox("Enabled", value=True, key=f"reg_page_enabled_{index}")
            target_selection = st.selectbox(
                "Target JSP",
                PAGE_OPTION_LABELS,
                index=None,
                placeholder="Select or type Target JSP",
                accept_new_options=True,
                key=f"reg_page_target_{index}",
            )
            target_page = selected_page_from_label(target_selection).strip() or str(stable_target or "").strip()
            st.session_state[target_value_key] = target_page

            option_meta = next((item for item in PAGE_OPTIONS if item["page_id"].lower() == target_page.lower()), None)
            if option_meta:
                st.caption(
                    " / ".join(
                        item
                        for item in [
                            f"entry: {option_meta.get('entry_url')}" if option_meta.get("entry_url") else "",
                            f"action: {option_meta.get('action')}" if option_meta.get("action") else "",
                            f"risk: {option_meta.get('risk')}" if option_meta.get("risk") else "",
                            "route: yes" if option_meta.get("route_map_path") else "",
                        ]
                        if item
                    )
                )
            elif target_page:
                st.warning("当前 page_mapping/route/recent reports 中没有找到该 JSP；仍允许手工执行。")

            col_a, col_b, col_c = st.columns(3)
            with col_a:
                login_entry = st.selectbox(
                    "Login Entry",
                    LOGIN_ENTRY_NAMES,
                    index=0,
                    key=f"reg_page_login_{index}",
                )
                checklist_path = st.text_input("Checklist Path", value=DEFAULT_CHECKLIST_PATH, key=f"reg_page_checklist_{index}")
            with col_b:
                route_map_path = st.text_input("Route Map Path", value=DEFAULT_ROUTE_MAP_PATH, key=f"reg_page_route_{index}")
                force_route_map = st.checkbox("Use Route Map", value=True, key=f"reg_page_force_route_{index}")
                manual_mode = st.checkbox("Manual Takeover", value=False, key=f"reg_page_manual_{index}")
            with col_c:
                risk_only = st.checkbox("Risk Only", value=False, key=f"reg_page_risk_{index}")
                include_semi_auto = st.checkbox("Include semi-auto", value=False, key=f"reg_page_semi_{index}")
                include_destructive = st.checkbox("Include destructive", value=False, key=f"reg_page_destructive_{index}")
                include_negative = st.checkbox("Include negative", value=False, key=f"reg_page_negative_{index}")
                negative_options = load_negative_profile_options(checklist_path, target_page)
                negative_labels = negative_profile_labels(negative_options)
                saved_negative_profiles = st.session_state.pop(f"reg_page_saved_negative_profiles_{index}", None)
                if saved_negative_profiles is not None:
                    st.session_state[f"reg_page_negative_profiles_{index}"] = _negative_labels_for_profiles(
                        negative_options,
                        saved_negative_profiles,
                    )
                selected_negative_labels = st.multiselect(
                    "Negative profiles",
                    negative_labels,
                    default=[],
                    disabled=not include_negative,
                    key=f"reg_page_negative_profiles_{index}",
                )
                negative_profile = ",".join(selected_page_from_label(label) for label in selected_negative_labels)
                if include_negative and not negative_profile:
                    st.warning("请选择至少一个 Negative profile。")
                elif include_negative:
                    selected_descriptions = [
                        (next((item for item in negative_options if item.get("profile") == selected_page_from_label(label)), {}) or {}).get("description", "")
                        for label in selected_negative_labels
                    ]
                    st.caption(" / ".join(item for item in selected_descriptions if item))

            st.markdown("#### Upload Files by Case")
            upload_cases = load_upload_case_options(checklist_path, target_page)
            upload_case_labels = upload_case_option_labels(upload_cases)
            upload_case_by_id = {str(case.get("case_id") or ""): case for case in upload_cases}
            if target_page and not upload_cases:
                st.caption("No upload cases found in the selected checklist for this page.")
            profile_count_key = f"reg_page_profile_count_{index}"
            if int(st.session_state.get(profile_count_key, 0) or 0) > len(upload_cases):
                st.session_state[profile_count_key] = len(upload_cases)
            profile_count = st.number_input(
                "Upload file count",
                min_value=0,
                max_value=len(upload_cases),
                value=0,
                step=1,
                key=profile_count_key,
            )
            upload_profiles = []
            for profile_index in range(int(profile_count)):
                p_col1, p_col2 = st.columns([2, 1])
                with p_col1:
                    saved_case_id = st.session_state.pop(f"reg_profile_saved_case_id_{index}_{profile_index}", None)
                    if saved_case_id:
                        saved_case_label = _upload_case_label_for(upload_case_labels, saved_case_id)
                        if saved_case_label:
                            st.session_state[f"reg_profile_case_id_{index}_{profile_index}"] = saved_case_label
                    selected_upload_case_label = st.selectbox(
                        "Upload case_id",
                        upload_case_labels,
                        index=None,
                        placeholder="Select upload case_id",
                        key=f"reg_profile_case_id_{index}_{profile_index}",
                    )
                    selected_upload_case_id = selected_page_from_label(selected_upload_case_label)
                    upload_case = upload_case_by_id.get(selected_upload_case_id, {})
                with p_col2:
                    profile_file = st.file_uploader("Upload file", key=f"reg_profile_file_{index}_{profile_index}")
                if upload_case:
                    st.caption(
                        " / ".join(
                            item
                            for item in [
                                upload_case.get("test_title"),
                                f"locator: {upload_case.get('locator')}" if upload_case.get("locator") else "",
                                f"submit: {upload_case.get('submit_locator')}" if upload_case.get("submit_locator") else "",
                            ]
                            if item
                        )
                    )
                upload_profiles.append(
                    {
                        "name": selected_upload_case_id or f"upload_case_{profile_index + 1}",
                        "case_id": selected_upload_case_id,
                        "test_title": upload_case.get("test_title") or "",
                        "uploaded_file": profile_file,
                        "case_type": upload_case.get("case_type") or "upload_submit",
                        "locator": upload_case.get("locator") or "",
                        "submit_locator": upload_case.get("submit_locator") or "",
                        "negative": False,
                    }
                )

            browser = queue_browser
            page_config = {
                "index": index,
                "enabled": enabled,
                "target_page": target_page,
                "browser": browser,
                "login_entry": login_entry,
                "checklist_path": checklist_path,
                "route_map_path": route_map_path,
                "force_route_map": force_route_map,
                "manual": manual_mode,
                "risk_only": risk_only,
                "include_semi_auto": include_semi_auto,
                "include_destructive": include_destructive,
                "include_negative": include_negative,
                "negative_profile": negative_profile,
                "upload_profiles_raw": upload_profiles,
                "html_path": html_report_path(browser, target_page),
                "regression_output_dir": regression_output_dir(browser),
            }
            preview_config = dict(page_config)
            if any(profile.get("uploaded_file") for profile in upload_profiles):
                preview_config["upload_profile_config"] = upload_profile_config_path(browser, target_page)
            if target_page:
                try:
                    preview_cmd = build_regression_command(preview_config, pytest_cmd=PYTEST_CMD)
                    st.code(preview_cmd)
                except ValueError as exc:
                    st.warning(str(exc))
            else:
                st.caption("Select or enter a Target JSP to preview the command.")
            page_configs.append(page_config)

    enabled_configs = [item for item in page_configs if item.get("enabled") and item.get("target_page")]
    if enabled_configs:
        st.caption(f"Ready: {len(enabled_configs)} page(s)")

    if st.button("🔥 Launch Regression Queue", key="reg_launch"):
        if not enabled_configs:
            st.error("Please add at least one enabled target page.")
            st.stop()

        completed_reports = []
        for page_config in enabled_configs:
            runtime_config = dict(page_config)
            upload_profiles = []
            for profile in page_config.get("upload_profiles_raw") or []:
                uploaded_file = profile.get("uploaded_file")
                if uploaded_file is None or not profile.get("case_id"):
                    continue
                file_path = save_uploaded_file(uploaded_file, subdir=f"gui_regression/{browser_key(page_config['browser'])}")
                upload_profiles.append({**profile, "file": str(file_path)})
            runtime_config["upload_profiles"] = upload_profiles
            upload_profile_config = write_upload_profile_config(runtime_config)
            if upload_profile_config:
                runtime_config["upload_profile_config"] = str(upload_profile_config)

            if runtime_config.get("checklist_path") and not Path(runtime_config["checklist_path"]).exists():
                st.warning(f"Checklist not found, using page mapping fallback: {runtime_config['checklist_path']}")

            full_cmd = build_regression_command(runtime_config, pytest_cmd=PYTEST_CMD)
            st.info(f"Running: `{full_cmd}`")
            code, _ = run_command(full_cmd)
            report_path = Path(runtime_config["html_path"])
            if report_path.exists():
                completed_reports.append(report_path)
            if code != 0:
                st.error(f"Regression failed for {runtime_config['target_page']} (exit={code}).")
                break

        if completed_reports:
            st.success("Regression Queue Complete.")
            for report_path in completed_reports:
                render_report_links(report_path, key_prefix=f"completed_{report_path.as_posix()}")

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
        v_manual_route = st.checkbox("Manual Full Route", value=False, key="rv_manual_route")
        v_use_upload_file = st.checkbox("Use Upload File", value=False, key="rv_use_upload_file")
        v_selected_upload_file = st.file_uploader("Upload File", key="rv_upload_file")
        
        if st.button("🛡️ Verify Route Consistency", key="rv_verify_route"):
            if not v_target.strip():
                st.error("Target JSP is required.")
            else:
                stem = route_file_stem(v_target)
                cand_file = f"generated/valid/route/route_candidates_{stem}.json"
                out_file = f"generated/valid/route/usable_route_map_{v_side}_{stem}.json"

                if not v_manual_route and not os.path.exists(cand_file):
                    st.error(f"Candidate file not found: {cand_file}")
                else:
                    cmd = f"{PYTHON_CMD} -m src.route_map_runner --candidates {quote(cand_file)} --target {quote(v_target)} --side {v_side} --browser {BROWSER_OPTIONS[v_browser_label]} --output {quote(out_file)} --manual-data"
                    if v_manual_route:
                        cmd += " --manual-route"
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
                    code, _ = run_command_in_powershell(cmd)
                    if code == 0:
                        st.success(f"Route map saved: {out_file}")
                        if Path("generated/valid/page_mapping.json").exists():
                            st.info("Runtime profile saved. Regenerating checklist from current mapping/profile data...")
                            checklist_cmd = f"{PYTHON_CMD} src/checklist_generator.py generated/valid/page_mapping.json -o generated/valid/migration_checklist.xlsx"
                            run_command(checklist_cmd)
                    else:
                        st.error(f"Route verification failed: exit={code}")

# --- TAB: Scanner & Mapper ---
with tabs[2]:
    st.header("Static Analysis Pipeline")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Step 1: Scan JSP Assets")
        leg_path = st.text_input("Legacy JSP Root Path", key="scan_legacy_root")
        new_path = st.text_input("New JSP Root Path", key="scan_new_root")
        
        if st.button("🔍 Run Full Scan", key="scan_run_full"):
            leg_path = clean_path_input(leg_path)
            new_path = clean_path_input(new_path)
            if leg_path:
                run_command(f"{PYTHON_CMD} src/jsp_scanner.py {quote(leg_path)} -o mappings/legacy_elements.json")
            if new_path:
                run_command(f"{PYTHON_CMD} src/jsp_scanner.py {quote(new_path)} -o mappings/new_elements.json")

    with col2:
        st.subheader("Step 2: Bridge Time & Space")
        generate_checklist_after_mapping = st.checkbox("Generate checklist after mapping", value=True, key="scan_generate_checklist_after_mapping")
        if st.button("🌉 Generate Mapping", key="scan_generate_mapping"):
            cmd = f"{PYTHON_CMD} src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json -o generated/valid/page_mapping.json --md generated/valid/comparison_summary.md"
            code, _ = run_command(cmd)
            if code == 0 and generate_checklist_after_mapping:
                checklist_cmd = f"{PYTHON_CMD} src/checklist_generator.py generated/valid/page_mapping.json -o generated/valid/migration_checklist.xlsx"
                run_command(checklist_cmd)

        st.subheader("Step 3: Test Documents")
        if st.button("📊 Export Excel Checklist", key="scan_export_checklist"):
            cmd = f"{PYTHON_CMD} src/checklist_generator.py generated/valid/page_mapping.json -o generated/valid/migration_checklist.xlsx"
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
