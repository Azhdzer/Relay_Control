#!/usr/bin/env python3
"""
Relay controller for OdczytTH measurement file.

Usage:  python pc_relay_script.py
Config: .env file in the same folder as this script

Relay closes (ON) only when ALL conditions met:
  1. roztdp(15min) is numeric AND < ROZTDP_THRESHOLD
  2. Current Tzadana+RHzadana setpoint has been continuously stable
     for more than SETPOINT_STABLE_HOURS

Fail-safe: any error (missing file, stale data, parse error, serial loss)
immediately results in relay OFF.

Two log outputs:
  - console / rotating debug log  : every poll cycle
  - relay_events.log              : only ON/OFF transitions + running totals
"""

import os
import sys
import serial
import time
import logging
import logging.handlers
from datetime import datetime, timedelta
from pathlib import Path

# On some Windows setups (especially in frozen GUI subprocesses),
# default stdout/stderr encoding may be cp1252 and crash on Cyrillic text.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# -- Locate .env next to this script ------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()

# When frozen as .exe (PyInstaller), __file__ points inside _MEIPASS temp dir.
# Use the executable folder so external .env remains editable.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()


# -- .env loader (no external deps) -------------------------------------------
def load_env(env_path: Path) -> dict:
    if not env_path.exists():
        msg = f"[ERROR] .env not found: {env_path}\n        Copy .env to the same folder as this script and fill it in."
        print(msg, file=sys.stderr)
        # Also log to relay_events.log if possible
        try:
            with open(SCRIPT_DIR / "relay_events.log", "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except Exception:
            pass
        sys.exit(1)
    cfg = {}
    with env_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


cfg = load_env(SCRIPT_DIR / ".env")

DATA_FILE             = cfg.get("DATA_FILE", "")
SERIAL_PORT           = cfg.get("SERIAL_PORT", "COM3")
BAUD_RATE             = int(cfg.get("BAUD_RATE", "9600"))
POLL_INTERVAL         = int(cfg.get("POLL_INTERVAL", "30"))
ROZTDP_THRESHOLD      = float(cfg.get("ROZTDP_THRESHOLD", "0.35"))
SETPOINT_STABLE_HOURS = float(cfg.get("SETPOINT_STABLE_HOURS", "2.0"))
STALE_DATA_MINUTES    = int(cfg.get("STALE_DATA_MINUTES", "5"))
MIN_ON_HOLD_MINUTES   = int(cfg.get("MIN_ON_HOLD_MINUTES", "15"))
LOG_DIR               = Path(cfg.get("LOG_DIR", "."))
if not LOG_DIR.is_absolute():
    LOG_DIR = SCRIPT_DIR / LOG_DIR
LOG_DIR.mkdir(parents=True, exist_ok=True)


# -- Logging setup ------------------------------------------------------------
class _FlushStreamHandler(logging.StreamHandler):
    """StreamHandler that flushes after every emit — critical for subprocess pipes."""
    def emit(self, record):
        super().emit(record)
        self.flush()


def _setup_logging() -> logging.Logger:
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    logger = logging.getLogger("relay")
    logger.setLevel(logging.DEBUG)

    ch = _FlushStreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "relay_debug.log",
        when="midnight", backupCount=7, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


log = _setup_logging()
EVENTS_LOG = LOG_DIR / "relay_events.log"
STATE_LOG = LOG_DIR / "relay_state.log"
RUNNER_VERSION = "2026-04-26-hotfix-serial-v2"


def log_event(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("[EVENT] %s", msg)
    with EVENTS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{ts}  {msg}\n")


def log_state(poll_n: int, cmd: str, tdp_text: str, reason: str, on_for: str = "-") -> None:
    """Per-poll state log: always includes relay state and current tdp value."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with STATE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(
            f"{ts}  poll#{poll_n}  RELAY={cmd}  on_for={on_for}  "
            f"tdp={tdp_text}  reason={reason}\n"
        )


# -- File helpers -------------------------------------------------------------
def read_file(path: str) -> list:
    for enc in ("utf-8-sig", "windows-1250", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.readlines()
        except UnicodeDecodeError:
            continue
    raise IOError(f"Cannot decode file: {path}")


def parse_rows(lines: list) -> tuple:
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Data Czas;"):
            header_idx = i
            break
    if header_idx is None:
        return [], "header row 'Data Czas;' not found"

    headers = lines[header_idx].rstrip(";\r\n").split(";")
    rows = []
    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(";")
        if len(parts) < len(headers):
            parts += [""] * (len(headers) - len(parts))
        row = dict(zip(headers, parts))
        try:
            row["_dt"] = datetime.strptime(row["Data Czas"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            continue
        rows.append(row)
    return rows, None


# -- Core evaluation ----------------------------------------------------------
def evaluate(rows: list) -> tuple:
    if not rows:
        return False, "no valid data rows", {}

    latest = rows[-1]

    age = datetime.now() - latest["_dt"]
    if age > timedelta(minutes=STALE_DATA_MINUTES):
        mins = int(age.total_seconds() // 60)
        return False, f"data stale ({mins} min old, limit {STALE_DATA_MINUTES} min)", {}

    raw = latest.get("roztdp(15min)", "brak").strip()
    if raw.lower() in ("brak", ""):
        return False, "roztdp(15min)=brak (15-min window not yet ready)", {}
    try:
        roztdp = float(raw.replace(",", "."))
    except ValueError:
        return False, f"roztdp(15min) not numeric: {raw!r}", {}

    if roztdp >= ROZTDP_THRESHOLD:
        return False, f"roztdp={roztdp:.3f} >= threshold {ROZTDP_THRESHOLD}", \
               {"roztdp": roztdp}

    try:
        tz_now = latest["Tzadana"].strip()
        rh_now = latest["RHzadana"].strip()
    except KeyError:
        return False, "Tzadana or RHzadana column missing", {}

    setpoint_start_dt = latest["_dt"]
    for row in reversed(rows):
        if row["Tzadana"].strip() == tz_now and row["RHzadana"].strip() == rh_now:
            setpoint_start_dt = row["_dt"]
        else:
            break

    stable   = latest["_dt"] - setpoint_start_dt
    required = timedelta(hours=SETPOINT_STABLE_HOURS)
    stable_min = int(stable.total_seconds() // 60)

    if stable <= required:
        remaining = required - stable
        h, rem = divmod(int(remaining.total_seconds()), 3600)
        m = rem // 60
        return False, (
            f"setpoint T={tz_now} RH={rh_now} stable {stable_min} min "
            f"(need {int(required.total_seconds()//60)} min, {h}h {m:02d}m left)"
        ), {"roztdp": roztdp, "stable_min": stable_min,
            "Tzadana": tz_now, "RHzadana": rh_now}

    return True, (
        f"roztdp={roztdp:.3f} < {ROZTDP_THRESHOLD}  |  "
        f"T={tz_now} RH={rh_now} stable {stable_min} min"
    ), {"roztdp": roztdp, "stable_min": stable_min,
        "Tzadana": tz_now, "RHzadana": rh_now}


# -- Statistics tracker -------------------------------------------------------
class RelayStats:
    def __init__(self):
        self._on_since = None
        self._total_on = timedelta()
        self._cycle_count = 0

    def transition_on(self, reason: str, extra: dict) -> None:
        self._on_since = datetime.now()
        self._cycle_count += 1
        roztdp = extra.get("roztdp", "?")
        stable = extra.get("stable_min", "?")
        tz     = extra.get("Tzadana", "?")
        rh     = extra.get("RHzadana", "?")
        log_event(
            f"ON   #{self._cycle_count:03d}  "
            f"T={tz} RH={rh}  stable={stable}min  roztdp={roztdp}"
        )

    def transition_off(self, reason: str) -> None:
        if self._on_since is not None:
            duration = datetime.now() - self._on_since
            self._total_on += duration
            dur_s  = int(duration.total_seconds())
            tot_s  = int(self._total_on.total_seconds())
            dur_str = f"{dur_s//3600}h {(dur_s%3600)//60:02d}m {dur_s%60:02d}s"
            tot_str = f"{tot_s//3600}h {(tot_s%3600)//60:02d}m"
            log_event(
                f"OFF  #{self._cycle_count:03d}  "
                f"duration={dur_str}  total_ON_session={tot_str}  | {reason}"
            )
            self._on_since = None
        else:
            log_event(f"OFF  (was already OFF)  | {reason}")

    def on_duration_str(self) -> str:
        if self._on_since is None:
            return "-"
        secs = int((datetime.now() - self._on_since).total_seconds())
        return f"{secs//3600}h {(secs%3600)//60:02d}m {secs%60:02d}s"


# -- Serial helpers -----------------------------------------------------------
def open_serial(retries: int = 1, retry_delay: float = 3.0):
    for attempt in range(1, retries + 1):
        try:
            ser = serial.Serial()
            ser.port     = SERIAL_PORT
            ser.baudrate = BAUD_RATE
            ser.timeout  = 2
            ser.dtr      = False   # do not reset Arduino / avoid CH340 hang
            ser.rts      = False
            ser.open()
            time.sleep(1.5)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            log.info("Serial opened: %s @ %d baud", SERIAL_PORT, BAUD_RATE)
            return ser
        except Exception as exc:
            if attempt < retries:
                log.warning("Serial open attempt %d/%d failed: %s - retrying in %.0fs",
                            attempt, retries, exc, retry_delay)
                time.sleep(retry_delay)
            else:
                log.error("Cannot open serial port %s: %s", SERIAL_PORT, exc)
                return None


def close_serial(ser) -> None:
    """Gracefully close CH340 port without leaving it in error 31 state."""
    if ser is None:
        return
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.dtr = False
        ser.rts = False
        time.sleep(0.5)   # CH340 needs time to release lines
        ser.close()
        time.sleep(1.0)   # Windows driver needs time after close before reopen
    except Exception:
        pass


def send_command(ser, cmd: str) -> bool:
    try:
        ser.write((cmd + "\n").encode("ascii"))
        return True
    except serial.SerialException as exc:
        log.error("Serial write failed: %s", exc)
        return False


def is_hard_error_reason(reason: str) -> bool:
    """Reasons that must always force OFF (fail-safe)."""
    text = (reason or "").lower()
    hard_markers = (
        "exception:",
        "file not found",
        "data_file not set",
        "data stale",
        "serial unavailable",
        "header row",
        "cannot decode",
        "column missing",
        "no valid data rows",
    )
    return any(marker in text for marker in hard_markers)


# -- Main loop ----------------------------------------------------------------
def main() -> None:
    log.info("Runner version: %s", RUNNER_VERSION)
    log_event(
        f"=== SESSION START ===  file={DATA_FILE}  port={SERIAL_PORT}  "
        f"roztdp<{ROZTDP_THRESHOLD}  stable>{SETPOINT_STABLE_HOURS}h  "
        f"min_on_hold={MIN_ON_HOLD_MINUTES}min"
    )
    log.info("Events log : %s", EVENTS_LOG)
    log.info("Debug log  : %s", LOG_DIR / 'relay_debug.log')
    log.info("Poll every : %d s", POLL_INTERVAL)

    ser = None
    serial_down_logged = False
    serial_recovered_logged = False

    stats      = RelayStats()
    last_cmd   = None
    last_extra = {}
    poll_n     = 0
    hold_until = None

    try:
        while True:
            poll_n  += 1
            relay_on = False
            reason   = "unknown"
            extra    = {}

            if ser is None:
                ser = open_serial(retries=1, retry_delay=0)
                if ser is None:
                    relay_on = False
                    reason = f"serial unavailable on {SERIAL_PORT}"
                    if not serial_down_logged:
                        log_event(f"SERIAL DOWN: {SERIAL_PORT} unavailable, running fail-safe OFF")
                        serial_down_logged = True
                        serial_recovered_logged = False
                else:
                    if not serial_recovered_logged:
                        log_event(f"SERIAL RECOVERED: connected to {SERIAL_PORT}")
                        serial_recovered_logged = True
                        serial_down_logged = False

            try:
                if ser is not None and not DATA_FILE:
                    reason = "DATA_FILE not set in .env"
                elif ser is not None and not os.path.isfile(DATA_FILE):
                    reason = f"file not found: {DATA_FILE}"
                elif ser is not None:
                    lines = read_file(DATA_FILE)
                    rows, err = parse_rows(lines)
                    if err:
                        reason = err
                    else:
                        relay_on, reason, extra = evaluate(rows)
            except Exception as exc:
                relay_on = False
                reason   = f"exception: {exc}"
                log.exception("Error in evaluation (poll #%d)", poll_n)

            # Additional rule: after turning ON, keep relay ON for at least
            # MIN_ON_HOLD_MINUTES, unless a hard fail-safe reason appears.
            if (
                last_cmd == "ON"
                and not relay_on
                and hold_until is not None
                and datetime.now() < hold_until
                and not is_hard_error_reason(reason)
            ):
                remaining = int((hold_until - datetime.now()).total_seconds() // 60)
                relay_on = True
                reason = f"minimum ON hold active ({remaining} min left)"

            cmd = "ON" if relay_on else "OFF"
            if extra:
                last_extra = extra

            # Build extended serial message: CMD;roztdp;stable_min
            dp_str = f"{last_extra['roztdp']:.2f}" if 'roztdp' in last_extra else "----"
            sp_str = str(last_extra.get('stable_min', '---'))
            serial_msg = f"{cmd};{dp_str};{sp_str}"
            tdp_text = f"{last_extra['roztdp']:.3f}" if 'roztdp' in last_extra else "n/a"

            if cmd != last_cmd:
                if cmd == "ON":
                    hold_until = datetime.now() + timedelta(minutes=MIN_ON_HOLD_MINUTES)
                    log.info("Hold timer armed until %s", hold_until.strftime("%H:%M:%S"))
                    stats.transition_on(reason, extra)
                else:
                    hold_until = None
                    if last_cmd is not None:   # skip the very first OFF at startup
                        stats.transition_off(reason)
                last_cmd = cmd

            # Always send heartbeat so Arduino watchdog stays reset
            if ser is not None:
                ok = send_command(ser, serial_msg)
                if not ok:
                    close_serial(ser)
                    ser = None
                    log.warning("Serial lost — will retry on next poll cycle")

            if relay_on:
                on_for = stats.on_duration_str()
                log.info("poll#%d  RELAY=ON   on_for=%s  | tdp=%s  | %s",
                         poll_n, on_for, tdp_text, reason)
                log_state(poll_n, cmd, tdp_text, reason, on_for=on_for)
            else:
                log.info("poll#%d  RELAY=OFF  on_for=-  | tdp=%s  | %s",
                         poll_n, tdp_text, reason)
                log_state(poll_n, cmd, tdp_text, reason, on_for="-")

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C)")
    finally:
        if last_cmd == "ON":
            stats.transition_off("script stopped")
        try:
            send_command(ser, "OFF;--;--")
        except Exception:
            pass
        close_serial(ser)
        log_event("=== SESSION END ===")
        print("=== SESSION END ===")


if __name__ == "__main__":
    main()
