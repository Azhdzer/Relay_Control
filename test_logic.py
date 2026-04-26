"""Isolated test for parse_rows + evaluate — no serial / no .env needed."""
from datetime import datetime, timedelta

# ---- копии функций из pc_relay_script.py ----

def read_file(path):
    for enc in ("utf-8-sig", "windows-1250", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.readlines()
        except UnicodeDecodeError:
            continue
    raise IOError(f"Cannot decode: {path}")


def parse_rows(lines):
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Data Czas;"):
            header_idx = i
            break
    if header_idx is None:
        return [], "header row not found"
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


ROZTDP_THRESHOLD = 0.35
SETPOINT_STABLE_HOURS = 2.0


def evaluate(rows):
    if not rows:
        return False, "no rows", {}
    latest = rows[-1]
    raw = latest.get("roztdp(15min)", "brak").strip()
    if raw.lower() in ("brak", ""):
        return False, "roztdp=brak (window not ready)", {}
    try:
        roztdp = float(raw.replace(",", "."))
    except ValueError:
        return False, f"roztdp not numeric: {raw!r}", {}
    if roztdp >= ROZTDP_THRESHOLD:
        return False, f"roztdp={roztdp:.3f} >= threshold {ROZTDP_THRESHOLD}", {"roztdp": roztdp}
    tz_now = latest.get("Tzadana", "").strip()
    rh_now = latest.get("RHzadana", "").strip()
    setpoint_start_dt = latest["_dt"]
    for row in reversed(rows):
        if row.get("Tzadana", "").strip() == tz_now and row.get("RHzadana", "").strip() == rh_now:
            setpoint_start_dt = row["_dt"]
        else:
            break
    stable = latest["_dt"] - setpoint_start_dt
    required = timedelta(hours=SETPOINT_STABLE_HOURS)
    stable_min = int(stable.total_seconds() // 60)
    if stable <= required:
        remaining = required - stable
        h, rem = divmod(int(remaining.total_seconds()), 3600)
        m = rem // 60
        return False, (
            f"stable={stable_min}min (need {int(required.total_seconds()//60)}min, {h}h{m:02d}m left)"
        ), {"roztdp": roztdp, "stable_min": stable_min}
    return True, f"roztdp={roztdp:.3f} | stable={stable_min}min", {
        "roztdp": roztdp, "stable_min": stable_min, "Tzadana": tz_now, "RHzadana": rh_now
    }


# ---- тесты ----
TEST_FILE = r"C:\Users\Artsiom\Desktop\2026-04-17 16.00_99.txt"

lines = read_file(TEST_FILE)
rows, err = parse_rows(lines)
print(f"Загружено строк: {len(rows)},  ошибка: {err}")
if rows:
    print(f"  первая:    {rows[0]['Data Czas']}")
    print(f"  последняя: {rows[-1]['Data Czas']}")
print()

cases = [
    # (описание, ожидаемый_on, срез_до_datetime_или_None)
    ("ON  — все условия выполнены (18:53)",        True,  datetime(2026, 4, 17, 18, 53, 18)),
    ("OFF — стабильность < 2ч (16:10, ~7 мин)",   False, datetime(2026, 4, 17, 16, 10, 0)),
    ("OFF — roztdp > 0.35 (16:22)",                False, datetime(2026, 4, 17, 16, 22, 0)),
    ("OFF — пустой список (fail-safe)",            False, None),
]

passed = 0
for name, expected_on, dt in cases:
    if dt is None:
        on, reason, extra = evaluate([])
    else:
        subset = [r for r in rows if r["_dt"] <= dt]
        on, reason, extra = evaluate(subset)
    tag = "PASS" if on == expected_on else "FAIL"
    if tag == "PASS":
        passed += 1
    print(f"[{tag}] {name}")
    print(f"       relay={on}  |  {reason}")
    print()

print(f"Итог: {passed}/{len(cases)} тестов прошло")
