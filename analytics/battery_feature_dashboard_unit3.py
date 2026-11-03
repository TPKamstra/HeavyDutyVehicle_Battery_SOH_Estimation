"""
battery_feature_dashboard_unit3.py

Dashboard for lab battery **ul18_12_unit3** (test plan v3: 5 degradation cycles
per block, 1 h rest before the C/5 test, Ah-based SoC sweep, stop at 50 % of the
Block 1 capacity). Started from battery_feature_dashboard_unit2.py and kept as a
separate script so the batteries are never pooled, per CLAUDE.md's Battery_ID
rule. Feature extraction is shared via testday_v2_features.py.

What differs from unit2, and why:
- SoC per run comes from the Pi's Coulomb-counted _sweep_points.csv, never from
  an OCV table (this battery reads 13.36 V at 100 % and 12.04 V at 10 % after a
  30 min rest, nowhere near the table unit2 used).
- SOH is relative to Block 1 (_soh_reference.json), with the 50 % stop line.
- Setpoint Check: every run is compared with the profile it actually replayed
  (matched on Profile_Hash). Until the fix of 2026-11-02 the BDPS sink current
  stayed frozen at its previous limit, so features from discharge events of
  those runs are invalid. They are hidden by default, never deleted.

The test is still running, so every tab handles missing and half-written files.
Use **Rescan** to pick up files synced by pi_daily_report.py.

Run with:
    panel serve battery_feature_dashboard_unit3.py --show
"""

import hashlib
import json
import os
import re
import warnings

import numpy as np
import pandas as pd
import panel as pn
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import testday_v2_features as v2feat

warnings.filterwarnings("ignore")
pn.extension("plotly", "tabulator")

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(__file__)
# The raw logs are gitignored and synced by pi_daily_report.py into
# BatPi_Download\\LOGBATTEST\\<battery_id>\\ -- see analytics/CLAUDE.md's
# "BATPI_DATA_DIR" section for why this is a constant with an env override.
_KNOWN_DATA_DIR = r"C:\Users\TPKam\Documents\BatPi_Download"
DATA_DIR       = os.environ.get("BATPI_DATA_DIR") or _KNOWN_DATA_DIR
BATTERY_ID     = "ul18_12_unit3"
LOG_DIR        = os.path.join(DATA_DIR, "LOGBATTEST", BATTERY_ID)
PROFILE_DIR    = os.path.join(BASE_DIR, "..", "lab-control", "LabSetupV2")
PROFILE_FILES  = ["test_day_profile_new.csv", "test_day_profile_v2_cap60A.csv"]
SOH_STOP_PCT   = 50.0
TRACKING_TOL_FRAC = 0.10   # same criterion as the Pi's own "PROFILE TRACKING FAILED" check
TRACKING_TOL_MIN_A = 0.2
V_LIMIT_SINK   = 10.2     # BDPS voltage floor while sinking; hitting it limits current physically
V_LIMIT_MARGIN = 0.05
MIN_SINK_SAMPLES = 4
NOMINAL_AH     = 18.0     # Ultracell UL18-12 nameplate C/20 capacity
MIN_DEGR_BYTES = 5_000    # Degr charge/discharge files smaller than this are fragments/aborted

# Validated categorical palette (fixed order — CVD-safe adjacent pairs; the
# first 3 slots also clear all-pairs comparisons, so scatter/bubble plots
# should stay within them where practical). Status colors are reserved and
# never reused as series colors, so state (complete/in-progress/invalid) is
# always visually distinct from series identity (which run, which feature).
_COLORS = [
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
]
_STATUS_GOOD     = "#0ca30c"
_STATUS_WARNING  = "#fab219"
_STATUS_SERIOUS  = "#ec835a"
_STATUS_CRITICAL = "#d03b3b"
_DIVERGING_LO, _DIVERGING_MID, _DIVERGING_HI = "#2a78d6", "#f0efec", "#e34948"
_DIVERGING_SCALE = [[0.0, _DIVERGING_LO], [0.5, _DIVERGING_MID], [1.0, _DIVERGING_HI]]
# Sequential = one hue, light -> dark (same blue as categorical slot 1).
# For genuinely continuous magnitude (a heatmap-style "near zero recedes to
# the surface" quantity) the full range is correct as-is.
_SEQUENTIAL_BLUE = [
    [0.0, "#cde2fb"], [0.25, "#86b6ef"], [0.5, "#3987e5"],
    [0.75, "#1c5cab"], [1.0, "#0d366b"],
]
# Ordinal ramp for a *discrete ordered* quantity like block/cycle number,
# used by the Pairwise scatter explorer's "colour by". Orange, not blue --
# the house palette's own rule for a second simultaneous one-hue ramp is
# "the next categorical slot's hue" (color-formula.md), i.e. categorical
# slot 2 (#eb6834). Blue stayed reserved for the *continuous* colorbar case
# below (build_scatter_pair()'s non-ordinal branch), so the two never
# collide -- but this also gives "colour by block" real hue contrast
# against blue-heavy UI chrome, which is what made the all-blue version
# still hard to read even after fixing its contrast floor.
#
# Derived (no orange ordinal ramp is in palette.md to pull from) by cloning
# the *documented* blue ramp's own HSL lightness trajectory (0.894 -> 0.235)
# onto orange's hue/saturation (H=17.0deg, S=0.821, from the categorical
# anchor #eb6834), then re-picking the floor step by the same rule used for
# blue: first step whose WCAG contrast against the light surface (#fcfcfb)
# clears 2:1. That lands one step later than blue's (index 4, not 3) --
# orange needs to go slightly darker before it clears the floor -- at
# contrast 2.31 (comfortably past the 2.0 minimum, same margin as blue's
# 2.06). Steps below verified strictly monotonic in lightness and contrast.
_ORDINAL_STEPS = [
    "#f08f69", "#ee7c4f", "#eb6733", "#e85318", "#d04a14",
    "#b54012", "#9d3810", "#842f0d", "#6d270b",
]
# Above this many distinct values, a discrete per-value legend stops being
# legible -- fall back to the continuous sequential colorbar instead.
_MAX_ORDINAL_VALUES = 24


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _ordinal_ramp(n: int) -> list:
    """n evenly-spaced, monotonically-darkening colors interpolated along
    _ORDINAL_STEPS. Snapping straight to the 9 *named* steps ties adjacent
    values together once n > 9 (block 20 needs its own shade too, not just
    block 1-9 repeated) -- interpolating keeps every value strictly darker
    than the last, for any n up to _MAX_ORDINAL_VALUES."""
    if n <= 1:
        return [_ORDINAL_STEPS[-1]]
    anchors = [_hex_to_rgb(c) for c in _ORDINAL_STEPS]
    m = len(anchors) - 1
    out = []
    for i in range(n):
        t = i / (n - 1) * m
        lo = int(t)
        hi = min(lo + 1, m)
        frac = t - lo
        rgb = tuple(anchors[lo][k] + (anchors[hi][k] - anchors[lo][k]) * frac for k in range(3))
        out.append(_rgb_to_hex(rgb))
    return out
_EVENT_COLORS = {
    "rest_baseline":       "#e0e0e0",
    "ocv_window":          "#c7c7c7",
    "wakeup_load":         "#ffbb78",
    "glow_plug_like_load": "#ff9896",
    "crank_pulse":         "#d62728",
    "recovery_rest":       "#aec7e8",
    "alternator_charge":   "#98df8a",
    "ramp_like_load":      "#c5b0d5",
    "driving_aux_load":    "#9edae5",
}
_META_COLS = {
    "battery_id", "block", "kind", "timestamp", "filename", "ocv_label_V", "soc_pct",
    "soc_source", "target_soc_pct", "run_in_progress", "discharge_tracking_ok", "profile_cap",
}

_DEGR_PAT = re.compile(
    r"^Block_(\d+)_Degr_(\d+)_(charge|discharge)_bdps_"
    r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.csv$"
)


# The Pi renames Block_01_SoC060_stepdown_* to Block_01_SoC060_OCV12p68V_stepdown_*
# once the OCV is known. The sync never deletes, so a copy made mid-step stays
# behind locally under the provisional name. Never read those.
_STALE_PROVISIONAL_PAT = re.compile(r"^(?:Block_\d+|SoCsweep)_SoC\d+_(?:stepdown|rest)_")

# ── Degradation-cycle discovery (Block_<nn>_Degr_<cycle>_charge/discharge) ────

SAMPLE_GAP_COARSE_S = 5.0  # median inter-sample gap above this -> dt-clip integration undercounts


def _cycle_ah(path: str, sign: str) -> tuple:
    """Ah + completeness + coarse-sampling flag for one charge/discharge Degr
    file.

    Fragments (e.g. an aborted cycle logged as a 3-row/246-byte file) are
    flagged, not silently dropped, since incomplete data is the expected
    state here. Separately: starting mid-Block 4 the Pi's logging interval
    degraded badly (median gap up to ~45s by Block 5, against a 10s dt-clip)
    — Degr *discharge* current is confirmed constant (~-9A) in this test
    protocol, so it's corrected the same way as the SOH C/5 calc
    (duration x mean|I|, immune to sample sparsity). Degr *charge* is CC/CV
    (current tapers from ~5A to <1A near the end) — a coarse-sampled charge
    file has no reliable correction, so it's just flagged unreliable rather
    than "fixed" with a number that can't be trusted.

    Returns (ah, n_rows, size_bytes, complete, coarse_sampling). Disk-cached
    (keyed on path + mtime + size, via testday_v2_features.py's shared
    cached_call/_fingerprint) since this file set (402+ files across 20
    blocks) doesn't change once written, bar the rare retried/re-run cycle.
    """
    fp = v2feat._fingerprint(path)
    return v2feat.cached_call("cycle_ah", (path, sign, fp), lambda: _cycle_ah_impl(path, sign))


def _cycle_ah_impl(path: str, sign: str) -> tuple:
    size = os.path.getsize(path)
    df = pd.read_csv(path)
    n = len(df)
    if size < MIN_DEGR_BYTES or n < 10:
        return None, n, size, False, False

    gap_series = df["Elapsed_s"].diff()
    median_gap = gap_series.median()
    coarse = bool(pd.notna(median_gap) and median_gap > SAMPLE_GAP_COARSE_S)

    if sign == "discharge":
        mask = df["Current"] < -0.5
        if coarse:
            duration_s = df["Elapsed_s"].max() - df["Elapsed_s"].min()
            ah = float(df.loc[mask, "Current"].abs().mean() * duration_s / 3600)
        else:
            dt = gap_series.clip(0, 10)
            ah = float((df.loc[mask, "Current"].abs() * dt.loc[mask]).sum() / 3600)
    else:
        mask = df["Current"] > 0.5
        dt = gap_series.clip(0, 10)
        ah = float((df.loc[mask, "Current"] * dt.loc[mask]).sum() / 3600)

    return ah, n, size, True, coarse


def find_degr_cycles(log_dir: str) -> pd.DataFrame:
    """One row per (block, cycle): discharge/charge Ah, coulombic efficiency,
    and a completeness flag. Ready for as many blocks/cycles as show up.

    A cycle can look well-formed (passes the size/row-count check) but still
    be a truncated in-progress write — e.g. a charge cycle logged mid-charge
    has a normal header and thousands of rows, just far less Ah than a
    finished one. So completeness is refined in a second pass: any Ah value
    below half the block's own median for that charge/discharge is flagged
    too, rather than trusting the file-size check alone.

    A given (block, cycle, charge/discharge) can have more than one file on
    disk — e.g. a coarse-sampled cycle logged during the logging-rate
    regression, later re-run cleanly. `ts` sorts lexicographically the same
    as chronologically, so only the most recent file per identity is kept.
    """
    entries: dict = {}
    ts_seen: dict = {}
    for fn in sorted(os.listdir(log_dir)):
        m = _DEGR_PAT.match(fn)
        if not m:
            continue
        block, cycle, kind, ts = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        key = (block, cycle, kind)
        if key not in ts_seen or ts > ts_seen[key]:
            ts_seen[key] = ts
            entries.setdefault((block, cycle), {})[kind] = os.path.join(log_dir, fn)

    def _build_row(item):
        (block, cycle), kinds = item
        row = {"block": block, "cycle": cycle}
        for kind in ("discharge", "charge"):
            if kind in kinds:
                ah, n_rows, size, complete, coarse = _cycle_ah(kinds[kind], kind)
                row[f"{kind}_ah"] = ah
                row[f"{kind}_complete"] = complete
                row[f"{kind}_coarse_sampling"] = coarse
            else:
                row[f"{kind}_ah"] = None
                row[f"{kind}_complete"] = False
                row[f"{kind}_coarse_sampling"] = False
        return row

    # I/O-bound (CSV parse per file, first-run cache miss on ~400 Degr
    # files) -- threads give a real speedup since pandas' C parser releases
    # the GIL.
    rows = v2feat.parallel_map(_build_row, sorted(entries.items()))
    v2feat.flush_cache()

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["first_after_sweep"] = df["cycle"] == df.groupby("block")["cycle"].transform("min")
    for kind in ("discharge", "charge"):
        col = f"{kind}_ah"
        # Per-block median (this battery's Ah scale shrinks a lot over 20
        # blocks of degradation — a global median would flag every cycle in
        # a heavily-degraded late block as "too low" relative to the early,
        # healthy blocks' much larger Ah values).
        valid_ah = df[col].where(df[f"{kind}_complete"])
        median_by_block = valid_ah.groupby(df["block"]).transform("median")
        too_low = (
            df[col].notna() & median_by_block.notna() & (median_by_block > 0)
            & (df[col] < 0.5 * median_by_block)
        )
        if kind == "discharge":
            # The first discharge of a block follows the SoC sweep, so the
            # battery is already near 10 % SoC: a small Ah value is correct.
            too_low &= ~df["first_after_sweep"]
        df.loc[too_low, f"{kind}_complete"] = False

    df["coulombic_eff_pct"] = None
    both = df["discharge_ah"].notna() & df["charge_ah"].notna() & ~df["first_after_sweep"]
    df.loc[both, "coulombic_eff_pct"] = df.loc[both, "charge_ah"] / df.loc[both, "discharge_ah"] * 100

    def _status(r):
        if not (r["discharge_complete"] and r["charge_complete"]):
            return "in progress / fragment"
        if r["discharge_coarse_sampling"] or r["charge_coarse_sampling"]:
            return "complete (Ah corrected/uncertain — coarse Pi sampling)"
        if r["first_after_sweep"]:
            return "complete (discharge starts after the SoC sweep)"
        return "complete"

    df["status"] = df.apply(_status, axis=1)
    return df


# ── File inventory (all file kinds, for tracking what's landed so far) ───────

def build_file_inventory(log_dir: str) -> pd.DataFrame:
    rows = []
    for fn in sorted(os.listdir(log_dir)):
        if not fn.endswith(".csv") or "_sensor_" in fn or "_sensor.csv" in fn:
            continue  # list the bdps side only; sensor files are paired 1:1
        if _STALE_PROVISIONAL_PAT.match(fn):
            continue  # already renamed on the Pi, see _STALE_PROVISIONAL_PAT
        path = os.path.join(log_dir, fn)
        if "_SOH_C5_" in fn:
            kind = "SOH C/5"
        elif "_testday_" in fn:
            kind = "Test-day (v2)"
        elif "_stepdown_" in fn:
            kind = "Step-down"
        elif "rest_before_soh" in fn:
            kind = "Rest before SOH"
        elif "_rest_" in fn:
            kind = "Sweep rest"
        elif "_Degr_" in fn and "_charge_" in fn:
            kind = "Degr charge"
        elif "_Degr_" in fn and "_discharge_" in fn:
            kind = "Degr discharge"
        elif "charge_full" in fn:
            kind = "Initial full charge"
        elif "sweep_charge" in fn:
            kind = "Sweep recharge"
        else:
            kind = "other"
        m_block = re.search(r"Block_(\d+)", fn)
        m_cycle = re.search(r"Degr_(\d+)", fn)
        rows.append({
            "file": fn,
            "block": int(m_block.group(1)) if m_block else None,
            "cycle": int(m_cycle.group(1)) if m_cycle else None,
            "kind": kind,
            "size_kb": round(os.path.getsize(path) / 1024, 1),
        })
    return pd.DataFrame(rows)


# ── Pi bookkeeping files (_checkpoint, _block_summary, _sweep_points, _events) ─

def _read_bookkeeping_csv(name: str) -> pd.DataFrame:
    path = os.path.join(LOG_DIR, name)
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, on_bad_lines="skip")
    except Exception:
        return pd.DataFrame()  # empty or mid-append at sync time


def _read_json(name: str) -> dict:
    try:
        with open(os.path.join(LOG_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_sweep_points() -> pd.DataFrame:
    """_sweep_points.csv with one row per (block, run). A sweep that restarted
    (Block 2, after the 2026-11-02 fix) appends new rows under the old ones, so
    the last row per point is the one that matches the file on disk."""
    df = _read_bookkeeping_csv("_sweep_points.csv")
    if df.empty:
        return df
    return df.drop_duplicates(subset=["block", "run"], keep="last").sort_values(["block", "run"]).reset_index(drop=True)


def load_block_soh() -> pd.DataFrame:
    """Per-block C/5 as the Pi measured it. _block_summary.csv only gets a row
    once a block ends, so the running block is taken from the checkpoint."""
    df = _read_bookkeeping_csv("_block_summary.csv")
    cols = ["block", "soh_c5_ah", "soh_pct_of_ref", "ocv_before_soh", "unplanned_gap_h", "degr_charge_timeouts"]
    df = df[[c for c in cols if c in df.columns]].copy() if not df.empty else pd.DataFrame(columns=cols)
    cp = _read_json("_checkpoint.json")
    running = cp.get("block_summary") or {}
    if running.get("soh_c5_ah") is not None and cp.get("block_index") not in set(df["block"]):
        row = {c: running.get(c) for c in cols}
        row["block"] = cp["block_index"]
        df = pd.concat([df.astype(object), pd.DataFrame([row]).astype(object)], ignore_index=True)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("block").reset_index(drop=True)


def _soh_sensor_temperature(block: int):
    """Median temperature during that block's C/5 test (sensor log). Temperature
    isn't controlled, and capacity depends on it: unit2 ran near 27 °C, unit3 near 23 °C."""
    candidates = sorted(
        fn for fn in os.listdir(LOG_DIR) if fn.startswith(f"Block_{block:02d}_SOH_C5_sensor_")
    )
    if not candidates:
        return None
    path = os.path.join(LOG_DIR, candidates[-1])

    def _impl():
        try:
            return float(pd.read_csv(path, usecols=["temperature"], on_bad_lines="skip")["temperature"].median())
        except Exception:
            return None

    return v2feat.cached_call("unit3_soh_temp", (path, v2feat._fingerprint(path)), _impl)


def build_soh_table(pi_soh: pd.DataFrame, log_soh: pd.DataFrame) -> pd.DataFrame:
    """Pi's C/5 (decides the 50 % stop) next to this repo's integration of the
    same file. The two differ by a steady ~0.2 Ah, not yet explained, so the
    percentages are each relative to their own Block 1."""
    blocks = sorted(set(pi_soh["block"].dropna().astype(int)) | set(log_soh["block"].astype(int)) if not log_soh.empty
                    else set(pi_soh["block"].dropna().astype(int)))
    pi = pi_soh.set_index("block") if not pi_soh.empty else pd.DataFrame()
    log = log_soh.set_index("block") if not log_soh.empty else pd.DataFrame()
    ref = _read_json("_soh_reference.json").get("capacity_ah")
    log_ref = None
    if not log.empty and 1 in log.index and bool(log.loc[1, "reached_cutoff"]):
        log_ref = float(log.loc[1, "block_c5_capacity_ah"])
    rows = []
    for b in blocks:
        pi_ah = float(pi.loc[b, "soh_c5_ah"]) if b in pi.index and pd.notna(pi.loc[b, "soh_c5_ah"]) else None
        log_ah = float(log.loc[b, "block_c5_capacity_ah"]) if b in log.index else None
        final = bool(log.loc[b, "reached_cutoff"]) if b in log.index else pi_ah is not None
        rows.append({
            "block": b,
            "pi_c5_ah": pi_ah,
            "pi_soh_pct": round(pi_ah / ref * 100, 1) if pi_ah and ref else None,
            "log_c5_ah": round(log_ah, 3) if log_ah else None,
            "log_soh_pct": round(log_ah / log_ref * 100, 1) if log_ah and log_ref else None,
            "log_minus_pi_ah": round(log_ah - pi_ah, 3) if log_ah and pi_ah else None,
            "final": final,
            "temp_c": _soh_sensor_temperature(b),
            "charge_timeouts": pi.loc[b, "degr_charge_timeouts"] if b in pi.index and "degr_charge_timeouts" in pi else None,
            "gap_h": pi.loc[b, "unplanned_gap_h"] if b in pi.index and "unplanned_gap_h" in pi else None,
        })
    return pd.DataFrame(rows)


def _projected_stop_block(table: pd.DataFrame):
    """Linear estimate of the block reaching 50 %, or None. Crude, and early
    capacity can still rise, so it's labelled as an estimate wherever shown."""
    sub = table.dropna(subset=["pi_soh_pct"]) if not table.empty else table
    if len(sub) < 3:
        return None
    slope, intercept = np.polyfit(sub["block"].astype(float), sub["pi_soh_pct"].astype(float), 1)
    if slope >= 0:
        return None
    return (SOH_STOP_PCT - intercept) / slope


# ── Profiles and the setpoint check ──────────────────────────────────────────

def _load_profiles() -> dict:
    """Profile_Hash -> (time, current, file). The log's hash is over the Pi's
    CRLF file; git may rewrite line endings here, so both spellings are indexed."""
    out = {}
    for name in PROFILE_FILES:
        path = os.path.join(PROFILE_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            raw = f.read()
        lf = raw.replace(b"\r\n", b"\n")
        df = pd.read_csv(path)
        profile = (df["Time (s)"].to_numpy(), df["Current (A)"].to_numpy(), name)
        for variant in (lf, lf.replace(b"\n", b"\r\n")):
            out[hashlib.sha256(variant).hexdigest()] = profile
    return out


_PROFILES = _load_profiles()


def _setpoint_at(elapsed: np.ndarray, times: np.ndarray, currents: np.ndarray) -> np.ndarray:
    idx = np.clip(np.searchsorted(times, elapsed, side="right") - 1, 0, len(times) - 1)
    sp = currents[idx].copy()
    sp[np.abs(sp) < 0.2] = 0.0  # backend.py's _snap_zero
    return sp


def _run_quality_impl(path: str) -> dict:
    """Per-event measured vs commanded current, plus the pre-crank current. Only
    small results are returned (see the cache note in testday_v2_features.py)."""
    try:
        df = pd.read_csv(path, on_bad_lines="skip")
    except Exception:
        return {"status": "unreadable", "events": [], "cranks": {}}
    if df.empty or "Profile_Hash" not in df.columns:
        return {"status": "unreadable", "events": [], "cranks": {}}
    profile = _PROFILES.get(str(df["Profile_Hash"].iloc[0]))
    if profile is None:
        return {"status": "unknown_profile", "profile": str(df["Profile_Hash"].iloc[0])[:12], "events": [], "cranks": {}}
    times, currents, name = profile
    df["sp"] = _setpoint_at(df["Elapsed_s"].to_numpy(), times, currents)

    events, cranks = [], {}
    crank_order = 0
    for idx, g in df.groupby("Event_Index", sort=True):
        etype = g["Event_Type"].iloc[0]
        if etype == "crank_pulse":
            crank_order += 1
            prev = df[df["Event_Index"] == idx - 1]
            tail = prev[prev["Elapsed_s"] >= prev["Elapsed_s"].max() - v2feat.PREV_TAIL_WIN_S] if not prev.empty else prev
            cranks["cold" if crank_order == 1 else "hot"] = {
                "I_pre_A": float(tail["Current"].abs().median()) if not tail.empty else 0.0,
                "V_pre": float(tail["Voltage"].mean()) if not tail.empty else None,
                "I_peak_A": float(g["Current"].abs().max()),
                "V_min": float(g.loc[g["Elapsed_s"] <= g["Elapsed_s"].min() + v2feat.CRANK_VMIN_WIN_S, "Voltage"].min()),
            }
        sink = g[g["sp"] < 0]
        # Replay timing can put a sample of the next event's setpoint just
        # before this event's boundary, so a stray sample isn't a discharge event.
        if len(sink) < MIN_SINK_SAMPLES:
            continue
        sp_med = float(sink["sp"].median())
        meas_med = float(sink["Current"].median())
        err = abs(meas_med - sp_med)
        tol = max(TRACKING_TOL_MIN_A, TRACKING_TOL_FRAC * abs(sp_med))
        v_min = float(g["Voltage"].min())
        if err <= tol:
            status = "ok"
        elif v_min <= V_LIMIT_SINK + V_LIMIT_MARGIN:
            status = "voltage_limited"
        else:
            status = "failed"
        events.append({
            "event_index": int(idx), "event": etype, "status": status,
            "sp_median_A": round(sp_med, 2), "meas_median_A": round(meas_med, 2),
            "sp_peak_A": round(float(sink["sp"].min()), 2), "meas_peak_A": round(float(g["Current"].min()), 2),
            "error_A": round(err, 2), "v_min_V": round(v_min, 3),
        })
    return {"status": "checked", "profile": name, "events": events, "cranks": cranks}


def run_quality(path: str) -> dict:
    return v2feat.cached_call("unit3_quality_v3", (path, v2feat._fingerprint(path)), lambda: _run_quality_impl(path))


# Which raw feature columns rest on which scripted discharge event. A feature is
# only as valid as the current its event really carried. total_ah_* and
# alternator ratio integrate over several events, so they need all of them.
_FEATURE_EVENT_PREFIXES = {
    "wakeup_load_1_": [2], "wakeup_load_2_": [3], "glow_plug_like_load_1_": [4],
    "crank_cold_": [5], "recovery_after_cold_": [5], "crank_1_": [5],
    "driving_aux_load_1_": [8], "ramp_like_load_1_": [9],
    "crank_hot_": [11], "recovery_after_hot_": [11], "crank_2_": [11],
    "total_ah_": [2, 3, 4, 5, 8, 9, 11], "alternator_charge_after_": [2, 3, 4, 5],
    "min_voltage": [2, 3, 4, 5, 8, 9, 11], "final_voltage": [2, 3, 4, 5, 8, 9, 11],
}
# V_ref bug, documented in the unit2 export's NOTES.md: never offered as a feature.
_UNUSABLE_FEATURES = ["wakeup_load_2_R_int_mohm", "wakeup_load_2_R_int_mohm_invalid"]


def _invalid_columns(columns, failed_events: set) -> list:
    out = []
    for c in columns:
        for prefix, needs in _FEATURE_EVENT_PREFIXES.items():
            if c.startswith(prefix) and failed_events.intersection(needs):
                out.append(c)
                break
    return out


def attach_quality(df: pd.DataFrame, sweep: pd.DataFrame) -> pd.DataFrame:
    """Adds SoC (Coulomb-counted, from _sweep_points.csv; else the filename's
    target), tracking flags, in-progress marking, and ΔI crank resistance."""
    if df.empty:
        return df
    df = df.drop(columns=[c for c in _UNUSABLE_FEATURES if c in df.columns]).copy()
    by_file = sweep.set_index("testday_file") if not sweep.empty and "testday_file" in sweep else pd.DataFrame()
    profile_max = max((float(p[0][-1]) for p in _PROFILES.values()), default=None)
    newest = df["timestamp"].max()

    records = []
    for _, r in df.iterrows():
        q = run_quality(os.path.join(LOG_DIR, r["filename"]))
        rec = {}
        m = re.search(r"_SoC(\d+)_", r["filename"])
        target = int(m.group(1)) if m else None
        if r["filename"] in by_file.index:
            rec["soc_pct"] = float(by_file.loc[r["filename"], "soc_cc_pct"])
            rec["soc_source"] = "coulomb"
        else:
            rec["soc_pct"] = float(target) if target is not None else None
            rec["soc_source"] = "target"
        rec["target_soc_pct"] = target
        rec["profile_cap"] = q.get("profile")
        ev = q["events"]
        rec["failed_events"] = {e["event_index"] for e in ev if e["status"] == "failed"}
        rec["discharge_tracking_ok"] = q["status"] == "checked" and bool(ev) and not rec["failed_events"]
        rec["run_in_progress"] = bool(
            r["timestamp"] == newest and profile_max and r["run_duration_s"] < profile_max * 0.99
        )
        for name, c in q["cranks"].items():
            d_i = c["I_peak_A"] - c["I_pre_A"]
            if c["V_pre"] is not None and d_i > 1.0:
                rec[f"crank_{name}_R_int_deltaI_mohm"] = (c["V_pre"] - c["V_min"]) / d_i * 1000
        records.append(rec)
    extra = pd.DataFrame(records, index=df.index)
    return pd.concat([df, extra], axis=1)


def analysis_view(df: pd.DataFrame, include_invalid: bool) -> pd.DataFrame:
    """Runs used for trends and correlations: never a run still being written,
    and by default with every feature of a failed event blanked. Rows stay, so
    voltage- and charge-side features of those runs remain usable."""
    if df.empty:
        return df
    out = df[~df["run_in_progress"]].copy()
    if not include_invalid:
        for i, r in out.iterrows():
            cols = _invalid_columns(out.columns, r["failed_events"])
            if not r["discharge_tracking_ok"] and r["profile_cap"] is None:
                cols = _invalid_columns(out.columns, set(range(13)))
            if cols:
                out.loc[i, cols] = np.nan
    return out.drop(columns=["failed_events"])


def build_tracking_table(path: str) -> pd.DataFrame:
    cols = ["event_index", "event", "status", "sp_median_A", "meas_median_A", "sp_peak_A", "meas_peak_A", "error_A", "v_min_V"]
    return pd.DataFrame(run_quality(path)["events"], columns=cols)


_TRACK_LEVEL = {"ok": 0, "voltage_limited": 1, "failed": 2}


def build_tracking_overview(df: pd.DataFrame) -> go.Figure:
    """One row per run, one column per discharge event. The cell shows measured
    median current, colored by status, so a frozen sink stands out at a glance."""
    if df.empty:
        return go.Figure().update_layout(title="No test-day runs yet", height=320, template="plotly_white")
    sub = df.sort_values(["block", "target_soc_pct"], ascending=[True, False])
    labels, z, text, x_labels = [], [], [], {}
    for _, r in sub.iterrows():
        evs = {e["event_index"]: e for e in run_quality(os.path.join(LOG_DIR, r["filename"]))["events"]}
        x_labels.update({k: f'{k} {e["event"]}' for k, e in evs.items()})
        labels.append(f'B{int(r["block"])} SoC {r["soc_pct"]:.0f}%' if pd.notna(r["soc_pct"]) else r["filename"][:24])
        z.append(evs)
    keys = sorted(x_labels)
    if not keys:
        return go.Figure().update_layout(title="Profile not recognised for these runs", height=320, template="plotly_white")
    matrix = [[_TRACK_LEVEL[row[k]["status"]] if k in row else None for k in keys] for row in z]
    text = [[f'{row[k]["meas_median_A"]:.1f}' if k in row else "" for k in keys] for row in z]
    fig = go.Figure(go.Heatmap(
        z=matrix, x=[x_labels[k] for k in keys], y=labels, text=text, texttemplate="%{text}",
        zmin=0, zmax=2, showscale=False, xgap=1, ygap=1,
        colorscale=[[0.0, "#bfe6bf"], [0.5, _STATUS_WARNING], [1.0, "#eda3a3"]],
        hovertemplate="%{y}<br>%{x}<br>measured median %{text} A<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Measured median current per discharge event [A]: green ok, yellow voltage-limited, red not following", x=0.02, xanchor="left"),
        template="plotly_white", height=max(320, 22 * len(labels) + 130),
        margin=dict(l=110, r=30, t=50, b=80), yaxis=dict(autorange="reversed"), xaxis=dict(tickangle=-20),
    )
    return fig


def build_setpoint_fig(path: str) -> go.Figure:
    try:
        df = pd.read_csv(path, on_bad_lines="skip")
    except Exception:
        df = pd.DataFrame()
    profile = _PROFILES.get(str(df["Profile_Hash"].iloc[0])) if not df.empty and "Profile_Hash" in df else None
    if profile is None:
        return go.Figure().update_layout(title="Profile for this run not found", height=360, template="plotly_white")
    sp = _setpoint_at(df["Elapsed_s"].to_numpy(), profile[0], profile[1])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["Elapsed_s"], y=sp, mode="lines", name="Setpoint", line=dict(color="#898781", width=1.5, dash="dot")))
    fig.add_trace(go.Scatter(x=df["Elapsed_s"], y=df["Current"], mode="lines", name="Measured", line=dict(color=_COLORS[7], width=1.5)))
    fig.update_xaxes(title_text="Elapsed [s]")
    fig.update_yaxes(title_text="Current [A]")
    fig.update_layout(height=380, template="plotly_white", title=f"Setpoint vs measured ({profile[2]})",
                      legend=dict(orientation="h", y=-0.2), margin=dict(t=50, b=40))
    return fig


# ── Sweep quality ────────────────────────────────────────────────────────────

def _file_ah_removed(path: str) -> float:
    """Ah removed according to the logged samples, integrated over real elapsed time."""
    def _impl():
        try:
            df = pd.read_csv(path, usecols=["Elapsed_s", "Current"], on_bad_lines="skip")
        except Exception:
            return 0.0
        dt = df["Elapsed_s"].diff().clip(0, 10).fillna(0)
        return float(-(df["Current"] * dt).sum() / 3600)
    return v2feat.cached_call("unit3_file_ah", (path, v2feat._fingerprint(path)), _impl)


def add_log_based_soc(sweep: pd.DataFrame, log_soh: pd.DataFrame) -> pd.DataFrame:
    """Independent check on soc_cc_pct: integrate the step-down and test-day logs
    ourselves and divide by the C/5 integrated the same way. The Pi books the Ah
    it asked for against its own counter; comparing with a mixed reference
    (log Ah over Pi C/5) would show a false ~1 %-point drift per ten steps."""
    if sweep.empty:
        return sweep
    out = sweep.copy()
    c5 = log_soh.set_index("block")["block_c5_capacity_ah"].to_dict() if not log_soh.empty else {}
    files = os.listdir(LOG_DIR)
    soc = []
    for block, g in out.groupby("block", sort=False):
        removed = 0.0
        for _, r in g.sort_values("run").iterrows():
            prefix = str(r.get("testday_file", "")).split("_testday_")[0]
            steps = sorted(f for f in files if f.startswith(prefix + "_stepdown_bdps_"))
            if steps:
                removed += _file_ah_removed(os.path.join(LOG_DIR, steps[-1]))
            cap = c5.get(block)
            soc.append(round(100 * (1 - removed / cap), 2) if cap else None)
            day = os.path.join(LOG_DIR, str(r.get("testday_file", "")))
            if os.path.exists(day):
                removed += _file_ah_removed(day)
    out = out.sort_values(["block", "run"]).reset_index(drop=True)
    out["soc_log_pct"] = soc
    return out


def build_sweep_quality_fig(sweep: pd.DataFrame) -> go.Figure:
    fig = make_subplots(rows=1, cols=3, subplot_titles=[
        "Achieved SoC vs target", "OCV after rest vs SoC", "Net Ah per profile run vs SoC",
    ], horizontal_spacing=0.08)
    if sweep.empty:
        return fig.update_layout(title="No sweep points yet", height=430, template="plotly_white")
    blocks = sorted(sweep["block"].unique())
    colors = _ordinal_ramp(len(blocks))
    fig.add_trace(go.Scatter(x=[0, 100], y=[0, 100], mode="lines", line=dict(color="#c3c2b7", dash="dot"),
                             showlegend=False, hoverinfo="skip"), row=1, col=1)
    for b, color in zip(blocks, colors):
        g = sweep[sweep["block"] == b].sort_values("run")
        kw = dict(mode="markers+lines", line=dict(color=color), marker=dict(color=color, size=7))
        fig.add_trace(go.Scatter(x=g["target_soc_pct"], y=g["soc_cc_pct"], name=f"Block {b}", **kw), row=1, col=1)
        fig.add_trace(go.Scatter(x=g["soc_cc_pct"], y=g["ocv_after_rest"], showlegend=False, **kw), row=1, col=2)
        fig.add_trace(go.Scatter(x=g["soc_cc_pct"], y=g["profile_net_ah"], showlegend=False, **kw), row=1, col=3)
        hit = g[g["step_hit_cutoff"].astype(str).str.lower() == "true"]
        if not hit.empty:
            fig.add_trace(go.Scatter(x=hit["target_soc_pct"], y=hit["soc_cc_pct"], mode="markers", showlegend=False,
                                     marker=dict(color=_STATUS_CRITICAL, symbol="x", size=11)), row=1, col=1)
    fig.update_xaxes(title_text="Target SoC [%]", row=1, col=1)
    fig.update_yaxes(title_text="SoC per Pi [%]", row=1, col=1)
    fig.update_xaxes(title_text="SoC [%]", row=1, col=2)
    fig.update_yaxes(title_text="OCV [V]", row=1, col=2)
    fig.update_xaxes(title_text="SoC [%]", row=1, col=3)
    fig.update_yaxes(title_text="Net Ah", row=1, col=3)
    fig.update_layout(height=440, template="plotly_white", legend=dict(orientation="h", y=-0.2), margin=dict(t=50))
    return fig


# ── Campaign overview ────────────────────────────────────────────────────────

def build_campaign_fig(table: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if table.empty:
        return fig.update_layout(title="No C/5 measurement yet", height=420, template="plotly_white")
    pi = table.dropna(subset=["pi_soh_pct"])
    log = table.dropna(subset=["log_soh_pct"])
    log = log[log["final"]]
    fig.add_trace(go.Scatter(x=pi["block"], y=pi["pi_soh_pct"], mode="markers+lines", name="SOH, Pi (decides the stop)",
                             line=dict(color=_COLORS[0]), marker=dict(size=10, color=_COLORS[0])))
    fig.add_trace(go.Scatter(x=log["block"], y=log["log_soh_pct"], mode="markers", name="SOH, logs integrated here",
                             marker=dict(size=8, color=_COLORS[1], symbol="diamond-open")))
    fig.add_hline(y=SOH_STOP_PCT, line=dict(color=_STATUS_CRITICAL, dash="dash"),
                  annotation_text=f"Stop at {SOH_STOP_PCT:.0f} %", annotation_position="bottom right")
    fig.update_xaxes(title_text="Block #", dtick=1)
    fig.update_yaxes(title_text="C/5 capacity, % of Block 1", range=[min(40, float(pi["pi_soh_pct"].min()) - 5) if not pi.empty else 40, 112])
    fig.update_layout(height=420, template="plotly_white", title="SOH per block",
                      legend=dict(orientation="h", y=-0.18))
    return fig


def campaign_status_md(table: pd.DataFrame, events: pd.DataFrame) -> str:
    cp = _read_json("_checkpoint.json")
    ref = _read_json("_soh_reference.json")
    lines = []
    if cp:
        where = f'Block **{cp.get("block_index")}**, stage **{cp.get("stage")}**'
        if cp.get("cycle"):
            where += f', cycle {cp["cycle"]}'
        if cp.get("stage") == "soc_sweep" and cp.get("sweep_run"):
            where += f', sweep run {cp["sweep_run"]}/10'
        lines.append(f"- {where}, as of the last sync. Heartbeat in that copy: `{str(cp.get('last_alive'))[:16]}`.")
    else:
        lines.append("- No `_checkpoint.json` here: plan not started, or finished.")
    if ref:
        lines.append(f'- SOH reference: Block {ref.get("block")}, **{float(ref.get("capacity_ah")):.3f} Ah**.')
    stop = _projected_stop_block(table)
    if stop is not None:
        lines.append(f"- Linear estimate of reaching {SOH_STOP_PCT:.0f} %: around block **{stop:.0f}** (crude; capacity is not linear early on).")
    fixed = events[events["event"] == "profile_replay_fixed"] if not events.empty and "event" in events else events
    if not fixed.empty:
        lines.append(f'- Profile replay fixed on the Pi at `{str(fixed.iloc[0]["timestamp"])[:16]}`.')
    return "\n".join(lines)


# ── Run Inspector figure (event-shaded V/I traces) ───────────────────────────

def build_event_fig(bdps_path: str, sensor_path: str | None = None) -> go.Figure:
    df = v2feat.load_run(bdps_path, sensor_path)
    if df is None:
        fig = go.Figure()
        fig.update_layout(title="This file does not have the v2 event schema.", height=300)
        return fig

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=["Voltage", "Current"], row_heights=[0.55, 0.45],
    )
    seen_types = []
    for _, g in df.groupby("Event_Index", sort=True):
        etype = g["Event_Type"].iloc[0]
        color = _EVENT_COLORS.get(etype, "#eeeeee")
        t0, t1 = g["Elapsed_s"].min(), g["Elapsed_s"].max()
        for row in (1, 2):
            fig.add_vrect(x0=t0, x1=t1, fillcolor=color, opacity=0.25, line_width=0, row=row, col=1)
        if etype not in seen_types:
            seen_types.append(etype)

    fig.add_trace(go.Scatter(
        x=df["Elapsed_s"], y=df["Voltage"], mode="lines",
        name="Voltage", line=dict(color=_COLORS[0], width=1.5),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df["Elapsed_s"], y=df["Current"], mode="lines",
        name="Current", line=dict(color=_COLORS[7], width=1.5), showlegend=False,
    ), row=2, col=1)
    profile = _PROFILES.get(str(df["Profile_Hash"].iloc[0]))
    if profile is not None:
        fig.add_trace(go.Scatter(
            x=df["Elapsed_s"], y=_setpoint_at(df["Elapsed_s"].to_numpy(), profile[0], profile[1]),
            mode="lines", name="Setpoint", line=dict(color="#898781", width=1, dash="dot"),
        ), row=2, col=1)

    for etype in seen_types:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=10, color=_EVENT_COLORS.get(etype, "#eeeeee")),
            name=etype,
        ), row=1, col=1)

    fig.update_yaxes(title_text="Voltage [V]", row=1, col=1)
    fig.update_yaxes(title_text="Current [A]", row=2, col=1)
    fig.update_xaxes(title_text="Elapsed [s]", row=2, col=1)
    fig.update_layout(
        height=580, template="plotly_white",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(t=50, b=40),
    )
    return fig


def build_feature_table(bdps_path: str, sensor_path: str | None = None) -> pd.DataFrame:
    df = v2feat.load_run(bdps_path, sensor_path)
    if df is None:
        return pd.DataFrame(columns=["feature", "value"])
    feats = v2feat.extract_features(df)
    return pd.DataFrame(sorted(feats.items()), columns=["feature", "value"])


def _run_label(r: dict) -> str:
    soc = f"SoC {r['target_soc']}%  ·  " if r.get("target_soc") is not None else ""
    return f"Block {r['block']}  ·  {soc}OCV {r['ocv_label']:.2f}V  ·  {r['ts']}"


# ── Compare Runs figure ────────────────────────────────────────────────────────

def build_compare_fig(labels: list, run_options: dict) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=["Voltage", "Current"], row_heights=[0.55, 0.45],
    )
    for i, label in enumerate(labels):
        info = run_options.get(label)
        if info is None:
            continue
        df = v2feat.load_run(info["bdps_path"])
        if df is None:
            continue
        color = _COLORS[i % len(_COLORS)]
        name = _run_label(info)
        fig.add_trace(go.Scatter(x=df["Elapsed_s"], y=df["Voltage"], mode="lines",
                                  name=name, line=dict(color=color)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df["Elapsed_s"], y=df["Current"], mode="lines",
                                  showlegend=False, line=dict(color=color)), row=2, col=1)
    fig.update_yaxes(title_text="Voltage [V]", row=1, col=1)
    fig.update_yaxes(title_text="Current [A]", row=2, col=1)
    fig.update_xaxes(title_text="Elapsed [s]", row=2, col=1)
    fig.update_layout(height=580, template="plotly_white", legend=dict(orientation="h", y=-0.15))
    return fig


# ── Feature Correlations ───────────────────────────────────────────────────────
_CORR_EXCLUDE_COLS = {"battery_id", "kind", "timestamp", "filename", "soc_source", "profile_cap", "failed_events",
                      "run_in_progress", "discharge_tracking_ok", "target_soc_pct"}


def _corr_columns(df: pd.DataFrame) -> list:
    """Numeric, non-identifier columns with actual variance in df. A
    constant column (e.g. n_events/cutoff_hit are the same value for every
    row once filtered to "clean" runs) has an undefined (NaN) correlation
    with everything, including itself — dropped here rather than left to
    show up as blank NaN rows/columns in the heatmap."""
    return [
        c for c in df.columns
        if c not in _CORR_EXCLUDE_COLS
        and pd.api.types.is_numeric_dtype(df[c])
        and df[c].nunique(dropna=True) > 1
    ]


def build_run_corr_heatmap(df: pd.DataFrame, cols: list) -> go.Figure:
    """Spearman correlation matrix across run-level test-day features."""
    available = [
        c for c in cols
        if c in df.columns and df[c].notna().sum() > 3 and df[c].nunique(dropna=True) > 1
    ]
    if len(available) < 2:
        return go.Figure().update_layout(title="Not enough data yet", height=520)
    sub = df[available].apply(pd.to_numeric, errors="coerce")
    corr = sub.corr(method="spearman").round(2)
    z = corr.values
    labels = corr.columns.tolist()
    # A pair can still be individually undefined even after dropping globally
    # constant columns — e.g. in "include all runs" mode, Block 5's corrupted
    # event structure means a column like crank_cold_V_pre is only populated
    # for clean rows, so correlating it against cutoff_hit hits a subset
    # where cutoff_hit has no variance. Genuinely undefined, not a bug —
    # shown as a blank cell rather than the literal text "nan".
    text = [[("" if pd.isna(v) else f"{v:.2f}") for v in row] for row in z]
    hover_r = [[("not enough overlapping data" if pd.isna(v) else f"{v:.3f}") for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=labels, y=labels, text=text, texttemplate="%{text}",
        textfont=dict(size=9),
        colorscale=_DIVERGING_SCALE, zmid=0, zmin=-1, zmax=1,
        colorbar=dict(title="Spearman r", thickness=14, len=0.9),
        customdata=hover_r,
        hovertemplate="<b>%{y}</b> vs <b>%{x}</b><br>r = %{customdata}<extra></extra>",
        xgap=1, ygap=1,
    ))
    fig.update_layout(
        title=dict(text="Spearman correlation matrix — test-day run features", x=0.02, xanchor="left"),
        template="plotly_white",
        height=max(560, 22 * len(labels)), margin=dict(l=170, r=60, t=50, b=170),
        xaxis=dict(tickangle=-40), yaxis=dict(autorange="reversed"),
    )
    return fig


def build_scatter_pair(df: pd.DataFrame, x_col: str, y_col: str, color_col: str = "block") -> go.Figure:
    """Scatter of two features, coloured by a third (block by default).
    Every point's hover shows the source filename, block, starting OCV and
    SoC% — the run identity behind that dot — regardless of which columns
    are plotted or used for colour."""
    if x_col not in df.columns or y_col not in df.columns:
        return go.Figure().update_layout(title="Column not found")

    id_cols = ["filename", "block", "ocv_label_V", "soc_pct"]
    want_cols = list(dict.fromkeys(
        [x_col, y_col] + [c for c in id_cols if c in df.columns]
        + ([color_col] if color_col in df.columns else [])
    ))
    sub = df[want_cols].dropna(subset=[x_col, y_col])
    if sub.empty:
        return go.Figure().update_layout(title="No data")

    fname = sub["filename"] if "filename" in sub.columns else pd.Series("n/a", index=sub.index)
    block = sub["block"] if "block" in sub.columns else pd.Series(np.nan, index=sub.index)
    ocv = sub["ocv_label_V"] if "ocv_label_V" in sub.columns else pd.Series(np.nan, index=sub.index)
    soc = sub["soc_pct"] if "soc_pct" in sub.columns else pd.Series(np.nan, index=sub.index)
    customdata = np.stack([
        fname.astype(str).values,
        block.map(lambda v: "n/a" if pd.isna(v) else f"{int(v)}").values,
        ocv.map(lambda v: "n/a" if pd.isna(v) else f"{v:.2f}").values,
        soc.map(lambda v: "n/a" if pd.isna(v) else f"{v:.0f}").values,
    ], axis=-1)
    id_line = (
        "<b>%{customdata[0]}</b><br>"
        "Block %{customdata[1]}  ·  OCV %{customdata[2]} V  ·  SoC ≈ %{customdata[3]}%<br>"
    )

    data_traces = []
    discrete_legend = False
    if color_col in sub.columns:
        color_vals = pd.to_numeric(sub[color_col], errors="coerce")
        n_unique = color_vals.nunique(dropna=True)
        if 0 < n_unique <= _MAX_ORDINAL_VALUES:
            # Discrete/ordinal identity (block, cycle, soc_pct, ...) — one
            # trace per value, each with its own ordinal-ramp step and a
            # real legend entry, rather than a continuous colorbar the
            # reader can't actually read a specific value off of for a
            # handful of discrete groups. See _ORDINAL_STEPS above.
            uniq = np.sort(color_vals.dropna().unique())
            step_colors = _ordinal_ramp(len(uniq))
            for v, color_hex in zip(uniq, step_colors):
                mask = (color_vals == v).to_numpy()
                label = f"{int(v)}" if float(v).is_integer() else f"{v:g}"
                htmpl = id_line + f"{x_col} = %{{x}}<br>{y_col} = %{{y}}<extra>{color_col} = {label}</extra>"
                data_traces.append(go.Scatter(
                    x=sub[x_col][mask], y=sub[y_col][mask], mode="markers",
                    marker=dict(
                        color=color_hex, size=10, opacity=0.9,
                        line=dict(width=1, color="rgba(11,11,11,0.35)"),
                    ),
                    customdata=customdata[mask], hovertemplate=htmpl,
                    name=f"{color_col} {label}", showlegend=True,
                ))
            nan_mask = color_vals.isna().to_numpy()
            if nan_mask.any():
                htmpl = id_line + f"{x_col} = %{{x}}<br>{y_col} = %{{y}}<extra>{color_col} = n/a</extra>"
                data_traces.append(go.Scatter(
                    x=sub[x_col][nan_mask], y=sub[y_col][nan_mask], mode="markers",
                    marker=dict(color="#c3c2b7", size=10, opacity=0.6, line=dict(width=1, color="rgba(11,11,11,0.35)")),
                    customdata=customdata[nan_mask], hovertemplate=htmpl,
                    name=f"{color_col} n/a", showlegend=True,
                ))
            discrete_legend = True
        else:
            marker = dict(
                color=color_vals, colorscale=_SEQUENTIAL_BLUE, showscale=True,
                colorbar=dict(title=color_col, thickness=14, len=0.75),
                size=10, opacity=0.85, line=dict(width=1, color="white"),
            )
            htmpl = id_line + f"{x_col} = %{{x}}<br>{y_col} = %{{y}}<br>{color_col} = %{{marker.color}}<extra></extra>"
            data_traces.append(go.Scatter(
                x=sub[x_col], y=sub[y_col], mode="markers", marker=marker,
                customdata=customdata, hovertemplate=htmpl, name="data",
            ))
    else:
        marker = dict(size=10, color=_COLORS[0], opacity=0.85, line=dict(width=1, color="white"))
        htmpl = id_line + f"{x_col} = %{{x}}<br>{y_col} = %{{y}}<extra></extra>"
        data_traces.append(go.Scatter(
            x=sub[x_col], y=sub[y_col], mode="markers", marker=marker,
            customdata=customdata, hovertemplate=htmpl, name="data",
        ))

    x_n = pd.to_numeric(sub[x_col], errors="coerce")
    y_n = pd.to_numeric(sub[y_col], errors="coerce")
    valid = np.isfinite(x_n) & np.isfinite(y_n)
    traces = list(data_traces)
    if valid.sum() >= 3:
        m, b = np.polyfit(x_n[valid], y_n[valid], 1)
        x_fit = np.linspace(x_n[valid].min(), x_n[valid].max(), 100)
        r = np.corrcoef(x_n[valid], y_n[valid])[0, 1]
        traces.append(go.Scatter(
            x=x_fit, y=m * x_fit + b, mode="lines",
            line=dict(color=_COLORS[7], dash="dash", width=1.5),
            name=f"trend  r={r:.2f}", showlegend=True,
        ))
    fig = go.Figure(traces)
    # A discrete colour grouping needs a real (scrollable, right-side)
    # legend for up to _MAX_ORDINAL_VALUES entries; the old top horizontal
    # strip only fit a couple of items.
    legend = (
        dict(title=color_col, orientation="v")
        if discrete_legend
        else dict(orientation="h", y=1.1)
    )
    fig.update_layout(
        title=dict(text=f"{y_col}  vs  {x_col}", x=0.02, xanchor="left"),
        xaxis_title=x_col, yaxis_title=y_col,
        template="plotly_white", height=460, margin=dict(l=60, r=40, t=60, b=50),
        legend=legend,
    )
    return fig


def build_block_predictor_table(dataset: pd.DataFrame, soh_df: pd.DataFrame) -> pd.DataFrame:
    """For each within-block test-day feature (mean over that block's clean,
    non-coarse-sampled runs), Spearman r against THIS block's own measured
    C/5 capacity and against the NEXT block's capacity. This is the core
    research question the v2 profile was designed for (see
    testday_v2_features.py's module docstring): the vehicle only ever
    observes OCV, never true SOH — a feature that predicts the *next*
    block's capacity ahead of time is a candidate field-deployable health
    indicator. Very small sample (one row per block) — exploratory only.

    soh_df comes from find_block_soh_df() rather than dataset's own
    block_c5_capacity_ah column, since SOH is measured before that block's
    test-day runs exist — a block with a fresh SOH reading but no test-day
    runs yet (e.g. right after `charge_full`) is still a valid "next block"
    target for the previous block's predictors, even though it has no rows
    in `dataset` at all.
    """
    cols = ["feature", "r_this_block", "n_this", "r_next_block", "n_next"]
    if dataset.empty or "cutoff_hit" not in dataset.columns or soh_df.empty:
        return pd.DataFrame(columns=cols)

    clean = dataset[~dataset["cutoff_hit"]]
    feat_cols = [
        c for c in _corr_columns(clean)
        if c not in {"block", "ocv_label_V", "soc_pct", "block_c5_capacity_ah",
                     "block_c5_reached_cutoff", "n_events", "cutoff_hit", "run_duration_s"}
    ]
    if clean.empty or not feat_cols:
        return pd.DataFrame(columns=cols)

    per_block = clean.groupby("block")[feat_cols].mean()
    valid_soh = soh_df[soh_df["reached_cutoff"]] if "reached_cutoff" in soh_df.columns else soh_df
    soh = valid_soh.drop_duplicates(subset=["block"]).set_index("block")["block_c5_capacity_ah"].sort_index()
    this_capacity = soh.reindex(per_block.index)
    next_capacity = pd.Series(soh.reindex(per_block.index + 1).values, index=per_block.index)

    rows = []
    for feat in feat_cols:
        x = per_block[feat]
        m_this = x.notna() & this_capacity.notna()
        m_next = x.notna() & next_capacity.notna()
        r_this = x[m_this].corr(this_capacity[m_this], method="spearman") if m_this.sum() >= 3 else None
        r_next = x[m_next].corr(next_capacity[m_next], method="spearman") if m_next.sum() >= 3 else None
        if r_this is None and r_next is None:
            continue
        rows.append({
            "feature": feat,
            "r_this_block": round(r_this, 3) if r_this is not None else None,
            "n_this": int(m_this.sum()),
            "r_next_block": round(r_next, 3) if r_next is not None else None,
            "n_next": int(m_next.sum()),
        })
    out = pd.DataFrame(rows, columns=cols)
    if not out.empty:
        out = out.reindex(out["r_next_block"].abs().sort_values(ascending=False, na_position="last").index)
        out = out.reset_index(drop=True)
    return out


def build_soc_robust_indicator_table(dataset: pd.DataFrame, soh_df: pd.DataFrame) -> pd.DataFrame:
    """Rank features by how well they track SOH *without* just being a proxy
    for SoC — the property a real field-deployable indicator needs, since a
    vehicle can't control what SoC it happens to measure at.

    For each feature: `r_soc` = Spearman r against soc_pct across all clean
    runs (SoC set-points repeat every block, so this isolates genuine
    SoC-dependence rather than a block/degradation confound); `r_next_block`
    = Spearman r between that block's mean feature value and the *next*
    block's measured capacity (from build_block_predictor_table). `quality`
    = |r_next_block| − |r_soc|: high when a feature strongly tracks future
    SOH while barely moving with SoC. Absolute voltage-level features
    (OCV/rest/recovery/crank V_pre, …) score poorly here almost by
    construction — voltage directly encodes charge state for a lead-acid
    cell, so they're highly SoC-coupled regardless of degradation.
    Resistance-derived features (crank/wakeup/ramp R_int) are the physically
    expected winners: they reflect internal impedance, not charge level.
    """
    cols = ["feature", "r_soc", "r_this_block", "r_next_block", "n_next", "quality"]
    predictors = build_block_predictor_table(dataset, soh_df)
    if predictors.empty or dataset.empty or "cutoff_hit" not in dataset.columns:
        return pd.DataFrame(columns=cols)

    clean = dataset[~dataset["cutoff_hit"]]
    if "soc_pct" not in clean.columns:
        return pd.DataFrame(columns=cols)

    r_soc = {}
    for feat in predictors["feature"]:
        sub = clean[[feat, "soc_pct"]].dropna()
        if len(sub) >= 5:
            r_soc[feat] = sub[feat].corr(sub["soc_pct"], method="spearman")

    out = predictors[predictors["feature"].isin(r_soc)].copy()
    out["r_soc"] = out["feature"].map(r_soc).round(3)
    out["quality"] = (out["r_next_block"].abs() - out["r_soc"].abs()).round(3)
    out = out[cols].sort_values("quality", ascending=False).reset_index(drop=True)
    return out


# ── SoC Sweep / Degradation Trends / SOH figures ──────────────────────────────

def _feature_options(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    return [
        c for c in df.columns
        if c not in _META_COLS and pd.api.types.is_numeric_dtype(df[c])
    ]


def build_sweep_fig(df: pd.DataFrame, feature: str) -> go.Figure:
    fig = go.Figure()
    if df.empty or feature not in df.columns or df[feature].dropna().empty:
        fig.update_layout(title="No valid data for this feature yet", height=420, template="plotly_white")
        return fig
    sub = df.dropna(subset=[feature, "soc_pct"])
    blocks = sorted(sub["block"].unique())
    for b, color in zip(blocks, _ordinal_ramp(len(blocks))):
        g = sub[sub["block"] == b].sort_values("soc_pct")
        fig.add_trace(go.Scatter(
            x=g["soc_pct"], y=g[feature], mode="markers+lines", name=f"Block {b}",
            line=dict(color=color), marker=dict(size=9, color=color),
            customdata=g["filename"], hovertemplate="%{customdata}<br>SoC %{x:.1f} %<br>%{y}<extra></extra>",
        ))
    fig.update_xaxes(title_text="SoC, Coulomb-counted [%]")
    fig.update_yaxes(title_text=feature)
    fig.update_layout(height=420, template="plotly_white", title=f"{feature} vs SoC, per block",
                      legend=dict(orientation="v"))
    return fig


def build_trend_fig(df: pd.DataFrame, feature: str, soc=None) -> go.Figure:
    """Feature per block. Averaging across SoC only compares blocks fairly when
    each has the same points, so a single SoC can be selected instead."""
    fig = go.Figure()
    if df.empty or feature not in df.columns or df[feature].dropna().empty:
        fig.update_layout(title="No valid data for this feature yet", height=420, template="plotly_white")
        return fig
    sub = df.dropna(subset=[feature])
    if soc is not None:
        sub = sub[sub["target_soc_pct"] == soc]
    agg = sub.groupby("block")[feature].mean().reset_index()
    fig.add_trace(go.Scatter(
        x=agg["block"], y=agg[feature], mode="markers+lines",
        marker=dict(size=10, color=_COLORS[0]),
    ))
    n_blocks = agg["block"].nunique()
    note = "" if n_blocks > 1 else "  (one valid block so far)"
    where = f"at SoC {soc} %" if soc is not None else "mean across SoC"
    fig.update_xaxes(title_text="Block #", dtick=1)
    fig.update_yaxes(title_text=feature)
    fig.update_layout(height=420, template="plotly_white", title=f"{feature} vs block, {where}{note}")
    return fig


def build_soh_fig(soh: pd.DataFrame, nominal_ah: float = NOMINAL_AH) -> go.Figure:
    """soh: tidy (block, block_c5_capacity_ah, reached_cutoff) frame from
    testday_v2_features.find_block_soh_df() — independent of whether that
    block has any test-day runs yet, since SOH is measured before the
    SoC-sweep starts.

    A discharge that hasn't reached the real cutoff voltage yet most likely
    means the C/5 test was still running on the Pi at the moment this file
    was synced (e.g. Block 6 read 12.07 V at sync time — every *completed*
    discharge so far ends at ~10.78-10.79 V). Its Ah is just a snapshot
    lower bound, not the final SOH reading, so it's plotted as a separate,
    disconnected series rather than joined into the trend line — rescan
    once it finishes to get the real value.
    """
    fig = go.Figure()
    if soh.empty:
        fig.update_layout(title="No SOH (C/5) measurement yet", height=420, template="plotly_white")
        return fig
    valid = soh[soh["reached_cutoff"]] if "reached_cutoff" in soh.columns else soh
    invalid = soh[~soh["reached_cutoff"]] if "reached_cutoff" in soh.columns else soh.iloc[0:0]
    fig.add_trace(go.Scatter(
        x=valid["block"], y=valid["block_c5_capacity_ah"], mode="markers+lines",
        name="Measured C/5 Ah", line=dict(color=_COLORS[0]), marker=dict(size=10, color=_COLORS[0]),
    ))
    if not invalid.empty:
        fig.add_trace(go.Scatter(
            x=invalid["block"], y=invalid["block_c5_capacity_ah"], mode="markers",
            name="Not final yet — test likely still running (snapshot)",
            marker=dict(size=13, color=_STATUS_WARNING, symbol="x"),
        ))
    fig.add_hline(
        y=nominal_ah, line=dict(color="#898781", dash="dot"),
        annotation_text=f"Nameplate {nominal_ah:.0f} Ah (C/20)", annotation_position="top left",
    )
    fig.update_xaxes(title_text="Block #", dtick=1)
    fig.update_yaxes(title_text="C/5 capacity [Ah]")
    fig.update_layout(
        height=420, template="plotly_white",
        title="SOH History — measured C/5 discharge capacity per block",
        legend=dict(orientation="h", y=-0.18),
    )
    return fig


def build_degr_fig(degr_df: pd.DataFrame) -> go.Figure:
    fig = make_subplots(rows=1, cols=2, subplot_titles=["Ah per degradation cycle", "Coulombic efficiency"])
    if degr_df.empty:
        fig.update_layout(height=420, template="plotly_white", title="No degradation-cycle data yet")
        return fig
    # Cycle numbers restart per block, so plot in campaign order with a block.cycle label.
    degr_df = degr_df.sort_values(["block", "cycle"]).copy()
    degr_df["x"] = degr_df["block"].astype(str) + "." + degr_df["cycle"].astype(str)
    is_complete = degr_df["status"].str.startswith("complete")
    complete = degr_df[is_complete]
    partial = degr_df[~is_complete]
    full_dis = complete[~complete["first_after_sweep"]]
    after_sweep = complete[complete["first_after_sweep"]]

    fig.add_trace(go.Scatter(x=degr_df["x"], y=[None] * len(degr_df), mode="markers", showlegend=False, hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Scatter(x=degr_df["x"], y=[None] * len(degr_df), mode="markers", showlegend=False, hoverinfo="skip"), row=1, col=2)
    fig.add_trace(go.Scatter(x=full_dis["x"], y=full_dis["discharge_ah"], mode="markers+lines",
                              name="Discharge Ah", marker=dict(color=_COLORS[0]), line=dict(color=_COLORS[0])), row=1, col=1)
    fig.add_trace(go.Scatter(x=after_sweep["x"], y=after_sweep["discharge_ah"], mode="markers",
                              name="Discharge Ah, after the sweep", marker=dict(color=_COLORS[0], symbol="circle-open", size=9)), row=1, col=1)
    fig.add_trace(go.Scatter(x=complete["x"], y=complete["charge_ah"], mode="markers+lines",
                              name="Charge Ah", marker=dict(color=_COLORS[1]), line=dict(color=_COLORS[1])), row=1, col=1)
    if not partial.empty:
        fig.add_trace(go.Scatter(x=partial["x"], y=partial["discharge_ah"], mode="markers",
                                  name="In progress / fragment",
                                  marker=dict(color=_STATUS_WARNING, symbol="x", size=10)), row=1, col=1)
        fig.add_trace(go.Scatter(x=partial["x"], y=partial["charge_ah"], mode="markers",
                                  showlegend=False,
                                  marker=dict(color=_STATUS_WARNING, symbol="x", size=10)), row=1, col=1)
    fig.add_trace(go.Scatter(x=full_dis["x"], y=full_dis["coulombic_eff_pct"], mode="markers+lines",
                              marker=dict(color=_COLORS[0]), showlegend=False), row=1, col=2)

    fig.update_xaxes(title_text="Block.cycle", type="category", row=1, col=1)
    fig.update_xaxes(title_text="Block.cycle", type="category", row=1, col=2)
    fig.update_yaxes(title_text="Ah", row=1, col=1)
    fig.update_yaxes(title_text="Coulombic efficiency [%]", row=1, col=2)
    fig.update_layout(height=440, template="plotly_white", legend=dict(orientation="h", y=-0.2))
    return fig


# ── Export ─────────────────────────────────────────────────────────────────────

def _style_ws(ws) -> None:
    """Navy bold header row + auto column widths (matches battery_feature_dashboard.py)."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    fill = PatternFill("solid", fgColor="1F4E79")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    for col_cells in ws.columns:
        width = max((len(str(c.value or "")) for c in col_cells), default=8)
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(width + 3, 42)


def export_excel(out_dir: str, dataset: pd.DataFrame, degr_df: pd.DataFrame, soh: pd.DataFrame) -> str:
    """Raw features, including invalid ones: discharge_tracking_ok and the
    Setpoint Check say which to trust."""
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{BATTERY_ID}_export_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    out_path = os.path.join(out_dir, fname)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        dataset.drop(columns=["failed_events"], errors="ignore").to_excel(writer, sheet_name="Run Summary", index=False)
        _SWEEP_DF.to_excel(writer, sheet_name="Sweep Points", index=False)
        _SOH_TABLE.to_excel(writer, sheet_name="Block SOH", index=False)
        degr_df.to_excel(writer, sheet_name="Degradation Cycles", index=False)
        soh.to_excel(writer, sheet_name="SOH History", index=False)
        build_file_inventory(LOG_DIR).to_excel(writer, sheet_name="File Inventory", index=False)

    from openpyxl import load_workbook
    wb = load_workbook(out_path)
    for ws in wb.worksheets:
        if ws.max_row >= 1:
            _style_ws(ws)
    wb.save(out_path)
    return out_path


def _load_state() -> None:
    global _DATASET, _DEGR_DF, _SOH_DF, _SWEEP_DF, _SOH_TABLE, _EVENTS_DF, RUN_OPTIONS
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)  # first run before any sync: show empty tabs
    _SOH_DF = v2feat.find_block_soh_df(LOG_DIR, battery_id=BATTERY_ID)
    _SWEEP_DF = add_log_based_soc(load_sweep_points(), _SOH_DF)
    _DATASET = attach_quality(v2feat.build_dataset(LOG_DIR, battery_id=BATTERY_ID), _SWEEP_DF)
    _DEGR_DF = find_degr_cycles(LOG_DIR)
    cp = _read_json("_checkpoint.json")
    if not _DEGR_DF.empty and cp.get("stage") == "degradation_cycles":
        # The cycle the Pi is on now can look well-formed but still be mid-write.
        running = (_DEGR_DF["block"] == cp.get("block_index")) & (_DEGR_DF["cycle"] == cp.get("cycle"))
        _DEGR_DF.loc[running, "status"] = "in progress / fragment"
    _SOH_TABLE = build_soh_table(load_block_soh(), _SOH_DF)
    _EVENTS_DF = _read_bookkeeping_csv("_events.csv")
    RUN_OPTIONS = _run_options()
    v2feat.flush_cache()


def _run_options() -> dict:
    infos = [r for r in v2feat.find_v2_testday_runs(LOG_DIR) if r["ocv_label"] is not None]
    infos.sort(key=lambda r: (-(r["block"] or 0), -(r["target_soc"] or 0)))
    return {_run_label(r): r for r in infos}


# ── App state ──────────────────────────────────────────────────────────────────
_load_state()


# ── Widgets ────────────────────────────────────────────────────────────────────
stat_blocks = pn.indicators.Number(
    name="Blocks started", value=0, format="{value}",
    default_color=_COLORS[0], font_size="32pt", title_size="12pt", align="center",
)
stat_runs = pn.indicators.Number(
    name="Test-day runs", value=0, format="{value}",
    default_color=_COLORS[2], font_size="32pt", title_size="12pt", align="center",
)
stat_cycles = pn.indicators.Number(
    name="Degr. cycles complete", value=0, format="{value}",
    default_color=_STATUS_GOOD, font_size="32pt", title_size="12pt", align="center",
)
stat_soh = pn.indicators.Number(
    name="Latest SOH (Pi, of Block 1)", value=0, format="{value:.1f} %",
    default_color=_COLORS[0], font_size="32pt", title_size="12pt", align="center",
)
header_caption = pn.pane.Markdown("", sizing_mode="stretch_width")
rescan_btn = pn.widgets.Button(name="🔄  Rescan log folder", button_type="primary", width=200)

run_sel = pn.widgets.Select(name="Test-day run", options=list(RUN_OPTIONS), width=380)
run_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
run_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=400, sizing_mode="stretch_width")

compare_sel = pn.widgets.MultiSelect(
    name="Runs to compare  (Ctrl+click for multiple)",
    options=list(RUN_OPTIONS), size=10, width=420,
    value=list(RUN_OPTIONS)[:3],
)
compare_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")

sweep_feat_sel = pn.widgets.Select(name="Feature", options=_feature_options(_DATASET), width=320)
sweep_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
sweep_tbl = pn.widgets.Tabulator(_DATASET.drop(columns=["failed_events"], errors="ignore"), pagination="local", page_size=15, height=340, sizing_mode="stretch_width")

trend_feat_sel = pn.widgets.Select(name="Feature", options=_feature_options(_DATASET), width=320)
trend_soc_sel = pn.widgets.Select(name="SoC", options={"Mean across SoC": 0, **{f"{s} %": s for s in range(100, 0, -10)}}, value=0, width=180)
trend_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")

soh_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
soh_tbl = pn.widgets.Tabulator(_SOH_TABLE, height=300, sizing_mode="stretch_width")

campaign_status = pn.pane.Markdown("", sizing_mode="stretch_width")
campaign_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
events_tbl = pn.widgets.Tabulator(_EVENTS_DF, height=220, sizing_mode="stretch_width")

sweepq_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
sweepq_tbl = pn.widgets.Tabulator(_SWEEP_DF.drop(columns=["battery_id"], errors="ignore"), pagination="local", page_size=20, height=480, sizing_mode="stretch_width")

track_overview_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
track_run_sel = pn.widgets.Select(name="Test-day run", options=list(RUN_OPTIONS), width=460)
track_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
track_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=320, sizing_mode="stretch_width")

show_invalid_toggle = pn.widgets.Checkbox(name="Also show features from events that failed the setpoint check", value=False)
quality_banner = pn.pane.Alert("", alert_type="warning", sizing_mode="stretch_width")

degr_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
degr_tbl = pn.widgets.Tabulator(_DEGR_DF, pagination="local", page_size=15, height=340, sizing_mode="stretch_width")

inventory_tbl = pn.widgets.Tabulator(
    build_file_inventory(LOG_DIR), pagination="local", page_size=25,
    height=500, sizing_mode="stretch_width",
)

corr_clean_toggle = pn.widgets.Checkbox(name="Exclude cutoff runs (recommended)", value=True)
corr_status = pn.pane.Markdown("", sizing_mode="stretch_width")
corr_heatmap_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
corr_x_sel = pn.widgets.Select(name="X axis", options=[], width=260)
corr_y_sel = pn.widgets.Select(name="Y axis", options=[], width=260)
corr_color_sel = pn.widgets.Select(name="Colour by", options=[], width=260)
corr_scatter_pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")
predictor_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=380, sizing_mode="stretch_width")
soc_robust_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=380, sizing_mode="stretch_width")

export_dir_input = pn.widgets.TextInput(name="Export folder", value=os.path.join(BASE_DIR, "exports"), width=420)
export_btn = pn.widgets.Button(name="⬇  Export to Excel", button_type="primary", width=200)
export_status = pn.pane.Markdown("", sizing_mode="stretch_width")


def _update_header() -> None:
    blocks_seen = set(_DATASET["block"].unique()) if not _DATASET.empty else set()
    blocks_seen |= set(_SOH_DF["block"].unique()) if not _SOH_DF.empty else set()
    n_cycles = len(_DEGR_DF)
    n_complete_cycles = int(_DEGR_DF["status"].str.startswith("complete").sum()) if not _DEGR_DF.empty else 0
    n_skipped = _DATASET.attrs.get("n_skipped_legacy_schema", 0)
    known = _SOH_TABLE.dropna(subset=["pi_soh_pct"]) if not _SOH_TABLE.empty else _SOH_TABLE
    latest = float(known["pi_soh_pct"].iloc[-1]) if not known.empty else 0.0

    stat_blocks.value = len(blocks_seen)
    stat_runs.value = len(_DATASET)
    stat_cycles.value = n_complete_cycles
    stat_cycles.format = f"{{value}} / {n_cycles}"
    stat_soh.value = latest
    stat_soh.default_color = _STATUS_GOOD if latest >= 70 else (_STATUS_WARNING if latest > SOH_STOP_PCT else _STATUS_CRITICAL)

    header_caption.object = (
        f"**Battery: `{BATTERY_ID}`**"
        + (f"  ·  {n_skipped} legacy-schema file(s) skipped" if n_skipped else "")
        + f"  ·  data folder `{LOG_DIR}`  ·  *testing in progress: click Rescan after a sync*"
    )
    done = _DATASET[~_DATASET["run_in_progress"]] if not _DATASET.empty else _DATASET
    n_bad = int((~done["discharge_tracking_ok"]).sum()) if not done.empty else 0
    if n_bad:
        quality_banner.object = (
            f"**{n_bad} of {len(done)} finished run(s) failed the setpoint check**, normally the runs from before "
            "the Pi fix of 2026-11-02, when discharge current stayed frozen. Features from the failed events are "
            "hidden in SoC Sweep, Degradation Trends and Feature Correlations unless you tick the box below."
        )
        quality_banner.visible = True
    else:
        quality_banner.visible = False


_update_header()


# ── Callbacks ──────────────────────────────────────────────────────────────────

def _update_run_inspector(event=None):
    if not RUN_OPTIONS:
        return
    info = RUN_OPTIONS[run_sel.value]
    run_pane.object = build_event_fig(info["bdps_path"], info["sensor_path"])
    run_tbl.value = build_feature_table(info["bdps_path"], info["sensor_path"])


def _update_compare(event=None):
    compare_pane.object = build_compare_fig(compare_sel.value, RUN_OPTIONS)


def _view() -> pd.DataFrame:
    return analysis_view(_DATASET, show_invalid_toggle.value)


def _update_sweep(event=None):
    sweep_pane.object = build_sweep_fig(_view(), sweep_feat_sel.value)


def _update_trend(event=None):
    trend_pane.object = build_trend_fig(_view(), trend_feat_sel.value, trend_soc_sel.value or None)


def _corr_source_df() -> pd.DataFrame:
    df = _view()
    if corr_clean_toggle.value and "cutoff_hit" in df.columns:
        df = df[~df["cutoff_hit"]]
    return df


def _update_scatter(event=None):
    df = _corr_source_df()
    corr_scatter_pane.object = build_scatter_pair(df, corr_x_sel.value, corr_y_sel.value, color_col=corr_color_sel.value)


def _update_corr(event=None):
    df = _corr_source_df()
    cols = _corr_columns(df)
    corr_heatmap_pane.object = build_run_corr_heatmap(df, cols)
    corr_x_sel.options = cols
    corr_y_sel.options = cols
    corr_color_sel.options = cols
    if cols:
        if corr_x_sel.value not in cols:
            corr_x_sel.value = "soc_pct" if "soc_pct" in cols else cols[0]
        if corr_y_sel.value not in cols:
            r_cols = [c for c in cols if "R_int" in c]
            corr_y_sel.value = r_cols[0] if r_cols else cols[-1]
        if corr_color_sel.value not in cols:
            corr_color_sel.value = "block" if "block" in cols else cols[0]
    n_blocks = df["block"].nunique() if not df.empty else 0
    corr_status.object = (
        f"Showing **{len(df)} run(s)** across **{n_blocks} block(s)**"
        + (" (cutoff runs excluded)" if corr_clean_toggle.value else " (cutoff runs included)")
        + ("; features of failed events included" if show_invalid_toggle.value else "; features of failed events blanked")
    )
    _update_scatter()
    view = _view()
    predictor_tbl.value = build_block_predictor_table(view, _SOH_DF)
    soc_robust_tbl.value = build_soc_robust_indicator_table(view, _SOH_DF)


def _update_campaign(event=None):
    campaign_status.object = campaign_status_md(_SOH_TABLE, _EVENTS_DF)
    campaign_pane.object = build_campaign_fig(_SOH_TABLE)
    events_tbl.value = _EVENTS_DF


def _update_track_run(event=None):
    if not RUN_OPTIONS or track_run_sel.value not in RUN_OPTIONS:
        return
    path = RUN_OPTIONS[track_run_sel.value]["bdps_path"]
    track_pane.object = build_setpoint_fig(path)
    track_tbl.value = build_tracking_table(path)


def _update_tracking(event=None):
    track_overview_pane.object = build_tracking_overview(_DATASET)
    _update_track_run()


def _on_invalid_toggle(event=None):
    _update_sweep()
    _update_trend()
    if _lazy_loaded[_CORR_TAB_INDEX]:
        _update_corr()


def _rescan(event=None):
    _load_state()

    run_sel.options = list(RUN_OPTIONS)
    if RUN_OPTIONS and run_sel.value not in RUN_OPTIONS:
        run_sel.value = list(RUN_OPTIONS)[0]
    compare_sel.options = list(RUN_OPTIONS)
    track_run_sel.options = list(RUN_OPTIONS)
    if RUN_OPTIONS and track_run_sel.value not in RUN_OPTIONS:
        track_run_sel.value = list(RUN_OPTIONS)[0]

    feat_opts = _feature_options(_DATASET)
    sweep_feat_sel.options = feat_opts
    trend_feat_sel.options = feat_opts

    sweep_tbl.value = _DATASET.drop(columns=["failed_events"], errors="ignore")
    sweepq_tbl.value = _SWEEP_DF.drop(columns=["battery_id"], errors="ignore")
    soh_tbl.value = _SOH_TABLE
    degr_tbl.value = _DEGR_DF
    inventory_tbl.value = build_file_inventory(LOG_DIR)

    _update_header()
    _update_run_inspector()
    _update_compare()
    _update_sweep()
    _update_trend()
    _update_corr()
    _update_campaign()
    _update_tracking()
    sweepq_pane.object = build_sweep_quality_fig(_SWEEP_DF)
    soh_pane.object = build_soh_fig(_SOH_DF)
    degr_pane.object = build_degr_fig(_DEGR_DF)


def _run_export(event=None):
    try:
        path = export_excel(export_dir_input.value, _DATASET, _DEGR_DF, _SOH_DF)
        export_status.object = f"Saved: `{path}`"
    except Exception as e:
        export_status.object = f"Export failed: {e}"


run_sel.param.watch(_update_run_inspector, "value")
compare_sel.param.watch(_update_compare, "value")
sweep_feat_sel.param.watch(_update_sweep, "value")
trend_feat_sel.param.watch(_update_trend, "value")
trend_soc_sel.param.watch(_update_trend, "value")
track_run_sel.param.watch(_update_track_run, "value")
show_invalid_toggle.param.watch(_on_invalid_toggle, "value")
corr_clean_toggle.param.watch(_update_corr, "value")
corr_x_sel.param.watch(_update_scatter, "value")
corr_y_sel.param.watch(_update_scatter, "value")
corr_color_sel.param.watch(_update_scatter, "value")
rescan_btn.on_click(_rescan)
export_btn.on_click(_run_export)

if RUN_OPTIONS:
    run_sel.value = list(RUN_OPTIONS)[0]
# Compare Runs and Feature Correlations are deferred to first view, see "Lazy
# sub-tab loading" below.
if RUN_OPTIONS:
    track_run_sel.value = list(RUN_OPTIONS)[0]
_update_run_inspector()
_update_campaign()
_update_tracking()
sweepq_pane.object = build_sweep_quality_fig(_SWEEP_DF)
_update_sweep()
_update_trend()
soh_pane.object = build_soh_fig(_SOH_DF)
degr_pane.object = build_degr_fig(_DEGR_DF)


# ── Layout ─────────────────────────────────────────────────────────────────────
campaign_tab = pn.Column(
    pn.pane.Markdown("## Campaign"),
    campaign_status, campaign_pane,
    pn.pane.Markdown("### Per block"),
    soh_tbl,
    pn.pane.Markdown("### Interruptions and markers (`_events.csv`)"),
    events_tbl,
    sizing_mode="stretch_width",
)

sweepq_tab = pn.Column(
    pn.pane.Markdown("## Sweep Quality"),
    pn.pane.Markdown(
        "Does each run start at the intended SoC? `soc_cc_pct` is the Pi's own count. `soc_log_pct` is an "
        "independent check: the step-down and test-day logs integrated here, over the C/5 integrated the same "
        "way. The OCV curve per block shows how the OCV–SoC relation moves with ageing. A red ✕ marks a "
        "step-down that hit the cutoff before reaching its target."
    ),
    sweepq_pane, sweepq_tbl,
    sizing_mode="stretch_width",
)

track_tab = pn.Column(
    pn.pane.Markdown("## Setpoint Check"),
    pn.pane.Markdown(
        "Measured current against the profile each run really replayed (matched on `Profile_Hash`), over the "
        "samples with a discharge setpoint. **ok**: median within 10 % (at least 0.2 A), the same rule as the Pi's "
        "check. **voltage_limited**: not following, but the voltage reached the 10.2 V floor, so this is the "
        "battery and not the control. **failed**: not following, for no such reason.\n\n"
        "Medians don't see short peaks: compare `sp_peak_A` and `meas_peak_A`. The hot crank reaches about "
        "−35.6 A instead of −55 A, because the output is off before it and current needs ~0.1 s to rise. That "
        "is known and reproducible, not a failure; features use the measured current."
    ),
    track_overview_pane,
    track_run_sel, track_pane, track_tbl,
    sizing_mode="stretch_width",
)

inspector_tab = pn.Column(
    pn.pane.Markdown("## Test Run Inspector"),
    pn.pane.Markdown(
        "Voltage/current for one test-day run, shaded by scripted event "
        "(`ocv_window → wakeup_load → glow_plug_like_load → crank_pulse → recovery_rest → "
        "… → alternator_charge → ramp_like_load / driving_aux_load`)."
    ),
    run_sel, run_pane,
    pn.pane.Markdown("### Extracted features"),
    run_tbl,
    sizing_mode="stretch_width",
)

compare_tab = pn.Column(
    pn.pane.Markdown("## Compare Runs"),
    pn.pane.Markdown("Overlay voltage/current for multiple test-day runs on a shared elapsed-time axis."),
    compare_sel, compare_pane,
    sizing_mode="stretch_width",
)

sweep_tab = pn.Column(
    pn.pane.Markdown("## SoC Sweep"),
    pn.pane.Markdown(
        "Each test-day run starts at a target SoC relative to that block's own C/5 capacity. Plotting a "
        "feature vs SoC per block separates SoC dependence (along a line) from ageing (between lines)."
    ),
    sweep_feat_sel, sweep_pane,
    pn.pane.Markdown("### Run feature table"),
    sweep_tbl,
    sizing_mode="stretch_width",
)

trends_tab = pn.Column(
    pn.pane.Markdown("## Degradation Trends"),
    pn.pane.Markdown(
        "Any feature per block, averaged across SoC or at one SoC. A single SoC is the fairer comparison, "
        "because a block with a shortened sweep would otherwise shift the mean."
    ),
    pn.Row(trend_feat_sel, trend_soc_sel), trend_pane,
    sizing_mode="stretch_width",
)

corr_intro = pn.pane.Alert(
    "**What this page does.** Every extracted test-day feature (crank R_int, wakeup sag, "
    "alternator charge ratio, recovery voltages, …) is correlated (Spearman) against every other "
    "feature — including starting OCV/SoC and block number — across all clean test-day runs. "
    "Below that, a focused check on the question the v2 profile was designed around: the vehicle "
    "only ever observes **OCV at rest**, never true SOH — so does any within-block feature track "
    "**this block's** or **the next block's** measured C/5 capacity ahead of time? A feature that "
    "predicts the *next* block's capacity is a candidate field-deployable health indicator.\n\n"
    "Every dot on the scatter below is one test-day run — hover it to see exactly which file, "
    "block, OCV and SoC% it is.",
    alert_type="info", sizing_mode="stretch_width",
)

corr_tab = pn.Column(
    pn.pane.Markdown("## Feature Correlations", margin=(5, 5, 0, 5)),
    corr_intro,
    pn.Card(
        pn.Row(corr_clean_toggle, sizing_mode="stretch_width"),
        corr_status,
        corr_heatmap_pane,
        title="🔗  Correlation heatmap — all features, all runs",
        collapsed=False, sizing_mode="stretch_width",
    ),
    pn.Card(
        pn.Row(corr_x_sel, corr_y_sel, corr_color_sel, sizing_mode="stretch_width"),
        corr_scatter_pane,
        title="🔍  Pairwise scatter explorer",
        collapsed=False, sizing_mode="stretch_width",
    ),
    pn.Card(
        pn.pane.Markdown(
            "One row per feature (mean over that block's clean runs) correlated against **this "
            "block's** and **the next block's** measured C/5 capacity.\n\n"
            "**Needs at least 3 blocks with valid features, and n stays small** (one row per "
            "block). Treat rankings as directional. These tables start empty for this battery, "
            "because only runs after the replay fix count."
        ),
        predictor_tbl,
        title="📈  Block-level SOH predictors  (this block / next block)",
        collapsed=False, sizing_mode="stretch_width",
    ),
    pn.Card(
        pn.pane.Markdown(
            "**The question that actually matters for a field-deployable indicator:** a vehicle "
            "only ever sees OCV/SoC at rest, never true SOH — so a *useful* health feature has to "
            "track degradation **independently of SoC**, not just move because the battery happens "
            "to be at a different charge level this time. `quality = |r vs next-block SOH| − |r vs "
            "SoC%|` — high when a feature strongly predicts the next block's capacity while barely "
            "moving with SoC.\n\n"
            "**Don't carry over conclusions from unit2**: its discharge events ran at a frozen "
            "current, so its rankings can't be assumed to hold here."
        ),
        soc_robust_tbl,
        title="🎯  SoC-robust SOH indicators  (tracks health, not just charge level)",
        collapsed=False, sizing_mode="stretch_width",
    ),
    sizing_mode="stretch_width",
)

soh_tab = pn.Column(
    pn.pane.Markdown("## SOH History"),
    pn.pane.Markdown(
        "Measured C/5 discharge capacity per block — the one honest SOH ground truth "
        "(`Block_<nn>_SOH_C5_bdps_*.csv`, once per block), integrated from the logs here. "
        "SOH as a percentage of Block 1 is on the Campaign tab."
    ),
    soh_pane,
    sizing_mode="stretch_width",
)

degr_tab = pn.Column(
    pn.pane.Markdown("## Degradation Cycles"),
    pn.pane.Markdown(
        "Charge/discharge Ah and coulombic efficiency per degradation cycle (5 cycles/block). The first "
        "discharge of a block starts after the SoC sweep, so it is small by design and left out of the "
        "efficiency. Fragment or still-in-progress cycles are marked with **✕**."
    ),
    degr_pane,
    pn.pane.Markdown("### Cycle table"),
    degr_tbl,
    sizing_mode="stretch_width",
)

inventory_tab = pn.Column(
    pn.pane.Markdown("## File Inventory"),
    pn.pane.Markdown("One row per primary (bdps) log file found for this battery — use this to track what's landed so far."),
    inventory_tbl,
    sizing_mode="stretch_width",
)

export_tab = pn.Column(
    pn.pane.Markdown("## Export to Excel"),
    pn.pane.Markdown(
        "| Sheet | Contents |\n|---|---|\n"
        "| **Run Summary** | Raw feature values per test-day run, with `discharge_tracking_ok` |\n"
        "| **Sweep Points** | SoC, OCV and Ah per sweep point |\n"
        "| **Block SOH** | C/5 per block, from the Pi and from the logs |\n"
        "| **Degradation Cycles** | Ah + coulombic efficiency per cycle |\n"
        "| **SOH History** | Measured C/5 Ah capacity per block |\n"
        "| **File Inventory** | Every primary log file found on disk |"
    ),
    pn.Row(export_dir_input, sizing_mode="stretch_width"),
    export_btn, export_status,
    sizing_mode="stretch_width",
)

incident_note = pn.pane.Alert(
    "**Data notes.** The Pi's C/5 and the logs integrated here differ by a steady ~0.2 Ah (cause not yet "
    "established); each SOH percentage uses its own Block 1, so the ratio is unaffected. The first two sweep "
    "points of Block 2 were redone after the fix, and only the latest file per point is used. A C/5 point can "
    "show as **\"not final yet\"** while that test is still running on the Pi; rescan after it completes.",
    alert_type="info", sizing_mode="stretch_width",
)

_STAT_CARD_STYLE = {
    "background": "#fcfcfb",
    "border": "1px solid rgba(11,11,11,0.10)",
    "border-radius": "10px",
    "padding": "6px 4px",
}


def _stat_card(indicator: pn.indicators.Number) -> pn.Column:
    return pn.Column(indicator, styles=_STAT_CARD_STYLE, sizing_mode="stretch_width")


stat_row = pn.Row(
    _stat_card(stat_blocks), _stat_card(stat_runs), _stat_card(stat_cycles), _stat_card(stat_soh),
    sizing_mode="stretch_width",
)

# ── Lazy sub-tab loading ─────────────────────────────────────────────────────
# Compare Runs / Feature Correlations are populated on first view rather than
# at import time (see the note above _update_run_inspector()). Rescan still
# refreshes every tab eagerly, including these two -- it's an explicit,
# infrequent user action, not the page-load path this is optimizing.
_COMPARE_TAB_INDEX = 2
_CORR_TAB_INDEX = 7
_lazy_loaded = {_COMPARE_TAB_INDEX: False, _CORR_TAB_INDEX: False}


def _on_main_tab_active(event) -> None:
    idx = event.new
    if idx in _lazy_loaded and not _lazy_loaded[idx]:
        _lazy_loaded[idx] = True
        if idx == _COMPARE_TAB_INDEX:
            _update_compare()
        elif idx == _CORR_TAB_INDEX:
            _update_corr()


main_tabs = pn.Tabs(
    ("Campaign", campaign_tab),
    ("Run Inspector", inspector_tab),
    ("Compare Runs", compare_tab),
    ("SoC Sweep", sweep_tab),
    ("Sweep Quality", sweepq_tab),
    ("Setpoint Check", track_tab),
    ("Degradation Trends", trends_tab),
    ("Feature Correlations", corr_tab),
    ("SOH History", soh_tab),
    ("Degradation Cycles", degr_tab),
    ("File Inventory", inventory_tab),
    ("Export", export_tab),
    sizing_mode="stretch_both",
)
main_tabs.param.watch(_on_main_tab_active, "active")

main_content = pn.Column(
    stat_row,
    header_caption,
    quality_banner,
    incident_note,
    pn.Row(rescan_btn, show_invalid_toggle),
    main_tabs,
    sizing_mode="stretch_both",
)

dashboard = pn.template.FastListTemplate(
    title=f"Battery Dashboard — {BATTERY_ID}",
    main=[main_content],
    accent_base_color=_COLORS[0],
    header_background=_COLORS[0],
    header_color="#FFFFFF",
)
dashboard.servable()

if __name__ == "__main__":
    pn.serve(dashboard, title=f"Battery Dashboard — {BATTERY_ID}", show=True, port=5011)
