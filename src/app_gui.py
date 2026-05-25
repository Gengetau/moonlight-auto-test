import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import os
import threading
from pathlib import Path
import json

class MoonlightGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🌕 Moonlight Control Center - Full Edition")
        self.root.geometry("11000x850")
        self.root.configure(bg="#1e1e1e")

        # Project root detection
        self.project_root = Path(__file__).resolve().parents[1]
        os.chdir(self.project_root)

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
        ttk.Label(header_frame, text="“月光所照之处，错误无所遁形。”", font=("Segoe UI", 10, "italic")).pack(side="right")

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
        ttk.Separator(frame, orient="horizontal").grid(row=4, column=0, columnspan=3, fill="x", pady=20)
        ttk.Label(frame, text="Step 2: Time & Space Bridge", style="SubHeader.TLabel").grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 10))
        
        ttk.Button(frame, text="🌉 Generate Mapping & Summary", command=self.run_mapping).grid(row=6, column=1, sticky="w", padx=10)
        
        # Step 3: Checklist
        ttk.Separator(frame, orient="horizontal").grid(row=7, column=0, columnspan=3, fill="x", pady=20)
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
        self.reg_entry = ttk.Entry(frame, width=20)
        self.reg_entry.insert(0, "dev-a")
        self.reg_entry.grid(row=2, column=1, sticky="w", padx=10)

        self.reg_risk = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Risk-Only Mode (High/Medium Diffs)", variable=self.reg_risk).grid(row=3, column=1, sticky="w", pady=5, padx=10)

        self.reg_manual = tk.BooleanVar()
        ttk.Checkbutton(frame, text="Takeover Mode (Manual login/nav first)", variable=self.reg_manual).grid(row=4, column=1, sticky="w", pady=5, padx=10)

        ttk.Button(frame, text="🔥 Launch Regression Engine", command=self.run_regression).grid(row=5, column=1, sticky="w", pady=20, padx=10)

    def setup_route_tab(self):
        frame = ttk.Frame(self.tab_route, padding=20)
        frame.pack(fill="both")

        # Route Generation
        ttk.Label(frame, text="1. Scout Candidates (Struts Tracer)", style="SubHeader.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        
        ttk.Label(frame, text="Target JSP:").grid(row=1, column=0, sticky="w", pady=5)
        self.route_target = ttk.Entry(frame, width=40)
        self.route_target.insert(0, "ProjectMemberUploadDisp.jsp")
        self.route_target.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(frame, text="Entry JSP:").grid(row=2, column=0, sticky="w", pady=5)
        self.route_entry = ttk.Entry(frame, width=40)
        self.route_entry.insert(0, "PatlicsMenu.jsp")
        self.route_entry.grid(row=2, column=1, sticky="w", padx=10)

        ttk.Button(frame, text="🛰️ Scout Paths", command=self.run_route_scout).grid(row=3, column=1, sticky="w", pady=5, padx=10)

        # Route Verification
        ttk.Separator(frame, orient="horizontal").grid(row=4, column=0, columnspan=2, fill="x", pady=20)
        ttk.Label(frame, text="2. Verify Route Consistency", style="SubHeader.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 10))
        
        ttk.Label(frame, text="Side:").grid(row=6, column=0, sticky="w", pady=5)
        self.route_side = ttk.Combobox(frame, values=["legacy", "new"], width=10)
        self.route_side.current(0)
        self.route_side.grid(row=6, column=1, sticky="w", padx=10)

        ttk.Button(frame, text="🛡️ Verify Selected Route", command=self.run_route_verify).grid(row=7, column=1, sticky="w", pady=10, padx=10)

    def browse_dir(self, entry_widget):
        directory = filedialog.askdirectory()
        if directory:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, directory)

    def log(self, text):
        self.log_area.insert(tk.END, text + "\n")
        self.log_area.see(tk.END)

    def run_command(self, cmd, success_msg="Task completed successfully!"):
        def target():
            self.log(f"> Running: {cmd}")
            # Ensure venv usage
            full_cmd = f"{self.project_root}/venv/bin/{cmd}"
            process = subprocess.Popen(full_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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
        leg = self.path_legacy.get().strip()
        new = self.path_new.get().strip()
        if leg:
            self.run_command(f"python3 src/jsp_scanner.py {leg} -o mappings/legacy_elements.json", "Legacy scan finished.")
        if new:
            self.run_command(f"python3 src/jsp_scanner.py {new} -o mappings/new_elements.json", "New scan finished.")

    def run_mapping(self):
        cmd = "python3 src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json -o generated/valid/page_mapping.json --md generated/valid/comparison_summary.md"
        self.run_command(cmd, "Mapping generated. View comparison_summary.md for details.")

    def run_checklist(self):
        cmd = "python3 src/checklist_generator.py generated/valid/page_mapping.json -o generated/migration_checklist.xlsx"
        self.run_command(cmd, "Excel Checklist exported to generated/ folder.")

    def run_regression(self):
        target = self.reg_target.get().strip()
        entry = self.reg_entry.get().strip()
        cmd = "pytest tests/test_migration.py --run-migration"
        if target: cmd += f" --target-page={target}"
        if self.reg_risk.get(): cmd += " --risk-only"
        if self.reg_manual.get(): cmd += " --manual"
        if entry: cmd += f" --login-entry={entry}"
        cmd += " --html=output/gui_regression_report.html"
        self.run_command(cmd, "Regression finished. Check output/ for HTML report.")

    def run_route_scout(self):
        target = self.route_target.get().strip()
        entry = self.route_entry.get().strip()
        if not target: return
        out = f"generated/valid/route_candidates_{target.replace('.jsp','')}.json"
        cmd = f"python3 -m src.route_catalog --target {target} --entry {entry} --output {out}"
        self.run_command(cmd, f"Scouted candidates for {target}.")

    def run_route_verify(self):
        target = self.route_target.get().strip()
        side = self.route_side.get()
        cand = f"generated/valid/route_candidates_{target.replace('.jsp','')}.json"
        out = f"generated/valid/usable_route_map_{side}_{target.replace('.jsp','')}.json"
        if not Path(cand).exists():
            messagebox.showerror("Error", f"Candidates file missing: {cand}")
            return
        cmd = f"python3 -m src.route_map_runner --candidates {cand} --target {target} --side {side} --output {out} --manual-data"
        self.run_command(cmd, f"Route verification finished for {side} side.")

if __name__ == "__main__":
    root = tk.Tk()
    app = MoonlightGUI(root)
    root.mainloop()
