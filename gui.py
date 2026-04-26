#!/usr/bin/env python3
"""
Simple GUI for Relay Controller.
Left: editable config params.  Right: live log output.
Buttons: Start / Stop.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import os
import subprocess
import threading
import sys
import time
from datetime import datetime
import serial
try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None
try:
    import psutil
except ImportError:
    psutil = None
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
ENV_FILE   = SCRIPT_DIR / ".env"
SCRIPT     = SCRIPT_DIR / "pc_relay_script.py"

# When frozen as .exe (PyInstaller), __file__ points inside _MEIPASS temp dir.
# Use the folder of the exe itself for .env and the script.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
    ENV_FILE   = SCRIPT_DIR / ".env"
    SCRIPT     = SCRIPT_DIR / "pc_relay_script.py"

# Fields to show in GUI (key, label, description)
FIELDS = [
    ("DATA_FILE",             "Data file",             "Path to OdczytTH .txt"),
    ("SERIAL_PORT",           "Serial port",           "e.g. COM6"),
    ("BAUD_RATE",             "Baud rate",             "Must match Arduino (9600)"),
    ("POLL_INTERVAL",         "Poll interval (s)",     "How often to check, seconds"),
    ("ROZTDP_THRESHOLD",      "roztdp threshold",      "Max drift to allow ON"),
    ("SETPOINT_STABLE_HOURS", "Stable hours required", "Min stability before ON"),
    ("MIN_ON_HOLD_MINUTES",   "Min ON hold (min)",     "Keep ON at least N minutes"),
    ("STALE_DATA_MINUTES",    "Stale data limit (min)","Data older than N min → OFF"),
]


def load_env(path: Path) -> dict:
    cfg = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def save_env(path: Path, cfg: dict) -> None:
    # Read existing file preserving comments, update values in-place
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated = set()
    result = []
    for raw in lines:
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, _ = line.partition("=")
            k = k.strip()
            if k in cfg:
                result.append(f"{k}={cfg[k]}")
                updated.add(k)
                continue
        result.append(raw)

    # Append any keys not already in file
    for k, v in cfg.items():
        if k not in updated:
            result.append(f"{k}={v}")

    path.write_text("\n".join(result) + "\n", encoding="utf-8")


class App(tk.Tk):
    def _diagnose(self):
        """Полная диагностика среды, .env, DATA_FILE, порта, зависших процессов."""
        self._log_line("\n=== Диагностика среды ===\n", "EVENT")
        # Проверка наличия psutil
        if psutil is None:
            self._log_line("[ERROR] Модуль psutil не установлен. Установите через pip install psutil\n", "ERROR")
            return
        # Проверка наличия serial.tools.list_ports
        # 1. Проверка .env
        env_path = ENV_FILE
        if not env_path.exists():
            self._log_line(f"[ERROR] .env не найден: {env_path}\n", "ERROR")
            return
        try:
            cfg = load_env(env_path)
        except Exception as exc:
            self._log_line(f"[ERROR] Ошибка чтения .env: {exc}\n", "ERROR")
            return
        self._log_line(f".env загружен: OK\n", "INFO")
        # 2. Проверка DATA_FILE
        data_file = cfg.get("DATA_FILE", "").strip()
        if not data_file:
            self._log_line("[ERROR] DATA_FILE не задан в .env\n", "ERROR")
            return
        if not os.path.isabs(data_file):
            self._log_line(f"[ERROR] DATA_FILE не абсолютный путь: {data_file}\n", "ERROR")
            return
        if not Path(data_file).exists():
            self._log_line(f"[ERROR] DATA_FILE не найден: {data_file}\n", "ERROR")
            return
        try:
            with open(data_file, "r", encoding="utf-8") as fh:
                fh.readline()
        except Exception as exc:
            self._log_line(f"[ERROR] Не удалось прочитать DATA_FILE: {exc}\n", "ERROR")
            return
        self._log_line(f"DATA_FILE читается: OK\n", "INFO")
        # 3. Проверка SERIAL_PORT
        port = cfg.get("SERIAL_PORT", "").strip()
        if not port:
            self._log_line("[ERROR] SERIAL_PORT не задан в .env\n", "ERROR")
            return
        if list_ports is not None:
            if list_ports is not None:
                ports = [p.device for p in list_ports.comports()]
            else:
                ports = []
        else:
            self._log_line("[ERROR] pyserial не установлен корректно. Установите через pip install pyserial\n", "ERROR")
            return
        if port not in ports:
            self._log_line(f"[ERROR] SERIAL_PORT {port} не найден среди доступных: {ports}\n", "ERROR")
            return
        # 4. Проверка зависших процессов
        my_pid = os.getpid()
        found = []
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
            try:
                if proc.info['pid'] == my_pid:
                    continue
                name = (proc.info['name'] or '').lower()
                exe = (proc.info['exe'] or '').lower()
                cmd = ' '.join(proc.info['cmdline'] or []).lower()
                if (
                    'python' in name or 'relaycontroller' in name or
                    'python' in exe or 'relaycontroller' in exe or
                    'python' in cmd or 'relaycontroller' in cmd
                ):
                    found.append(proc.info['pid'])
            except Exception:
                pass
        if found:
            self._log_line(f"[WARNING] Найдены зависшие процессы: {found}\n", "WARNING")
        else:
            self._log_line("Нет зависших процессов: OK\n", "INFO")
        # 5. Проверка открытия порта
        try:
            s = serial.Serial()
            s.port = port
            s.baudrate = 9600
            s.timeout = 1
            s.open()
            s.close()
            self._log_line(f"Порт {port} открыт/закрыт: OK\n", "INFO")
        except Exception as exc:
            self._log_line(f"[ERROR] Не удалось открыть порт {port}: {exc}\n", "ERROR")
            return
        self._log_line("=== Диагностика завершена: ВСЁ ОК, можно запускать! ===\n", "EVENT")
    def __init__(self):
        super().__init__()
        self.title("Relay Controller")
        self.resizable(True, True)
        self.process = None
        self.session_dir = None
        self.session_log_file = None
        self._build_ui()
        self._load_fields()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Left panel ──────────────────────────────────────────────────────
        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="nsew")

        ttk.Label(left, text="Configuration", font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.vars = {}
        for i, (key, label, hint) in enumerate(FIELDS, start=1):
            ttk.Label(left, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self.vars[key] = var
            if key == "DATA_FILE":
                frame = ttk.Frame(left)
                frame.grid(row=i, column=1, sticky="ew", padx=(6, 0), pady=2)
                ttk.Entry(frame, textvariable=var, width=30).pack(side="left", fill="x", expand=True)
                ttk.Button(frame, text="…", width=2,
                           command=self._browse_file).pack(side="left", padx=(2, 0))
            elif key == "SERIAL_PORT":
                frame = ttk.Frame(left)
                frame.grid(row=i, column=1, sticky="ew", padx=(6, 0), pady=2)
                self.port_combo = ttk.Combobox(frame, textvariable=var, width=10)
                self.port_combo.pack(side="left")
                ttk.Button(frame, text="↺", width=2,
                           command=self._refresh_ports).pack(side="left", padx=(3, 0))
                self._refresh_ports()
            else:
                ttk.Entry(left, textvariable=var, width=14).grid(
                    row=i, column=1, sticky="w", padx=(6, 0), pady=2)
            ttk.Label(left, text=hint, foreground="gray").grid(
                row=i, column=2, sticky="w", padx=(8, 0))

        ttk.Button(left, text="Save config", command=self._save_fields).grid(
            row=len(FIELDS)+1, column=0, columnspan=3, sticky="ew", pady=(14, 4))

        # Log folder controls
        self.log_root_var = tk.StringVar(value=str(SCRIPT_DIR / "logs"))
        self.auto_session_var = tk.BooleanVar(value=True)

        log_row = len(FIELDS) + 2
        ttk.Label(left, text="Log folder").grid(row=log_row, column=0, sticky="w", pady=2)
        log_frame = ttk.Frame(left)
        log_frame.grid(row=log_row, column=1, sticky="ew", padx=(6, 0), pady=2)
        ttk.Entry(log_frame, textvariable=self.log_root_var, width=30).pack(side="left", fill="x", expand=True)
        ttk.Button(log_frame, text="…", width=2,
                   command=self._browse_log_folder).pack(side="left", padx=(2, 0))
        ttk.Label(left, text="Where session logs are stored", foreground="gray").grid(
            row=log_row, column=2, sticky="w", padx=(8, 0))

        ttk.Checkbutton(
            left,
            text="Create dated subfolder per start",
            variable=self.auto_session_var
        ).grid(row=log_row + 1, column=0, columnspan=3, sticky="w", pady=(2, 4))


        # ── Buttons ─────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(left, padding=(0, 6))
        btn_frame.grid(row=log_row + 2, column=0, columnspan=3, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)

        self.btn_start = ttk.Button(btn_frame, text="▶  Start",
                        command=self._start, style="Start.TButton")
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.btn_stop = ttk.Button(btn_frame, text="■  Stop",
                       command=self._stop, state="disabled")
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        self.btn_diag = ttk.Button(btn_frame, text="🩺 Диагностика",
                       command=self._diagnose)
        self.btn_diag.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        # Status label
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(left, textvariable=self.status_var, foreground="gray").grid(
            row=log_row + 3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # Style for Start button
        style = ttk.Style(self)
        style.configure("Start.TButton", foreground="green")

        # ── Right panel — log ────────────────────────────────────────────────
        right = ttk.Frame(self, padding=(0, 10, 10, 10))
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=0)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Log output", font=("", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        self.log_box = scrolledtext.ScrolledText(
            right, state="disabled", width=80, height=35,
            font=("Consolas", 9), wrap="word",
            background="#1e1e1e", foreground="#d4d4d4",
            insertbackground="white")
        self.log_box.grid(row=1, column=0, sticky="nsew")

        # Colour tags
        self.log_box.tag_config("INFO",    foreground="#9cdcfe")
        self.log_box.tag_config("ERROR",   foreground="#f44747")
        self.log_box.tag_config("WARNING", foreground="#dcdcaa")
        self.log_box.tag_config("EVENT",   foreground="#4ec9b0")

        # Allow Ctrl+C / Ctrl+A on disabled widget
        self.log_box.bind("<Control-c>", self._copy_log)
        self.log_box.bind("<Control-C>", self._copy_log)
        self.log_box.bind("<Control-a>", self._select_all_log)
        self.log_box.bind("<Control-A>", self._select_all_log)

        log_btn_frame = ttk.Frame(right)
        log_btn_frame.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(log_btn_frame, text="Copy all",  command=self._copy_all_log).pack(side="right", padx=(4, 0))
        ttk.Button(log_btn_frame, text="Save log as...", command=self._save_visible_log).pack(side="right", padx=(4, 0))
        ttk.Button(log_btn_frame, text="Clear log", command=self._clear_log).pack(side="right")

    # --------------------------------------------------------------- ports --
    def _refresh_ports(self):
        if list_ports is not None:
            ports = [p.device for p in list_ports.comports()]
        else:
            self._log_line("[ERROR] pyserial не установлен корректно. Установите через pip install pyserial\n", "ERROR")
            return
        self.port_combo["values"] = ports
        current = self.vars["SERIAL_PORT"].get()
        if not current and ports:
            self.vars["SERIAL_PORT"].set(ports[0])
        if hasattr(self, "log_box"):
            self._log_line(f"Ports found: {', '.join(ports) if ports else 'none'}\n", "EVENT")

    def _safe_log(self, text: str, tag: str = "INFO"):
        self.after(0, self._log_line, text, tag)

    def _reset_and_check_port(self, port: str) -> bool:
        """
        Лёгкая проверка перед запуском:
        1. Убедиться, что COM-порт виден в системе.
        2. Не выполнять open/close здесь: это может дестабилизировать CH340.
        3. Реальное подключение и ретраи делает сам воркер.
        """
        self._safe_log(f"[STEP] Checking and freeing port {port}...\n", "EVENT")
        if list_ports is None:
            self._safe_log("[ERROR] pyserial не установлен корректно. Установите через pip install pyserial\n", "ERROR")
            return False
        ports = [p.device for p in list_ports.comports()]
        if port not in ports:
            self._safe_log(f"[ERROR] SERIAL_PORT {port} не найден среди доступных: {ports}\n", "ERROR")
            return False
        self._safe_log(f"Port {port} detected. Worker will handle connect/retry.\n", "EVENT")
        return True

    # --------------------------------------------------------------- fields --
    def _browse_log_folder(self):
        # Always start in project folder, not Documents
        path = filedialog.askdirectory(
            title="Select folder for logs",
            initialdir=str(SCRIPT_DIR)
        )
        if path:
            self.log_root_var.set(path)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select data file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.vars["DATA_FILE"].set(path)

    def _load_fields(self):
        cfg = load_env(ENV_FILE)
        for key, var in self.vars.items():
            var.set(cfg.get(key, ""))
        # LOG_ROOT_DIR is the stable user-chosen base; LOG_DIR is auto session dir
        if "LOG_ROOT_DIR" in cfg and cfg["LOG_ROOT_DIR"].strip():
            self.log_root_var.set(cfg["LOG_ROOT_DIR"].strip())

    def _save_fields(self, extra_cfg: dict | None = None, silent: bool = False):
        cfg = {k: v for k, v in load_env(ENV_FILE).items()}
        for key, var in self.vars.items():
            cfg[key] = var.get().strip()
        cfg["LOG_ROOT_DIR"] = self.log_root_var.get().strip()
        if extra_cfg:
            cfg.update(extra_cfg)
        save_env(ENV_FILE, cfg)
        if not silent:
            self._log_line("Config saved.\n", "EVENT")

    # ----------------------------------------------------------------- log --
    def _log_line(self, text: str, tag: str = "INFO"):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text, tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")
        if self.session_log_file:
            try:
                with self.session_log_file.open("a", encoding="utf-8") as fh:
                    fh.write(text)
            except Exception:
                pass

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _copy_log(self, event=None):
        try:
            text = self.log_box.selection_get()
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass
        return "break"

    def _select_all_log(self, event=None):
        self.log_box.tag_add("sel", "1.0", "end")
        return "break"

    def _copy_all_log(self):
        text = self.log_box.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log_line("Log copied to clipboard.\n", "EVENT")

    def _save_visible_log(self):
        default_name = f"gui_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path = filedialog.asksaveasfilename(
            title="Save visible log",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not file_path:
            return
        text = self.log_box.get("1.0", "end")
        Path(file_path).write_text(text, encoding="utf-8")
        self._log_line(f"Visible log saved to: {file_path}\n", "EVENT")

    def _pick_tag(self, line: str) -> str:
        if "ERROR" in line:   return "ERROR"
        if "WARNING" in line: return "WARNING"
        if "[EVENT]" in line: return "EVENT"
        return "INFO"

    # ------------------------------------------------------------- process --
    def _prepare_session_dir(self) -> Path:
        # Always derive from LOG_ROOT_DIR — never from a previous session folder
        root_raw = self.log_root_var.get().strip() or str(SCRIPT_DIR / "logs")
        base = Path(root_raw)
        if not base.is_absolute():
            base = (SCRIPT_DIR / base).resolve()
        session = base / datetime.now().strftime("%Y%m%d_%H%M%S") if self.auto_session_var.get() else base
        session.mkdir(parents=True, exist_ok=True)
        self.session_dir = session
        self.session_log_file = session / "gui_live_log.txt"
        return session

    def _start(self):
        # Автодиагностика перед стартом
        import psutil
        from pathlib import Path

        # 0. Убить предыдущий воркер, если ещё работает
        if self.process and self.process.poll() is None:
            self._log_line("Stopping previous worker...\n", "WARNING")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None

        # 1. Проверка .env
        env_path = ENV_FILE
        if not env_path.exists():
            self._log_line(f"[ERROR] .env не найден: {env_path}\n", "ERROR")
            return
        try:
            cfg = load_env(env_path)
        except Exception as exc:
            self._log_line(f"[ERROR] Ошибка чтения .env: {exc}\n", "ERROR")
            return
        # 2. Проверка DATA_FILE
        data_file = cfg.get("DATA_FILE", "").strip()
        if not data_file:
            self._log_line("[ERROR] DATA_FILE не задан в .env\n", "ERROR")
            return
        if not os.path.isabs(data_file):
            self._log_line(f"[ERROR] DATA_FILE не абсолютный путь: {data_file}\n", "ERROR")
            return
        if not Path(data_file).exists():
            self._log_line(f"[ERROR] DATA_FILE не найден: {data_file}\n", "ERROR")
            return
        try:
            with open(data_file, "r", encoding="utf-8") as fh:
                fh.readline()
        except Exception as exc:
            self._log_line(f"[ERROR] Не удалось прочитать DATA_FILE: {exc}\n", "ERROR")
            return
        # 3. Проверка SERIAL_PORT
        port = cfg.get("SERIAL_PORT", "").strip()
        if not port:
            self._log_line("[ERROR] SERIAL_PORT не задан в .env\n", "ERROR")
            return
        if list_ports is not None:
            ports = [p.device for p in list_ports.comports()]
        else:
            self._log_line("[ERROR] pyserial не установлен корректно. Установите через pip install pyserial\n", "ERROR")
            return
        if port not in ports:
            self._log_line(f"[ERROR] SERIAL_PORT {port} не найден среди доступных: {ports}\n", "ERROR")
            return
        # 4. Проверка зависших процессов (только info, kill делает _reset_and_check_port)
        my_pid = os.getpid()
        found = []
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
            try:
                if proc.info['pid'] == my_pid:
                    continue
                name = (proc.info['name'] or '').lower()
                exe = (proc.info['exe'] or '').lower()
                cmd = ' '.join(proc.info['cmdline'] or []).lower()
                if (
                    'python' in name or 'relaycontroller' in name or
                    'python' in exe or 'relaycontroller' in exe or
                    'python' in cmd or 'relaycontroller' in cmd
                ):
                    found.append(proc.info['pid'])
            except Exception:
                pass
        if found:
            self._log_line(f"[WARNING] Найдены зависшие процессы: {found}\n", "WARNING")
        # Порт НЕ открываем здесь — это делает _reset_and_check_port
        # (двойное open/close конфликтует с CH340 драйвером)
        session_dir = self._prepare_session_dir()
        self._save_fields({"LOG_DIR": str(session_dir)}, silent=True)
        self._log_line(f"Session logs: {session_dir}\n", "EVENT")
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Starting...")
        threading.Thread(target=self._start_after_reset, args=(port,), daemon=True).start()

    def _start_after_reset(self, port: str):
        # 1. Гарантированно освободить порт и убедиться, что он свободен
        ok = True
        if port:
            ok = self._reset_and_check_port(port)
        if ok:
            self.after(0, self._launch_process)
        else:
            self._safe_log("[ERROR] Порт не готов, запуск отменён.\n", "ERROR")
            self.after(0, self._set_stopped)

    def _launch_process(self):
        self._log_line("=== Starting script ===\n", "EVENT")
        try:
            if getattr(sys, "frozen", False):
                # Single-exe mode: start this same exe as worker process.
                cmd = [sys.executable, "--worker"]
            else:
                cmd = [sys.executable, str(SCRIPT)]
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(SCRIPT_DIR),
                env=env,
            )
        except Exception as exc:
            self._log_line(f"Failed to start: {exc}\n", "ERROR")
            self._set_stopped()
            return

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.status_var.set("Running")

        thread = threading.Thread(target=self._read_output, daemon=True)
        thread.start()

    def _stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self._set_stopped()

    def _set_stopped(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Stopped")

    def _read_output(self):
        # Читаем stdout воркера, если есть ошибки — выводим их явно
        lines = []
        try:
            if self.process and self.process.stdout:
                for line in self.process.stdout:
                    lines.append(line)
                    tag = self._pick_tag(line)
                    self.after(0, self._log_line, line, tag)
        except Exception:
            pass
        finally:
            # Если воркер завершился слишком быстро и не вывел ни одной ошибки — явно показать это
            error_lines = [l for l in lines if "[ERROR]" in l or "SESSION ABORTED" in l]
            if not lines:
                self.after(0, self._log_line, "[ERROR] Worker exited with no output.\n", "ERROR")
            elif error_lines:
                for l in error_lines:
                    self.after(0, self._log_line, l, "ERROR")
            self.after(0, self._set_stopped)
            self.after(0, self._log_line, "=== Script exited ===\n", "EVENT")


if __name__ == "__main__":
    if "--worker" in sys.argv:
        # Worker subprocess: stdout/stderr are the Popen pipe — just run the script.
        from pc_relay_script import main as worker_main
        worker_main()
    else:
        # GUI mode: hide the console window that --console subsystem creates.
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass
        app = App()
        app.mainloop()
