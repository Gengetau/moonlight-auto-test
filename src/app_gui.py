import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import os
import threading
from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config_parser import Config

BROWSER_OPTIONS = {
    "Chrome portable": "chrome_port",
    "Microsoft Edge": "edge",
    "Firefox": "firefox",
}


class MoonlightGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🌕 Moonlight Control Center - Full Edition")
        self.root.geometry("1100x850")
        self.root.configure(bg="#1e1e1e")

        # Project root detection
        self.project_root = PROJECT_ROOT
        os.chdir(self.project_root)
        self.python_cmd = self.venv_executable("python")
        self.pytest_cmd = self.venv_executable("pytest")
        self.login_entry_names = self.load_login_entry_names()

        self.setup_styles()
        self.create_widgets()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background="#1e1e1e")
        style.configure("TLabel", background="#1e1e1e", foreground="#e0e0e0", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=5)
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"), foreground="#4a90e2")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 12, "bold"), foreground="#9292ac")
        style.configure("TNotebook", background="#1e1e1e")
        style.configure("TNotebook.Tab", background="#2e3b4e", foreground="white", padding=[10, 5])
        style.map("TNotebook.Tab", background=[("selected", "#4a90e2")])

    def create_widgets(self):
        # Header
        header_frame = ttk.Frame(self.root)
        header_frame.pack(fill="x", padx=20, pady=20)
        ttk.Label(header_frame, text="🌕 Moonlight Control Center", style="Header.TLabel").pack(side="left")

        # Main Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=10)

        # Tab: Scanner & Mapper
        self.tab_scan = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_scan, text=" 🔍 Scanner & Mapper ")
        self.setup_scan_tab()

        # Tab: Regression
        self.tab_regression = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_regression, text=" 🚀 Regression ")
        self.setup_regression_tab()

        # Tab: Route Scouting
        self.tab_route = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_route, text=" 🗺️ Route Intelligence ")
        self.setup_route_tab()

        # Console Output
        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=20, pady=20)
        ttk.Label(log_frame, text="📡 Console Output:", font=("Consolas", 10, "bold")).pack(anchor="w")
        self.log_area = scrolledtext.ScrolledText(log_frame, height=12, bg="#000000", fg="#00ff00", font=("Consolas", 9))
        self.log_area.pack(fill="both", expand=True, pady=5)

    def setup_scan_tab(self):
        frame = ttk.Frame(self.tab_scan, padding=20)
        frame.pack(fill="both")

        # Step 1: Scan
        ttk.Label(frame, text="Step 1: JSP Scanning", style="SubHeader.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        
        ttk.Label(frame, text="Legacy JSP Path:").grid(row=1, column=0, sticky="w", pady=5)
        self.path_legacy = ttk.Entry(frame, width=60)
        self.path_legacy.grid(row=1, column=1, sticky="w", padx=10)
        ttk.Button(frame, text="Browse", command=lambda: self.browse_dir(self.path_legacy)).grid(row=1, column=2, sticky="w")

        ttk.Label(frame, text="New JSP Path:").grid(row=2, column=0, sticky="w", pady=5)
        self.path_new = ttk.Entry(frame, width=60)
        self.path_new.grid(row=2, column=1, sticky="w", padx=10)
        ttk.Button(frame, text="Browse", command=lambda: self.browse_dir(self.path_new)).grid(row=2, column=2, sticky="w")

        ttk.Button(frame, text="🔍 Start Full Scan", command=self.run_scan).grid(row=3, column=1, sticky="w", pady=10, padx=10)

        # Step 2: Mapping
        ttk.Separator(frame, orient="horizontal").grid(row=4, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(frame, text="Step 2: Time & Space Bridge", style="SubHeader.TLabel").grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 10))
        
        ttk.Button(frame, text="🌉 Generate Mapping & Summary", command=self.run_mapping).grid(row=6, column=1, sticky="w", padx=10)
        
        # Step 3: Checklist
        ttk.Separator(frame, orient="horizontal").grid(row=7, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(frame, text="Step 3: Test Documents", style="SubHeader.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Button(frame, text="📊 Export Excel Checklist", command=self.run_checklist).grid(row=9, column=1, sticky="w", padx=10)

    def setup_regression_tab(self):
        frame = ttk.Frame(self.tab_regression, padding=20)
        frame.pack(fill="both")

        ttk.Label(frame, text="Regression Configuration", style="SubHeader.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Label(frame, text="Target Page (e.g. AbstListEdit.jsp):").grid(row=1, column=0, sticky="w", pady=5)
        self.reg_target = ttk.Entry(frame, width=40)
        self.reg_target.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Login Entry (from .env):").grid(row=2, column=0, sticky="w", pady=5)
        self.reg_entry = ttk.Combobox(frame, values=self.login_entry_names, width=20, state="readonly")
        if self.login_entry_names:
            self.reg_entry.current(0)
        self.reg_entry.grid(row=2, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Browser:").grid(row=3, column=0, sticky="w", pady=5)
        self.reg_browser = ttk.Combobox(frame, values=list(BROWSER_OPTIONS.keys()), width=20, state="readonly")
        self.reg_browser.current(0)
        self.reg_browser.grid(row=3, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Checklist Path:").grid(row=4, column=0, sticky="w", pady=5)
        self.reg_checklist = ttk.Entry(frame, width=40)
        self.reg_checklist.insert(0, "generated/valid/migration_checklist.xlsx")
        self.reg_checklist.grid(row=4, column=1, sticky="w", padx=10)
        ttk.Button(frame, text="Browse", command=lambda: self.browse_file(self.reg_checklist, [("Excel files", "*.xlsx"), ("All files", "*.*")])).grid(row=4, column=2, sticky="w")

        self.reg_risk = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Risk-Only Mode (High/Medium Diffs)", variable=self.reg_risk).grid(row=5, column=1, sticky="w", pady=5, padx=10)

        self.reg_manual = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Takeover Mode (Manual login/nav first)", variable=self.reg_manual).grid(row=6, column=1, sticky="w", pady=5, padx=10)

        self.reg_force_route_map = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Use Route Map", variable=self.reg_force_route_map).grid(row=7, column=1, sticky="w", pady=5, padx=10)

        self.reg_use_upload_file = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Use Upload File", variable=self.reg_use_upload_file).grid(row=8, column=1, sticky="w", pady=5, padx=10)

        ttk.Label(frame, text="Upload File:").grid(row=9, column=0, sticky="w", pady=5)
        self.reg_upload_file = ttk.Entry(frame, width=40)
        self.reg_upload_file.grid(row=9, column=1, sticky="w", padx=10)
        ttk.Button(frame, text="Browse", command=lambda: self.browse_file(self.reg_upload_file, [("All files", "*.*")])).grid(row=9, column=2, sticky="w")

        ttk.Button(frame, text="🔥 Launch Regression Engine", command=self.run_regression).grid(row=10, column=1, sticky="w", pady=20, padx=10)

    def setup_route_tab(self):
        frame = ttk.Frame(self.tab_route, padding=20)
        frame.pack(fill="both")

        # Route Generation
        ttk.Label(frame, text="1. Scout Candidates (Struts Tracer)", style="SubHeader.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        
        ttk.Label(frame, text="Target JSP:").grid(row=1, column=0, sticky="w", pady=5)
        self.route_target = ttk.Entry(frame, width=40)
        self.route_target.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Entry JSP:").grid(row=2, column=0, sticky="w", pady=5)
        self.route_entry = ttk.Entry(frame, width=40)
        self.route_entry.insert(0, "PatlicsMenu.jsp")
        self.route_entry.grid(row=2, column=1, sticky="w", padx=10)

        ttk.Button(frame, text="🛰️ Scout Paths", command=self.run_route_scout).grid(row=3, column=1, sticky="w", pady=5, padx=10)

        # Route Verification
        ttk.Separator(frame, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=20)
        ttk.Label(frame, text="2. Verify Route Consistency", style="SubHeader.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 10))
        
        ttk.Label(frame, text="Side:").grid(row=6, column=0, sticky="w", pady=5)
        self.route_side = ttk.Combobox(frame, values=["legacy", "new"], width=10)
        self.route_side.current(0)
        self.route_side.grid(row=6, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Login Entry (from .env):").grid(row=7, column=0, sticky="w", pady=5)
        self.route_login_entry = ttk.Combobox(frame, values=self.login_entry_names, width=20, state="readonly")
        if self.login_entry_names:
            self.route_login_entry.current(0)
        self.route_login_entry.grid(row=7, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Browser:").grid(row=8, column=0, sticky="w", pady=5)
        self.route_browser = ttk.Combobox(frame, values=list(BROWSER_OPTIONS.keys()), width=20, state="readonly")
        self.route_browser.current(0)
        self.route_browser.grid(row=8, column=1, sticky="w", padx=10)

        self.route_auto_login = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Auto Login", variable=self.route_auto_login).grid(row=9, column=1, sticky="w", pady=5, padx=10)

        self.route_use_upload_file = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Use Upload File", variable=self.route_use_upload_file).grid(row=10, column=1, sticky="w", pady=5, padx=10)

        ttk.Label(frame, text="Upload File:").grid(row=11, column=0, sticky="w", pady=5)
        self.route_upload_file = ttk.Entry(frame, width=40)
        self.route_upload_file.grid(row=11, column=1, sticky="w", padx=10)
        ttk.Button(frame, text="Browse", command=lambda: self.browse_file(self.route_upload_file, [("All files", "*.*")])).grid(row=11, column=2, sticky="w")

        ttk.Button(frame, text="🛡️ Verify Selected Route", command=self.run_route_verify).grid(row=12, column=1, sticky="w", pady=10, padx=10)

    def browse_dir(self, entry_widget):
        directory = filedialog.askdirectory()
        if directory:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, directory)

    def browse_file(self, entry_widget, filetypes):
        filename = filedialog.askopenfilename(filetypes=filetypes)
        if filename:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, filename)

    def log(self, text):
        self.log_area.insert(tk.END, text + "\n")
        self.log_area.see(tk.END)

    def venv_executable(self, name):
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        executable = f"{name}.exe" if os.name == "nt" else name
        candidate = self.project_root / "venv" / scripts_dir / executable
        if candidate.exists():
            return str(candidate)
        return sys.executable if name == "python" else name

    @staticmethod
    def load_login_entry_names():
        try:
            names = [entry["name"] for entry in Config.login_entries() if entry.get("name")]
        except Exception:
            names = []
        return names or ["entry-1"]

    @staticmethod
    def quote(value):
        text = str(value)
        return '"' + text.replace('"', '\\"') + '"'

    @staticmethod
    def clean_path_input(value):
        text = str(value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1].strip()
        return text

    @staticmethod
    def route_file_stem(target):
        text = str(target or "").replace("\\", "/").strip().strip("/")
        if text.lower().endswith(".jsp"):
            text = text[:-4]
        text = text.replace("/", "_")
        safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in text)
        return safe or "target"

    def run_command(self, cmd, success_msg="Task completed successfully!"):
        def target():
            self.log(f"> Running: {cmd}")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            if process.stdout:
                for line in process.stdout:
                    self.log(line.strip())
            process.wait()
            self.log(f"--- Process exited with code {process.returncode} ---")
            if process.returncode == 0:
                messagebox.showinfo("Success", success_msg)
            else:
                messagebox.showerror("Error", "Task failed. Check console output.")

        threading.Thread(target=target, daemon=True).start()

    def run_scan(self):
        leg = self.clean_path_input(self.path_legacy.get())
        new = self.clean_path_input(self.path_new.get())
        if leg:
            self.run_command(f"{self.quote(self.python_cmd)} src/jsp_scanner.py {self.quote(leg)} -o mappings/legacy_elements.json", "Legacy scan finished.")
        if new:
            self.run_command(f"{self.quote(self.python_cmd)} src/jsp_scanner.py {self.quote(new)} -o mappings/new_elements.json", "New scan finished.")

    def run_mapping(self):
        cmd = f"{self.quote(self.python_cmd)} src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json -o generated/valid/page_mapping.json --md generated/valid/comparison_summary.md"
        self.run_command(cmd, "Mapping generated. View comparison_summary.md for details.")

    def run_checklist(self):
        cmd = f"{self.quote(self.python_cmd)} src/checklist_generator.py generated/valid/page_mapping.json -o generated/valid/migration_checklist.xlsx"
        self.run_command(cmd, "Excel Checklist exported to generated/ folder.")

    def run_regression(self):
        target = self.reg_target.get().strip()
        entry = self.reg_entry.get().strip()
        browser = BROWSER_OPTIONS.get(self.reg_browser.get(), "chrome_port")
        checklist = self.reg_checklist.get().strip()
        cmd = f"{self.quote(self.pytest_cmd)} tests/test_migration.py --run-migration --test-browser={browser}"
        if target: cmd += f" --target-page={target}"
        if self.reg_risk.get(): cmd += " --risk-only"
        if self.reg_manual.get(): cmd += " --manual"
        if entry: cmd += f" --login-entry={entry}"
        if checklist:
            if Path(checklist).exists():
                cmd += f" --checklist-path={self.quote(checklist)}"
            else:
                self.log(f"[WARN] Checklist not found, using page mapping fallback: {checklist}")
        if self.reg_force_route_map.get():
            cmd += " --force-route-map --route-map-path=generated/valid/route"
        if self.reg_use_upload_file.get():
            upload_file = self.reg_upload_file.get().strip()
            if not upload_file or not Path(upload_file).exists():
                messagebox.showerror("Error", "Upload File is required when Use Upload File is checked.")
                return
            cmd += f" --upload-file={self.quote(upload_file)}"
        cmd += " --html=output/gui_regression_report.html"
        self.run_command(cmd, "Regression finished. Check output/ for HTML report.")

    def run_route_scout(self):
        target = self.route_target.get().strip()
        entry = self.route_entry.get().strip()
        if not target:
            messagebox.showerror("Error", "Target JSP is required.")
            return
        route_dir = Path("generated/valid/route")
        route_dir.mkdir(parents=True, exist_ok=True)
        out = route_dir / f"route_candidates_{self.route_file_stem(target)}.json"
        cmd = f"{self.quote(self.python_cmd)} -m src.route_catalog --target {self.quote(target)} --output {self.quote(out)}"
        if entry:
            cmd += f" --entry {self.quote(entry)}"
        self.run_command(cmd, f"Scouted candidates for {target}.")

    def run_route_verify(self):
        target = self.route_target.get().strip()
        side = self.route_side.get()
        login_entry = self.route_login_entry.get().strip()
        browser = BROWSER_OPTIONS.get(self.route_browser.get(), "chrome_port")
        if not target:
            messagebox.showerror("Error", "Target JSP is required.")
            return
        route_dir = Path("generated/valid/route")
        stem = self.route_file_stem(target)
        cand = route_dir / f"route_candidates_{stem}.json"
        out = route_dir / f"usable_route_map_{side}_{stem}.json"
        if not Path(cand).exists():
            messagebox.showerror("Error", f"Candidates file missing: {cand}")
            return
        cmd = f"{self.quote(self.python_cmd)} -m src.route_map_runner --candidates {self.quote(cand)} --target {self.quote(target)} --side {side} --browser {browser} --output {self.quote(out)} --manual-data"
        if login_entry:
            cmd += f" --login-entry {self.quote(login_entry)}"
        if self.route_auto_login.get():
            cmd += " --auto-login"
        if self.route_use_upload_file.get():
            upload_file = self.route_upload_file.get().strip()
            if not upload_file or not Path(upload_file).exists():
                messagebox.showerror("Error", "Upload File is required when Use Upload File is checked.")
                return
            cmd += f" --upload-file {self.quote(upload_file)}"
        self.run_command(cmd, f"Route verification finished for {side} side.")

if __name__ == "__main__":
    root = tk.Tk()
    app = MoonlightGUI(root)
    root.mainloop()
