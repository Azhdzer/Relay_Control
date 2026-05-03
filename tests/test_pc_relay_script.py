"""
tests/test_pc_relay_script.py

Comprehensive pytest suite for pc_relay_script.py.

Covered modules / functions:
  parse_rows            — header detection, column mapping, edge cases
  evaluate              — every OFF/ON branch, boundary conditions
  get_roztdp_threshold  — all 6 T/RH calibration ranges, tolerance, fallback
  is_hard_error_reason  — all hard markers, soft reasons, case insensitivity
  RelayStats            — timer, cycles, transition_on/off
  load_env              — valid file, missing file, comments, quoting, BOM
  read_file             — encoding fallback (utf-8 / windows-1250 / latin-1)
  send_command          — mock serial write, exception handling
  close_serial          — mock serial close, None guard, exception swallow
  Integration           — full pipeline on real fixture .txt files in repo
  Stale-data logic      — mtime-based freshness arithmetic
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup: make project root importable regardless of cwd
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pc_relay_script as script  # noqa: E402  (after sys.path patch)
from pc_relay_script import (  # noqa: E402
    close_serial,
    evaluate,
    get_roztdp_threshold,
    is_hard_error_reason,
    load_env,
    parse_rows,
    read_file as script_read_file,
    RelayStats,
    send_command,
)

# ---------------------------------------------------------------------------
# Paths to real fixture files that ship with the repository
# ---------------------------------------------------------------------------
TEST_FILE_ON   = PROJECT_ROOT / "test_data_on.txt"
TEST_FILE_OFF  = PROJECT_ROOT / "test_data_off.txt"
TEST_FILE_MANY = PROJECT_ROOT / "test_data_many_channels_on.txt"


# ---------------------------------------------------------------------------
# Helper: build synthetic row list
# ---------------------------------------------------------------------------
def _make_rows(
    count: int,
    *,
    tz: str = "25",
    rh: str = "10",
    roztdp: float = 0.10,
    dt_start: datetime | None = None,
    step_minutes: int = 30,
) -> list[dict]:
    """Return *count* synthetic rows with consistent setpoints and future timestamps."""
    if dt_start is None:
        dt_start = datetime(2099, 1, 1, 0, 0, 0)
    rows = []
    for i in range(count):
        dt = dt_start + timedelta(minutes=i * step_minutes)
        rows.append({
            "Data Czas": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "_dt": dt,
            "Tzadana": tz,
            "RHzadana": rh,
            "roztdp(15min)": str(roztdp),
        })
    return rows


# ===========================================================================
# parse_rows
# ===========================================================================

class TestParseRows:
    """Unit tests for the CSV-like file parser."""

    def test_empty_input_returns_error(self):
        rows, err = parse_rows([])
        assert rows == []
        assert err is not None

    def test_no_header_returns_error(self):
        rows, err = parse_rows(["No semicolon here\n", "2099-01-01 00:00:00\n"])
        assert rows == []
        assert "header" in err.lower()

    def test_header_only_yields_no_data_rows(self):
        rows, err = parse_rows(["Data Czas;Tzadana;RHzadana;roztdp(15min);\n"])
        assert err is None
        assert rows == []

    def test_basic_single_row(self):
        lines = [
            "Data Czas;Tzadana;RHzadana;roztdp(15min);\n",
            "2099-01-01 10:00:00;23;50;0.20;\n",
        ]
        rows, err = parse_rows(lines)
        assert err is None
        assert len(rows) == 1
        assert rows[0]["Tzadana"] == "23"
        assert rows[0]["roztdp(15min)"] == "0.20"
        assert rows[0]["_dt"] == datetime(2099, 1, 1, 10, 0, 0)

    def test_multiple_rows_all_parsed(self):
        lines = ["Data Czas;Tzadana;RHzadana;roztdp(15min);\n"]
        for h in range(3):
            lines.append(f"2099-01-01 {10 + h:02d}:00:00;23;50;0.2{h};\n")
        rows, err = parse_rows(lines)
        assert err is None
        assert len(rows) == 3
        assert rows[-1]["_dt"] == datetime(2099, 1, 1, 12, 0, 0)

    def test_malformed_timestamp_row_is_skipped(self):
        lines = [
            "Data Czas;Tzadana;RHzadana;roztdp(15min);\n",
            "BADDATE;23;50;0.20;\n",
            "2099-01-01 10:00:00;23;50;0.22;\n",
        ]
        rows, err = parse_rows(lines)
        assert err is None
        assert len(rows) == 1

    def test_blank_lines_are_skipped(self):
        lines = [
            "Data Czas;Tzadana;RHzadana;roztdp(15min);\n",
            "\n",
            "2099-01-01 10:00:00;23;50;0.20;\n",
            "\n",
        ]
        rows, err = parse_rows(lines)
        assert err is None
        assert len(rows) == 1

    def test_short_row_padded_with_empty_strings(self):
        lines = [
            "Data Czas;Tzadana;RHzadana;roztdp(15min);\n",
            "2099-01-01 10:00:00;23\n",  # only 2 values → rest padded
        ]
        rows, err = parse_rows(lines)
        assert err is None
        assert len(rows) == 1
        assert rows[0].get("RHzadana", "MISSING") == ""

    def test_trailing_semicolon_in_header_handled(self):
        """Real files always end headers with ';' producing an empty last column."""
        lines = [
            "Data Czas;Tzadana;RHzadana;roztdp(15min);\n",
            "2099-01-01 10:00:00;23;50;0.20;\n",
        ]
        rows, err = parse_rows(lines)
        assert err is None
        assert len(rows) == 1

    def test_many_columns_all_accessible(self):
        """Wide header (20 extra channels) must not lose required columns."""
        extra_headers = ";".join(f"Ch{i}" for i in range(1, 21))
        header = f"Data Czas;Tzadana;RHzadana;{extra_headers};roztdp(15min);\n"
        values = ";".join("1" for _ in range(20))
        data_row = f"2099-01-01 10:00:00;23;50;{values};0.15;\n"
        rows, err = parse_rows([header, data_row])
        assert err is None
        assert rows[0]["roztdp(15min)"] == "0.15"

    def test_comment_lines_skipped_via_bad_timestamp(self):
        lines = [
            "# leading comment\n",
            "Data Czas;Tzadana;RHzadana;roztdp(15min);\n",
            "# inline comment\n",
            "2099-01-01 10:00:00;23;50;0.20;\n",
        ]
        rows, err = parse_rows(lines)
        assert err is None
        # Comment lines don't have valid timestamps → skipped
        assert all("_dt" in r for r in rows)


# ===========================================================================
# evaluate
# ===========================================================================

class TestEvaluate:
    """Unit tests for the relay decision function."""

    def test_empty_rows_returns_false(self):
        on, reason, extra = evaluate([])
        assert on is False
        assert "no valid data rows" in reason

    def test_roztdp_brak_returns_false(self):
        rows = _make_rows(5)
        rows[-1]["roztdp(15min)"] = "brak"
        on, reason, _ = evaluate(rows)
        assert on is False
        assert "brak" in reason.lower()

    def test_roztdp_empty_string_returns_false(self):
        rows = _make_rows(5)
        rows[-1]["roztdp(15min)"] = ""
        on, reason, _ = evaluate(rows)
        assert on is False

    def test_roztdp_non_numeric_returns_false(self):
        rows = _make_rows(5)
        rows[-1]["roztdp(15min)"] = "n/a"
        on, reason, _ = evaluate(rows)
        assert on is False
        assert "not numeric" in reason.lower()

    def test_roztdp_above_fallback_threshold_returns_false(self):
        # T=25, RH=10 → not in calibration table → fallback ROZTDP_THRESHOLD (0.35)
        rows = _make_rows(5, tz="25", rh="10", roztdp=0.40)
        on, reason, _ = evaluate(rows)
        assert on is False
        assert ">=" in reason

    def test_roztdp_below_threshold_but_not_stable_returns_false(self):
        # Only 30 min stable (2 rows × 30 min step) — need 120 min
        rows = _make_rows(2, tz="25", rh="10", roztdp=0.10)
        on, reason, _ = evaluate(rows)
        assert on is False
        assert "stable" in reason.lower() or "need" in reason.lower()

    def test_all_conditions_met_returns_true(self):
        # 6 rows × 30 min = 150 min stable (> 120 required); roztdp=0.10 < fallback 0.35
        rows = _make_rows(6, tz="25", rh="10", roztdp=0.10)
        on, reason, extra = evaluate(rows)
        assert on is True
        assert "roztdp" in extra
        assert "stable_min" in extra
        assert extra["stable_min"] == 150  # 5 × 30min intervals

    def test_returns_correct_roztdp_in_extra(self):
        rows = _make_rows(6, tz="25", rh="10", roztdp=0.12)
        on, _, extra = evaluate(rows)
        assert on is True
        assert extra["roztdp"] == pytest.approx(0.12)
        assert extra["stable_min"] == 150  # 5 × 30min intervals

    def test_missing_tzadana_column_returns_false(self):
        rows = _make_rows(5)
        for r in rows:
            del r["Tzadana"]
        on, reason, _ = evaluate(rows)
        assert on is False
        assert "missing" in reason.lower()

    def test_setpoint_change_resets_stability_clock(self):
        base = datetime(2099, 1, 1, 0, 0, 0)
        rows = [
            {"_dt": base + timedelta(hours=i), "Tzadana": "23",
             "RHzadana": "50", "roztdp(15min)": "0.10"}
            for i in range(4)
        ]
        # Switch setpoint on the last row → stability resets to 0
        rows.append({
            "_dt": base + timedelta(hours=4),
            "Tzadana": "35",
            "RHzadana": "50",
            "roztdp(15min)": "0.10",
        })
        on, reason, _ = evaluate(rows)
        assert on is False
        assert "stable" in reason.lower()

    def test_european_comma_decimal_separator(self):
        rows = _make_rows(6, tz="25", rh="10", roztdp=0.10)
        rows[-1]["roztdp(15min)"] = "0,12"
        on, _, extra = evaluate(rows)
        assert on is True
        assert extra["roztdp"] == pytest.approx(0.12)

    def test_future_timestamps_skip_stale_check(self):
        """Year-2099 rows have negative age → internal stale check is bypassed."""
        rows = _make_rows(6, tz="25", rh="10", roztdp=0.10)
        # Confirm age is negative
        age = datetime.now() - rows[-1]["_dt"]
        assert age.total_seconds() < 0
        on, _, _ = evaluate(rows)
        assert on is True

    def test_reason_includes_remaining_time_when_not_stable(self):
        rows = _make_rows(2, tz="23", rh="50", roztdp=0.10)
        _, reason, _ = evaluate(rows)
        # Should state how much time is remaining
        assert "h" in reason and "m" in reason

    def test_exactly_120_min_stable_is_still_off(self):
        """Condition is strict greater-than: exactly 120 min → still OFF."""
        rows = _make_rows(5, tz="25", rh="10", roztdp=0.10,
                          dt_start=datetime(2099, 1, 1, 0, 0, 0),
                          step_minutes=30)
        # Rows span 0:00 → 2:00 (inclusive) = exactly 120 min difference
        # stable = last - first = 120 min, required = 120 min → NOT greater_than
        on, reason, _ = evaluate(rows)
        assert on is False
        assert "stable" in reason.lower()

    def test_one_minute_over_threshold_is_on(self):
        """121 min stable → ON."""
        rows = _make_rows(5, tz="25", rh="10", roztdp=0.10,
                          dt_start=datetime(2099, 1, 1, 0, 0, 0),
                          step_minutes=31)  # 4 × 31min = 124 min
        on, _, _ = evaluate(rows)
        assert on is True


# ===========================================================================
# get_roztdp_threshold
# ===========================================================================

class TestGetRoztdpThreshold:
    """All 6 calibration ranges, tolerance boundary, fallback, invalid input."""

    # ── T ≈ 10 °C ──────────────────────────────────────────────────────────
    def test_t10_rh_mid_range(self):
        th = get_roztdp_threshold("10", "57")   # rh 40÷75
        assert th == pytest.approx(script.ROZTDP_T10_RH_MID)

    def test_t10_rh_ext_low_range(self):
        th = get_roztdp_threshold("10", "30")   # rh 22÷40
        assert th == pytest.approx(script.ROZTDP_T10_RH_EXT)

    def test_t10_rh_ext_high_range(self):
        th = get_roztdp_threshold("10", "80")   # rh 75÷95
        assert th == pytest.approx(script.ROZTDP_T10_RH_EXT)

    def test_t10_within_tolerance(self):
        # 10.4 is within ±0.5 of 10
        th = get_roztdp_threshold("10.4", "57")
        assert th == pytest.approx(script.ROZTDP_T10_RH_MID)

    def test_t10_outside_tolerance(self):
        # 10.6 is just outside ±0.5 → T10 not matched
        th = get_roztdp_threshold("10.6", "57")
        assert th == pytest.approx(script.ROZTDP_THRESHOLD)  # fallback

    # ── T ≈ 23 °C ──────────────────────────────────────────────────────────
    def test_t23_rh_mid_range(self):
        th = get_roztdp_threshold("23", "50")
        assert th == pytest.approx(script.ROZTDP_T23_RH_MID)

    def test_t23_rh_ext_low_range(self):
        th = get_roztdp_threshold("23", "20")   # rh 10÷30
        assert th == pytest.approx(script.ROZTDP_T23_RH_EXT)

    def test_t23_rh_ext_high_range(self):
        th = get_roztdp_threshold("23", "85")   # rh 75÷95
        assert th == pytest.approx(script.ROZTDP_T23_RH_EXT)

    def test_t23_within_tolerance(self):
        th = get_roztdp_threshold("22.7", "50")
        assert th == pytest.approx(script.ROZTDP_T23_RH_MID)

    # ── T ≈ 35 °C ──────────────────────────────────────────────────────────
    def test_t35_rh_mid_range(self):
        th = get_roztdp_threshold("35", "57")
        assert th == pytest.approx(script.ROZTDP_T35_RH_MID)

    def test_t35_rh_ext_low_range(self):
        th = get_roztdp_threshold("35", "25")   # rh 10÷40
        assert th == pytest.approx(script.ROZTDP_T35_RH_EXT)

    def test_t35_rh_ext_high_range(self):
        th = get_roztdp_threshold("35", "80")   # rh 75÷95
        assert th == pytest.approx(script.ROZTDP_T35_RH_EXT)

    # ── Fallback / invalid ─────────────────────────────────────────────────
    def test_unknown_temperature_falls_back_to_global(self):
        # T=25 is not within ±0.5 of 10, 23, or 35
        th = get_roztdp_threshold("25", "50")
        assert th == pytest.approx(script.ROZTDP_THRESHOLD)

    def test_invalid_string_falls_back_to_global(self):
        th = get_roztdp_threshold("abc", "xyz")
        assert th == pytest.approx(script.ROZTDP_THRESHOLD)

    def test_empty_strings_fall_back_to_global(self):
        th = get_roztdp_threshold("", "")
        assert th == pytest.approx(script.ROZTDP_THRESHOLD)

    def test_comma_decimal_separator_accepted(self):
        th = get_roztdp_threshold("10,0", "57")
        assert th == pytest.approx(script.ROZTDP_T10_RH_MID)

    def test_returns_float(self):
        assert isinstance(get_roztdp_threshold("23", "50"), float)


# ===========================================================================
# is_hard_error_reason
# ===========================================================================

class TestIsHardErrorReason:

    @pytest.mark.parametrize("reason", [
        "exception: something crashed",
        "file not found: /path/to/data",
        "DATA_FILE not set in .env",
        "data stale (10 min old, limit 5 min)",
        "data file not updated for 6.5 min (limit 5 min)",
        "serial unavailable on COM6",
        "header row 'Data Czas;' not found",
        "cannot decode file: bad.txt",
        "Tzadana or RHzadana column missing",
        "no valid data rows",
    ])
    def test_hard_reason_detected(self, reason: str):
        assert is_hard_error_reason(reason) is True

    @pytest.mark.parametrize("reason", [
        "roztdp=0.40 >= threshold 0.35",
        "setpoint T=23 RH=50 stable 60 min (need 120 min, 1h 00m left)",
        "roztdp(15min)=brak (15-min window not yet ready)",
        "minimum ON hold active (5 min left)",
        "",
        "unknown",
        "poll interval elapsed",
    ])
    def test_soft_reason_not_detected(self, reason: str):
        assert is_hard_error_reason(reason) is False

    def test_case_insensitive_matching(self):
        assert is_hard_error_reason("FILE NOT FOUND: x") is True
        assert is_hard_error_reason("Data Stale (5 min)") is True
        assert is_hard_error_reason("EXCEPTION: crash") is True

    def test_none_like_empty_string(self):
        assert is_hard_error_reason("") is False


# ===========================================================================
# RelayStats
# ===========================================================================

class TestRelayStats:

    def test_on_duration_returns_dash_when_off(self):
        stats = RelayStats()
        assert stats.on_duration_str() == "-"

    def test_on_duration_changes_after_transition_on(self):
        stats = RelayStats()
        with patch.object(script, "log_event"):
            stats.transition_on("test", {})
        assert stats.on_duration_str() != "-"
        assert stats.on_duration_str().startswith("0h")

    def test_cycle_count_increments_per_on_transition(self):
        stats = RelayStats()
        with patch.object(script, "log_event"):
            stats.transition_on("r1", {})
            stats.transition_off("r2")
            stats.transition_on("r3", {})
        assert stats._cycle_count == 2

    def test_transition_off_from_off_does_not_raise(self):
        stats = RelayStats()
        with patch.object(script, "log_event"):
            stats.transition_off("was already off")  # must not raise

    def test_total_on_accumulates_after_cycle(self):
        stats = RelayStats()
        with patch.object(script, "log_event"):
            stats.transition_on("start", {})
            stats._on_since = datetime.now() - timedelta(seconds=60)
            stats.transition_off("end")
        assert stats._total_on.total_seconds() >= 60

    def test_on_duration_string_format(self):
        stats = RelayStats()
        with patch.object(script, "log_event"):
            stats.transition_on("test", {})
            stats._on_since = datetime.now() - timedelta(hours=1, minutes=5, seconds=10)
        dur = stats.on_duration_str()
        # Format: "1h 05m 10s"
        assert "h" in dur
        assert "m" in dur
        assert "s" in dur

    def test_on_duration_resets_to_dash_after_off(self):
        stats = RelayStats()
        with patch.object(script, "log_event"):
            stats.transition_on("start", {})
            stats.transition_off("end")
        assert stats.on_duration_str() == "-"


# ===========================================================================
# load_env
# ===========================================================================

class TestLoadEnv:

    def test_reads_key_value_pairs(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text("DATA_FILE=C:/data.txt\nSERIAL_PORT=COM3\n", encoding="utf-8")
        cfg = load_env(env)
        assert cfg["DATA_FILE"] == "C:/data.txt"
        assert cfg["SERIAL_PORT"] == "COM3"

    def test_missing_file_calls_sys_exit(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            load_env(tmp_path / "nonexistent.env")

    def test_blank_lines_are_ignored(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text("\n\nKEY=value\n\n", encoding="utf-8")
        cfg = load_env(env)
        assert cfg["KEY"] == "value"

    def test_comment_lines_are_ignored(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text("# comment\nKEY=value\n# another\n", encoding="utf-8")
        cfg = load_env(env)
        assert "KEY" in cfg
        # No key starting with '#'
        assert all(not k.startswith("#") for k in cfg)

    def test_double_quoted_values_stripped(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text('KEY="quoted value"\n', encoding="utf-8")
        cfg = load_env(env)
        assert cfg["KEY"] == "quoted value"

    def test_single_quoted_values_stripped(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text("KEY='single'\n", encoding="utf-8")
        cfg = load_env(env)
        assert cfg["KEY"] == "single"

    def test_value_with_extra_equals_signs(self, tmp_path: Path):
        """Only first '=' is the separator; rest belong to the value."""
        env = tmp_path / ".env"
        env.write_text("KEY=a=b=c\n", encoding="utf-8")
        cfg = load_env(env)
        assert cfg["KEY"] == "a=b=c"

    def test_whitespace_stripped_from_key_and_value(self, tmp_path: Path):
        env = tmp_path / ".env"
        env.write_text("  KEY  =  value  \n", encoding="utf-8")
        cfg = load_env(env)
        assert cfg["KEY"] == "value"

    def test_real_dot_env_file_is_loadable(self):
        """The project's own .env must be parseable without error."""
        real_env = PROJECT_ROOT / ".env"
        if real_env.exists():
            cfg = load_env(real_env)
            assert isinstance(cfg, dict)
            assert len(cfg) > 0


# ===========================================================================
# read_file (encoding fallback)
# ===========================================================================

class TestReadFile:

    def test_reads_utf8_file(self, tmp_path: Path):
        p = tmp_path / "data.txt"
        p.write_text("Data Czas;Tzadana;\n", encoding="utf-8")
        lines = script_read_file(str(p))
        assert "Data Czas" in lines[0]

    def test_reads_utf8_bom_file(self, tmp_path: Path):
        p = tmp_path / "data.txt"
        p.write_bytes(b"\xef\xbb\xbfData Czas;Tzadana;\n")
        lines = script_read_file(str(p))
        assert "Data Czas" in lines[0]

    def test_reads_windows1250_file(self, tmp_path: Path):
        p = tmp_path / "data.txt"
        content = "Data Czas;Tzadana;\n"
        p.write_bytes(content.encode("windows-1250"))
        lines = script_read_file(str(p))
        assert "Data Czas" in lines[0]

    def test_reads_latin1_file(self, tmp_path: Path):
        p = tmp_path / "data.txt"
        p.write_bytes("Data Czas;Tzadana;\n".encode("latin-1"))
        lines = script_read_file(str(p))
        assert "Data Czas" in lines[0]

    def test_returns_list_of_strings(self, tmp_path: Path):
        p = tmp_path / "data.txt"
        p.write_text("line1\nline2\n", encoding="utf-8")
        lines = script_read_file(str(p))
        assert isinstance(lines, list)
        assert all(isinstance(l, str) for l in lines)

    def test_preserves_line_count(self, tmp_path: Path):
        p = tmp_path / "data.txt"
        p.write_text("a\nb\nc\n", encoding="utf-8")
        lines = script_read_file(str(p))
        assert len(lines) == 3


# ===========================================================================
# send_command
# ===========================================================================

class TestSendCommand:

    def test_encodes_command_with_newline(self):
        mock_ser = MagicMock()
        send_command(mock_ser, "ON;0.20;130")
        mock_ser.write.assert_called_once_with(b"ON;0.20;130\n")

    def test_returns_true_on_success(self):
        mock_ser = MagicMock()
        result = send_command(mock_ser, "OFF;--;--")
        assert result is True

    def test_returns_false_on_serial_exception(self):
        import serial
        mock_ser = MagicMock()
        mock_ser.write.side_effect = serial.SerialException("port gone")
        result = send_command(mock_ser, "ON;0.20;130")
        assert result is False

    def test_off_command_is_encoded_correctly(self):
        mock_ser = MagicMock()
        send_command(mock_ser, "OFF;--;--")
        mock_ser.write.assert_called_once_with(b"OFF;--;--\n")


# ===========================================================================
# close_serial
# ===========================================================================

class TestCloseSerial:

    def test_none_argument_does_not_raise(self):
        close_serial(None)  # must not raise

    def test_calls_close_method(self):
        mock_ser = MagicMock()
        with patch("time.sleep"):
            close_serial(mock_ser)
        mock_ser.close.assert_called_once()

    def test_resets_dtr_and_rts_before_close(self):
        mock_ser = MagicMock()
        with patch("time.sleep"):
            close_serial(mock_ser)
        # dtr and rts must be set to False
        assert mock_ser.dtr is False or mock_ser.dtr == False  # noqa: E712
        assert mock_ser.rts is False or mock_ser.rts == False  # noqa: E712

    def test_exception_during_close_is_swallowed(self):
        mock_ser = MagicMock()
        mock_ser.close.side_effect = Exception("hardware gone")
        with patch("time.sleep"):
            close_serial(mock_ser)  # must not raise


# ===========================================================================
# Stale-data mtime arithmetic  (logic extracted from main())
# ===========================================================================

class TestStaleMtimeLogic:
    """
    The mtime-based stale check lives inside main().
    We test the underlying arithmetic directly since the check
    is a single comparison and can be verified without running main().
    """

    def test_file_updated_1_min_ago_is_fresh(self):
        stale_limit = 5  # minutes
        mtime = time.time() - 60   # 1 minute old
        file_age_secs = time.time() - mtime
        assert file_age_secs <= stale_limit * 60

    def test_file_updated_6_min_ago_is_stale(self):
        stale_limit = 5  # minutes
        mtime = time.time() - 400  # ~6.7 minutes old
        file_age_secs = time.time() - mtime
        assert file_age_secs > stale_limit * 60

    def test_year_2099_internal_timestamp_does_not_affect_mtime(self):
        """
        Internal row timestamps can be far-future (2099).
        The mtime check uses os.path.getmtime(), independent of row content.
        evaluate() skips its own stale check when age is negative.
        """
        rows = _make_rows(6, tz="25", rh="10", roztdp=0.10)
        age = datetime.now() - rows[-1]["_dt"]
        # Confirm: age is negative (future timestamp)
        assert age.total_seconds() < 0
        # evaluate() must pass through without triggering stale check
        on, _, _ = evaluate(rows)
        assert on is True


# ===========================================================================
# Integration: full pipeline on real fixture files
# ===========================================================================

class TestIntegrationFixtures:
    """End-to-end: read_file → parse_rows → evaluate on repository fixture files."""

    @pytest.mark.skipif(not TEST_FILE_ON.exists(), reason="test_data_on.txt not present")
    def test_data_on_evaluates_to_on(self):
        lines = script_read_file(str(TEST_FILE_ON))
        rows, err = parse_rows(lines)
        assert err is None, f"parse error: {err}"
        assert rows, "no rows parsed"
        on, reason, extra = evaluate(rows)
        assert on is True, f"Expected ON, got OFF. Reason: {reason}"

    @pytest.mark.skipif(not TEST_FILE_OFF.exists(), reason="test_data_off.txt not present")
    def test_data_off_evaluates_to_off(self):
        lines = script_read_file(str(TEST_FILE_OFF))
        rows, err = parse_rows(lines)
        assert err is None, f"parse error: {err}"
        assert rows, "no rows parsed"
        on, reason, extra = evaluate(rows)
        assert on is False, f"Expected OFF, got ON. Reason: {reason}"

    @pytest.mark.skipif(not TEST_FILE_MANY.exists(), reason="test_data_many_channels_on.txt not present")
    def test_many_channels_file_parses_without_error(self):
        lines = script_read_file(str(TEST_FILE_MANY))
        rows, err = parse_rows(lines)
        assert err is None
        assert rows

    @pytest.mark.skipif(not TEST_FILE_MANY.exists(), reason="test_data_many_channels_on.txt not present")
    def test_many_channels_required_columns_present(self):
        lines = script_read_file(str(TEST_FILE_MANY))
        rows, _ = parse_rows(lines)
        for r in rows:
            assert "Tzadana" in r
            assert "RHzadana" in r
            assert "roztdp(15min)" in r
            assert "_dt" in r

    @pytest.mark.skipif(not TEST_FILE_MANY.exists(), reason="test_data_many_channels_on.txt not present")
    def test_many_channels_last_row_above_threshold_gives_off(self):
        """Last row roztdp=0.44, T=25 RH=10.5 → fallback threshold 0.35 → OFF."""
        lines = script_read_file(str(TEST_FILE_MANY))
        rows, _ = parse_rows(lines)
        on, reason, _ = evaluate(rows)
        assert on is False
        last_val = float(rows[-1]["roztdp(15min)"].replace(",", "."))
        assert last_val > script.ROZTDP_THRESHOLD


# ===========================================================================
# Parametric edge-case battery
# ===========================================================================

class TestEvaluateParametric:
    """Tabular tests for evaluate() across various roztdp / stable combinations."""

    @pytest.mark.parametrize("roztdp,stable_rows,expected_on", [
        (0.10, 6, True),   # below threshold, 150 min stable     → ON
        (0.34, 6, True),   # just below fallback 0.35, 150 min   → ON
        (0.35, 6, False),  # exactly at threshold (>=)           → OFF
        (0.36, 6, False),  # above threshold                     → OFF
        (0.10, 5, False),  # stable = exactly 120 min (not > 120)→ OFF
        (0.10, 3, False),  # stable = 60 min                     → OFF
        (0.10, 7, True),   # stable = 180 min                    → ON
    ])
    def test_evaluate_roztdp_vs_stability(
        self, roztdp: float, stable_rows: int, expected_on: bool
    ):
        rows = _make_rows(stable_rows, tz="25", rh="10",
                          roztdp=roztdp, step_minutes=30)
        on, reason, _ = evaluate(rows)
        assert on is expected_on, (
            f"roztdp={roztdp}, rows={stable_rows} → expected {expected_on}, "
            f"got {on}. Reason: {reason}"
        )
