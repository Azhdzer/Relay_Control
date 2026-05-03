#!/usr/bin/env python3
"""
Relay Controller GUI  —  modern dark theme
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

if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
    ENV_FILE   = SCRIPT_DIR / ".env"
    SCRIPT     = SCRIPT_DIR / "pc_relay_script.py"

# ── Colour palette ────────────────────────────────────────────────────────────
C = {
    "bg":        "#1a1a2e",
    "panel":     "#16213e",
    "card":      "#0f3460",
    "border":    "#1f4068",
    "accent":    "#e94560",
    "green":     "#00b894",
    "yellow":    "#fdcb6e",
    "text":      "#eaeaea",
    "muted":     "#7f8c8d",
    "entry_bg":  "#0d2137",
    "entry_fg":  "#eaeaea",
    "log_bg":    "#0d0d1a",
    "btn_start": "#00b894",
    "btn_stop":  "#e94560",
    "btn_diag":  "#636e72",
}
FONT_BODY  = ("Segoe UI", 9)
FONT_BOLD  = ("Segoe UI", 9,  "bold")
FONT_HEAD  = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 8)
FONT_MONO  = ("Cascadia Code", 9) if sys.platform == "win32" else ("Consolas", 9)


def _flat_button(parent, text, command, bg, fg="#ffffff",
                 padx=14, pady=5, font=FONT_BOLD):
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
        font=font, relief="flat", bd=0,
        padx=padx, pady=pady, cursor="hand2",
    )


def _section_label(parent, text, bg=None):
    bg = bg or C["panel"]
    f = tk.Frame(parent, bg=bg)
    tk.Label(f, text=text, font=FONT_BOLD,
             bg=bg, fg=C["accent"]).pack(side="left")
    tk.Frame(f, bg=C["border"], height=1).pack(
        side="left", fill="x", expand=True, padx=(8, 0), pady=(4, 0))
    return f


# Fields to show in GUI (key, label, description)
FIELDS = [
    ("DATA_FILE",             "Data file",             "Path to OdczytTH .txt"),
    ("SERIAL_PORT",           "Serial port",           "e.g. COM6"),
    ("BAUD_RATE",             "Baud rate",             "Must match Arduino (9600)"),
    ("POLL_INTERVAL",         "Poll interval (s)",     "How often to check, seconds"),
    ("ROZTDP_THRESHOLD",      "roztdp threshold",      "Fallback max drift"),
    ("SETPOINT_STABLE_HOURS", "Stable hours required", "Min stability before ON"),
    ("MIN_ON_HOLD_MINUTES",   "Min ON hold (min)",     "Keep ON at least N minutes"),
    ("STALE_DATA_MINUTES",    "Stale data limit (min)","File not updated N min → OFF"),
]

THRESHOLD_FIELDS = [
    ("ROZTDP_T10_RH_MID", "T≈10°C, RH 40–75",          "Mid humidity at 10°C"),
    ("ROZTDP_T10_RH_EXT", "T≈10°C, RH 22–40 / 75–95", "Ext humidity at 10°C"),
    ("ROZTDP_T23_RH_MID", "T≈23°C, RH 30–75",          "Mid humidity at 23°C"),
    ("ROZTDP_T23_RH_EXT", "T≈23°C, RH 10–30 / 75–95", "Ext humidity at 23°C"),
    ("ROZTDP_T35_RH_MID", "T≈35°C, RH 40–75",          "Mid humidity at 35°C"),
    ("ROZTDP_T35_RH_EXT", "T≈35°C, RH 10–40 / 75–95", "Ext humidity at 35°C"),
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
        self.minsize(1150, 720)
        self.geometry("1420x860")
        self.configure(bg=C["bg"])
        self.process = None
        self.session_dir = None
        self.session_log_file = None
        self._apply_theme()
        self._build_ui()
        self._load_fields()

    def _apply_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        # Base widget colours
        style.configure(".",
            background=C["panel"], foreground=C["text"],
            fieldbackground=C["entry_bg"], font=FONT_BODY,
            bordercolor=C["border"], troughcolor=C["entry_bg"],
            selectbackground=C["card"], selectforeground=C["text"],
        )
        style.configure("TFrame",        background=C["panel"])
        style.configure("TLabel",        background=C["panel"], foreground=C["text"], font=FONT_BODY)
        style.configure("Muted.TLabel",  background=C["panel"], foreground=C["muted"], font=FONT_SMALL)
        style.configure("Head.TLabel",   background=C["panel"], foreground=C["text"],  font=FONT_HEAD)
        style.configure("TEntry",
            fieldbackground=C["entry_bg"], foreground=C["entry_fg"],
            bordercolor=C["border"], insertcolor=C["text"], relief="flat",
        )
        style.configure("TCombobox",
            fieldbackground=C["entry_bg"], foreground=C["entry_fg"],
            selectbackground=C["card"], selectforeground=C["text"],
            arrowcolor=C["muted"],
        )
        style.map("TCombobox",
            fieldbackground=[("readonly", C["entry_bg"])],
            foreground=[("readonly", C["entry_fg"])],
        )
        style.configure("TCheckbutton",
            background=C["panel"], foreground=C["text"], font=FONT_BODY,
        )
        style.map("TCheckbutton",
            background=[("active", C["panel"])],
            foreground=[("active", C["accent"])],
        )
        style.configure("Card.TFrame",   background=C["card"])
        style.configure("TScrollbar",    background=C["border"], troughcolor=C["entry_bg"],
                        arrowcolor=C["muted"], bordercolor=C["panel"])
        # option_add for Combobox dropdown
        self.option_add("*TCombobox*Listbox.background",  C["entry_bg"])
        self.option_add("*TCombobox*Listbox.foreground",  C["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["card"])
        self.option_add("*TCombobox*Listbox.font", FONT_BODY)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        self.columnconfigure(0, weight=0, minsize=450)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        # ── Left panel (scrollable canvas) ──────────────────────────────────
        left_outer = tk.Frame(self, bg=C["panel"], width=540)
        left_outer.grid(row=0, column=0, sticky="nsew")
        left_outer.grid_propagate(False)
        left_outer.rowconfigure(0, weight=1)
        left_outer.columnconfigure(0, weight=1)

        canvas = tk.Canvas(left_outer, bg=C["panel"], highlightthickness=0, bd=0)
        # Custom flat scrollbar drawn on a Canvas
        vsb_canvas = tk.Canvas(left_outer, bg=C["panel"],
                               width=6, highlightthickness=0, bd=0)
        vsb_canvas.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def _vsb_set(first, last):
            first, last = float(first), float(last)
            h = vsb_canvas.winfo_height()
            y0 = int(first * h)
            y1 = max(y0 + 20, int(last * h))
            vsb_canvas.delete("thumb")
            vsb_canvas.create_rectangle(
                1, y0, 5, y1,
                fill=C["border"], outline="", tags="thumb")

        def _vsb_click(event):
            h = vsb_canvas.winfo_height()
            frac = event.y / h if h else 0
            canvas.yview_moveto(frac)

        def _vsb_drag(event):
            h = vsb_canvas.winfo_height()
            frac = event.y / h if h else 0
            canvas.yview_moveto(frac)

        vsb_canvas.bind("<Button-1>", _vsb_click)
        vsb_canvas.bind("<B1-Motion>", _vsb_drag)
        canvas.configure(yscrollcommand=_vsb_set)

        left = tk.Frame(canvas, bg=C["panel"], padx=16, pady=14)
        win_id = canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(win_id, width=e.width)
        left.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))

        # App title
        tk.Label(left, text="⚡  Relay Controller",
                 font=("Segoe UI", 13, "bold"),
                 bg=C["panel"], fg=C["accent"]).pack(anchor="w", pady=(0, 12))

        # ── Section: General config ──────────────────────────────────────────
        _section_label(left, "GENERAL CONFIG").pack(fill="x", pady=(0, 6))

        config_frame = tk.Frame(left, bg=C["panel"])
        config_frame.pack(fill="x")
        config_frame.columnconfigure(1, weight=1)
        config_frame.columnconfigure(2, weight=0)

        self.vars = {}
        for i, (key, label, hint) in enumerate(FIELDS):
            tk.Label(config_frame, text=label, font=FONT_BODY,
                     bg=C["panel"], fg=C["text"], anchor="w").grid(
                row=i, column=0, sticky="w", pady=3, padx=(0, 8))
            var = tk.StringVar()
            self.vars[key] = var
            if key == "DATA_FILE":
                row_f = tk.Frame(config_frame, bg=C["panel"])
                row_f.grid(row=i, column=1, sticky="ew", pady=3)
                row_f.columnconfigure(0, weight=1)
                tk.Entry(row_f, textvariable=var,
                         bg=C["entry_bg"], fg=C["entry_fg"], relief="flat",
                         insertbackground=C["text"], font=FONT_BODY).grid(
                    row=0, column=0, sticky="ew", ipady=4)
                tk.Button(row_f, text="…", command=self._browse_file,
                          bg=C["card"], fg=C["text"], relief="flat",
                          font=FONT_BODY, padx=6, cursor="hand2").grid(
                    row=0, column=1, padx=(3, 0))
            elif key == "SERIAL_PORT":
                row_f = tk.Frame(config_frame, bg=C["panel"])
                row_f.grid(row=i, column=1, columnspan=2, sticky="ew", pady=3)
                row_f.columnconfigure(0, weight=1)
                self.port_combo = ttk.Combobox(row_f, textvariable=var, width=14)
                self.port_combo.grid(row=0, column=0, sticky="ew", ipady=2)
                tk.Button(row_f, text="↺  Refresh ports",
                          command=self._refresh_ports,
                          bg=C["accent"], fg="#ffffff", relief="flat",
                          font=FONT_SMALL, padx=12, pady=4,
                          cursor="hand2", activebackground=C["accent"]
                          ).grid(row=0, column=1, padx=(10, 0))
                self._refresh_ports()
            else:
                tk.Entry(config_frame, textvariable=var, width=12,
                         bg=C["entry_bg"], fg=C["entry_fg"], relief="flat",
                         insertbackground=C["text"], font=FONT_BODY).grid(
                    row=i, column=1, sticky="w", pady=3, ipady=4)
            if key != "SERIAL_PORT" and hint:
                tk.Label(config_frame, text=hint, font=FONT_SMALL,
                         bg=C["panel"], fg=C["muted"]).grid(
                    row=i, column=2, sticky="w", padx=(8, 0))

        # ── Section: Log folder ──────────────────────────────────────────────
        _section_label(left, "LOG FOLDER").pack(fill="x", pady=(14, 6))

        self.log_root_var    = tk.StringVar(value=str(SCRIPT_DIR / "logs"))
        self.auto_session_var = tk.BooleanVar(value=True)

        log_row_f = tk.Frame(left, bg=C["panel"])
        log_row_f.pack(fill="x")
        log_row_f.columnconfigure(0, weight=1)
        tk.Entry(log_row_f, textvariable=self.log_root_var,
                 bg=C["entry_bg"], fg=C["entry_fg"], relief="flat",
                 insertbackground=C["text"], font=FONT_BODY).grid(
            row=0, column=0, sticky="ew", ipady=4)
        tk.Button(log_row_f, text="…", command=self._browse_log_folder,
                  bg=C["card"], fg=C["text"], relief="flat",
                  font=FONT_BODY, padx=6, cursor="hand2").grid(
            row=0, column=1, padx=(3, 0))

        ttk.Checkbutton(left, text="Create dated subfolder per session",
                        variable=self.auto_session_var).pack(anchor="w", pady=(4, 0))

        # ── Section: Dew-point thresholds ────────────────────────────────────
        _section_label(left, "PROGI t_dp  [°C]").pack(fill="x", pady=(14, 6))

        tdp_card = tk.Frame(left, bg=C["card"], padx=10, pady=8)
        tdp_card.pack(fill="x")

        self.threshold_vars = {}
        _tdp_defaults = {
            "ROZTDP_T10_RH_MID": "0.20", "ROZTDP_T10_RH_EXT": "0.45",
            "ROZTDP_T23_RH_MID": "0.20", "ROZTDP_T23_RH_EXT": "0.45",
            "ROZTDP_T35_RH_MID": "0.20", "ROZTDP_T35_RH_EXT": "0.45",
        }
        for key, label, hint in THRESHOLD_FIELDS:
            row_c = tk.Frame(tdp_card, bg=C["card"])
            row_c.pack(fill="x", pady=2)
            tk.Label(row_c, text=label, width=26, anchor="w",
                     bg=C["card"], fg=C["text"], font=FONT_BODY).pack(side="left")
            var = tk.StringVar(value=_tdp_defaults[key])
            self.threshold_vars[key] = var
            tk.Entry(row_c, textvariable=var, width=7,
                     bg=C["entry_bg"], fg=C["entry_fg"], relief="flat",
                     insertbackground=C["text"], font=FONT_BODY).pack(
                side="left", ipady=3, padx=(0, 8))
            tk.Label(row_c, text=hint,
                     bg=C["card"], fg=C["muted"], font=FONT_SMALL).pack(side="left")

        # ── Save button ──────────────────────────────────────────────────────
        _flat_button(left, "💾  Save config", self._save_fields,
                     bg=C["card"], fg=C["text"], pady=7).pack(
            fill="x", pady=(14, 0))

        # ── Control buttons ──────────────────────────────────────────────────
        _section_label(left, "CONTROL").pack(fill="x", pady=(14, 8))

        btn_row = tk.Frame(left, bg=C["panel"])
        btn_row.pack(fill="x")
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)
        btn_row.columnconfigure(2, weight=1)

        self.btn_start = _flat_button(btn_row, "▶  Start", self._start,
                                      bg=C["btn_start"], pady=8)
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.btn_stop = _flat_button(btn_row, "■  Stop", self._stop,
                                     bg=C["border"], fg=C["muted"], pady=8)
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        self.btn_stop.config(state="disabled",
                             activebackground=C["border"])

        self.btn_diag = _flat_button(btn_row, "🩺  Diagnose", self._diagnose,
                                     bg=C["btn_diag"], pady=8)
        self.btn_diag.grid(row=0, column=2, sticky="ew")

        # ── Status pill ──────────────────────────────────────────────────────
        status_row = tk.Frame(left, bg=C["panel"])
        status_row.pack(fill="x", pady=(10, 4))
        self._dot = tk.Label(status_row, text="●", font=("Segoe UI", 11),
                             bg=C["panel"], fg=C["muted"])
        self._dot.pack(side="left")
        self.status_var = tk.StringVar(value="Disconnected")
        tk.Label(status_row, textvariable=self.status_var,
                 font=FONT_BODY, bg=C["panel"], fg=C["muted"]).pack(
            side="left", padx=(4, 0))

        # ── Right panel — log ────────────────────────────────────────────────
        right_outer = tk.Frame(self, bg=C["bg"])
        right_outer.grid(row=0, column=1, sticky="nsew", padx=(1, 0))
        right_outer.rowconfigure(1, weight=1)
        right_outer.columnconfigure(0, weight=1)

        log_header = tk.Frame(right_outer, bg=C["bg"], pady=10, padx=12)
        log_header.grid(row=0, column=0, sticky="ew")
        tk.Label(log_header, text="LOG OUTPUT", font=FONT_BOLD,
                 bg=C["bg"], fg=C["accent"]).pack(side="left")

        # Custom Text + flat Canvas scrollbar (no system scrollbar)
        log_container = tk.Frame(right_outer, bg=C["log_bg"])
        log_container.grid(row=1, column=0, sticky="nsew")
        log_container.rowconfigure(0, weight=1)
        log_container.columnconfigure(0, weight=1)

        self.log_box = tk.Text(
            log_container, state="normal",
            font=FONT_MONO, wrap="word",
            background=C["log_bg"], foreground="#cdd6f4",
            insertbackground=C["text"],
            borderwidth=0, relief="flat",
            padx=12, pady=8,
            selectbackground=C["card"],
        )
        self.log_box.grid(row=0, column=0, sticky="nsew")

        # Flat scrollbar for log
        log_vsb = tk.Canvas(log_container, bg=C["log_bg"],
                            width=6, highlightthickness=0, bd=0)
        log_vsb.grid(row=0, column=1, sticky="ns")

        def _log_vsb_set(first, last):
            first, last = float(first), float(last)
            h = log_vsb.winfo_height()
            y0 = int(first * h)
            y1 = max(y0 + 24, int(last * h))
            log_vsb.delete("thumb")
            log_vsb.create_rectangle(
                1, y0, 5, y1,
                fill=C["border"], outline="", tags="thumb")

        def _log_vsb_click(event):
            h = log_vsb.winfo_height()
            frac = event.y / h if h else 0
            self.log_box.yview_moveto(frac)

        def _log_vsb_drag(event):
            h = log_vsb.winfo_height()
            frac = event.y / h if h else 0
            self.log_box.yview_moveto(frac)

        log_vsb.bind("<Button-1>", _log_vsb_click)
        log_vsb.bind("<B1-Motion>", _log_vsb_drag)
        self.log_box.configure(yscrollcommand=_log_vsb_set)

        self.log_box.tag_config("INFO",    foreground="#89dceb")
        self.log_box.tag_config("ERROR",   foreground="#f38ba8")
        self.log_box.tag_config("WARNING", foreground="#f9e2af")
        self.log_box.tag_config("EVENT",   foreground="#a6e3a1")

        self.log_box.bind("<Control-c>", self._copy_log)
        self.log_box.bind("<Control-C>", self._copy_log)
        self.log_box.bind("<Control-a>", self._select_all_log)
        self.log_box.bind("<Control-A>", self._select_all_log)
        # Block keyboard edits but allow selection/copy shortcuts
        def _block_edit(e):
            if e.state & 0x4:  # Ctrl held — allow Ctrl+C, Ctrl+A, etc.
                return None
            if e.keysym in ("Up", "Down", "Left", "Right",
                            "Home", "End", "Prior", "Next"):
                return None
            return "break"
        self.log_box.bind("<Key>", _block_edit)

        log_toolbar = tk.Frame(right_outer, bg=C["bg"], pady=6, padx=8)
        log_toolbar.grid(row=2, column=0, sticky="ew")
        for txt, cmd in [
            ("Copy all",  self._copy_all_log),
            ("Save log…", self._save_visible_log),
            ("Clear",     self._clear_log),
        ]:
            _flat_button(log_toolbar, txt, cmd,
                         bg=C["card"], fg=C["text"],
                         padx=10, pady=4, font=FONT_SMALL).pack(
                side="right", padx=(4, 0))

        # ── Bottom status bar ────────────────────────────────────────────────
        bar = tk.Frame(self, bg=C["border"], height=24)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._bar_label = tk.Label(
            bar, text="  Relay Controller  ·  disconnected",
            font=FONT_SMALL, bg=C["border"], fg=C["muted"], anchor="w")
        self._bar_label.pack(side="left", padx=8)
        self._bar_time = tk.Label(
            bar, text="", font=FONT_SMALL,
            bg=C["border"], fg=C["muted"], anchor="e")
        self._bar_time.pack(side="right", padx=8)
        self._tick_clock()

    def _tick_clock(self):
        self._bar_time.config(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S  "))
        self.after(1000, self._tick_clock)

    def _set_status(self, text: str, color: str = None):
        self.status_var.set(text)
        col = color or C["muted"]
        self._dot.config(fg=col)
        for w in (self._dot,):
            w.config(fg=col)
        self._bar_label.config(text=f"  Relay Controller  ·  {text}")

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
        # Load per-range t_dp thresholds from .env
        for key, var in self.threshold_vars.items():
            if cfg.get(key, "").strip():
                var.set(cfg[key].strip())

    def _save_fields(self, extra_cfg: dict | None = None, silent: bool = False):
        cfg = {k: v for k, v in load_env(ENV_FILE).items()}
        for key, var in self.vars.items():
            cfg[key] = var.get().strip()
        cfg["LOG_ROOT_DIR"] = self.log_root_var.get().strip()
        # Save per-range t_dp thresholds
        for key, var in self.threshold_vars.items():
            val = var.get().strip()
            if val:
                cfg[key] = val
        if extra_cfg:
            cfg.update(extra_cfg)
        save_env(ENV_FILE, cfg)
        if not silent:
            self._log_line("Config saved.\n", "EVENT")

    # ----------------------------------------------------------------- log --
    def _log_line(self, text: str, tag: str = "INFO"):
        self.log_box.insert("end", text, tag)
        self.log_box.see("end")
        if self.session_log_file:
            try:
                with self.session_log_file.open("a", encoding="utf-8") as fh:
                    fh.write(text)
            except Exception:
                pass

    def _clear_log(self):
        self.log_box.delete("1.0", "end")

    def _copy_log(self, event=None):
        try:
            text = self.log_box.selection_get()
        except tk.TclError:
            text = self.log_box.get("1.0", "end")  # nothing selected → copy all
        self.clipboard_clear()
        self.clipboard_append(text)
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
        self.btn_start.config(state="disabled",
                              bg=C["border"], fg=C["muted"],
                              activebackground=C["border"])
        self.btn_stop.config(state="disabled")
        self._set_status("Starting…", C["yellow"])
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

        self.btn_start.config(state="disabled",
                              bg=C["border"], fg=C["muted"],
                              activebackground=C["border"])
        self.btn_stop.config(state="normal",
                             bg=C["btn_stop"], fg="#ffffff",
                             activebackground=C["btn_stop"])
        self._set_status("Running", C["green"])

        thread = threading.Thread(target=self._read_output, daemon=True)
        thread.start()

    def _stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self._set_stopped()

    def _set_stopped(self):
        self.btn_start.config(state="normal",
                              bg=C["btn_start"], fg="#ffffff",
                              activebackground=C["btn_start"])
        self.btn_stop.config(state="disabled",
                             bg=C["border"], fg=C["muted"],
                             activebackground=C["border"])
        self._set_status("Disconnected", C["muted"])

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
