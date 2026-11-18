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
  stayed frozen at its previous limit (-3.6 A through all of Block 1), so those
  runs are left out of every analysis by default, never deleted. Their SoC and
  OCV stay on Sweep Quality: the SoC came from a step-down that worked.

The test is still running, so every tab handles missing and half-written files.
Use **Rescan** to pick up files synced by pi_daily_report.py.

Run with:
    panel serve battery_feature_dashboard_unit3.py --port 5011 --show
"""

import json
import os
import re
import warnings

import numpy as np
import pandas as pd
import panel as pn
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import detailed_features as dfm
import detailed_figures as dfig
import soh_eval as se
import testday_v2_features as v2feat
from dashboard_panes import plot_pane
from lab_field_feature_comparison import FLAGGED_MARKER

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
# Tracking tolerance, sink floor and the per-feature validity rules live in
# testday_v2_features.py (event_checks / feature_validity), shared with the tests.
MIN_SINK_SAMPLES = v2feat.MIN_SINK_SAMPLES
NOMINAL_AH     = 18.0     # Ultracell UL18-12 nameplate C/20 capacity
INVALID_SUFFIX = "__invalid_reason"  # per-feature validity column, "" = valid
_GREY = "#c3c2b7"
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
    "removed_ah",
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
        row["discharge_file_present"] = "discharge" in kinds
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
    # From Block 3 on the sweep ends at 10 % SoC close enough to the cutoff that
    # the first discharge stops after a few seconds (a ~300-650 byte file). That
    # is the protocol working, not a fragment. Not applied to the running cycle:
    # _load_state() overrides that one from the checkpoint.
    at_cutoff = df["first_after_sweep"] & df["discharge_file_present"] & df["discharge_ah"].isna()
    df["discharge_at_cutoff"] = at_cutoff
    df.loc[at_cutoff, "discharge_ah"] = 0.0
    df.loc[at_cutoff, "discharge_complete"] = True
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
        if r["discharge_at_cutoff"]:
            return "complete (sweep left the battery at cutoff; discharge ~0 Ah)"
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
    cols = ["block", "soh_c5_ah", "soh_pct_of_ref", "ocv_before_soh", "unplanned_gap_h", "degr_charge_timeouts", "soh_start"]
    df = df[[c for c in cols if c in df.columns]].copy() if not df.empty else pd.DataFrame(columns=cols)
    cp = _read_json("_checkpoint.json")
    running = cp.get("block_summary") or {}
    if running.get("soh_c5_ah") is not None and cp.get("block_index") not in set(df["block"]):
        row = {c: running.get(c) for c in cols}
        row["block"] = cp["block_index"]
        df = pd.concat([df.astype(object), pd.DataFrame([row]).astype(object)], ignore_index=True)
    for c in df.columns:
        if c == "soh_start":
            df[c] = pd.to_datetime(df[c], errors="coerce")
        else:
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
            "c5_start": pi.loc[b, "soh_start"] if b in pi.index and "soh_start" in pi else pd.NaT,
            "temp_c": _soh_sensor_temperature(b),
            "charge_timeouts": pi.loc[b, "degr_charge_timeouts"] if b in pi.index and "degr_charge_timeouts" in pi else None,
            "gap_h": pi.loc[b, "unplanned_gap_h"] if b in pi.index and "unplanned_gap_h" in pi else None,
        })
    return pd.DataFrame(rows)


PROJECTION_BLOCKS = 4  # fit the stop estimate on the last N blocks only


def _projected_stop(table: pd.DataFrame):
    """Estimate of when SOH crosses the stop line, from a straight line through
    the last PROJECTION_BLOCKS blocks. The early blocks are left out: capacity
    rose in Block 2 and fell fastest in Blocks 3-5, so a fit over the whole
    campaign lags behind the current rate.

    Returns None, or a dict: slope (%/block), intercept, fitted blocks,
    stop_block (fractional), first_block_below (the first whole block whose C/5
    is expected below the line), next_soh_pct (expected at the next block), and
    next_c5_at (expected start of the next C/5, from the median block duration).
    """
    sub = table.dropna(subset=["pi_soh_pct"]) if not table.empty else table
    if len(sub) < 3:
        return None
    sub = sub.sort_values("block").tail(PROJECTION_BLOCKS)
    x = sub["block"].astype(float)
    slope, intercept = np.polyfit(x, sub["pi_soh_pct"].astype(float), 1)
    if slope >= 0:
        return None
    last = int(x.iloc[-1])
    stop_block = (SOH_STOP_PCT - intercept) / slope
    out = {
        "slope": slope, "intercept": intercept, "blocks": (int(x.iloc[0]), last),
        "stop_block": stop_block, "first_block_below": max(last + 1, int(np.ceil(stop_block))),
        "next_soh_pct": slope * (last + 1) + intercept, "next_c5_at": None,
    }
    starts = table.dropna(subset=["c5_start"]).sort_values("block") if "c5_start" in table else table.iloc[0:0]
    if len(starts) >= 3:
        per_block = (starts["c5_start"].diff() / starts["block"].diff()).dropna().tail(PROJECTION_BLOCKS).median()
        out["block_duration"] = per_block
        out["next_c5_at"] = starts["c5_start"].iloc[-1] + per_block
    return out


# ── Profiles and the setpoint check ──────────────────────────────────────────

_PROFILES = v2feat.load_profiles([os.path.join(PROFILE_DIR, n) for n in PROFILE_FILES])
_setpoint_at = v2feat.setpoint_at


def _read_run_with_setpoint(path: str):
    """(run sorted by time, setpoint per row, profile name) or (None, None, status)."""
    try:
        df = pd.read_csv(path, on_bad_lines="skip")
    except Exception:
        return None, None, "unreadable"
    if df.empty or not v2feat.REQUIRED_V2_COLS.issubset(df.columns):
        return None, None, "unreadable"
    profile = _PROFILES.get(str(df["Profile_Hash"].iloc[0]))
    if profile is None:
        return None, None, "unknown_profile"
    df = df.sort_values("Elapsed_s").reset_index(drop=True)
    return df, _setpoint_at(df["Elapsed_s"].to_numpy(), profile[0], profile[1]), profile[2]


def _run_quality_impl(path: str) -> dict:
    """Per-event measured vs commanded current, and per-feature validity. Only
    small results are returned (see the cache note in testday_v2_features.py)."""
    df, sp, name = _read_run_with_setpoint(path)
    if df is None:
        return {"status": name, "events": [], "validity": {}}
    df["sp"] = sp

    events = []
    for idx, g in df.groupby("Event_Index", sort=True):
        etype = g["Event_Type"].iloc[0]
        v_min = float(g["Voltage"].min())
        # Replay timing can put a sample of the next event's setpoint just
        # before this event's boundary, so a stray sample isn't a discharge event.
        status, sp_med, meas_med = v2feat.sink_status(g["Current"].to_numpy(float), g["sp"].to_numpy(float), v_min)
        if status is None:
            continue
        sink = g[g["sp"] < 0]
        events.append({
            "event_index": int(idx), "event": etype, "status": status,
            "sp_median_A": round(sp_med, 2), "meas_median_A": round(meas_med, 2),
            "sp_peak_A": round(float(sink["sp"].min()), 2), "meas_peak_A": round(float(g["Current"].min()), 2),
            "error_A": round(abs(meas_med - sp_med), 2), "v_min_V": round(v_min, 3),
        })
    validity = v2feat.feature_validity(df, sp)
    return {"status": "checked", "profile": name, "events": events, "validity": validity}


def run_quality(path: str) -> dict:
    # Known profiles are part of the key: adding one must re-check runs cached as unknown.
    # v4 (2026-11-17): per-feature validity added. Bump when the validity rules change.
    key = (path, v2feat._fingerprint(path), tuple(sorted(_PROFILES)))
    return v2feat.cached_call("unit3_quality_v4", key, lambda: _run_quality_impl(path))


def attach_quality(df: pd.DataFrame, sweep: pd.DataFrame) -> pd.DataFrame:
    """Adds SoC (Coulomb-counted, from _sweep_points.csv; else the filename's
    target), Ah removed before the run, tracking flags, in-progress marking,
    and one <feature>__invalid_reason column per catalogued feature ("" =
    valid). Feature values stay raw here; mask_invalid() blanks invalid ones."""
    if df.empty:
        return df
    df = df.copy()
    by_file = sweep.set_index("testday_file") if not sweep.empty and "testday_file" in sweep else pd.DataFrame()
    profile_max = max((float(p[0][-1]) for p in _PROFILES.values()), default=None)
    newest = df["timestamp"].max()
    # First load after a rule change reads every run once; threads overlap the I/O.
    quality = v2feat.parallel_map(lambda fn: run_quality(os.path.join(LOG_DIR, fn)), list(df["filename"]))
    v2feat.flush_cache()
    catalogued = [f for f in v2feat.FEATURE_CATALOG if f in df.columns]

    records = []
    for (_, r), q in zip(df.iterrows(), quality):
        rec = {}
        m = re.search(r"_SoC(\d+)_", r["filename"])
        target = int(m.group(1)) if m else None
        if r["filename"] in by_file.index:
            rec["soc_pct"] = float(by_file.loc[r["filename"], "soc_cc_pct"])
            rec["soc_source"] = "coulomb"
            rec["removed_ah"] = float(by_file.loc[r["filename"], "removed_ah_before_run"])
        else:
            rec["soc_pct"] = float(target) if target is not None else None
            rec["soc_source"] = "target"
            cap = r.get("block_c5_capacity_ah")
            rec["removed_ah"] = (100 - target) / 100 * cap if target is not None and pd.notna(cap) else None
        rec["target_soc_pct"] = target
        rec["profile_cap"] = q.get("profile")
        ev = q["events"]
        rec["discharge_tracking_ok"] = (
            q["status"] == "checked" and bool(ev) and not any(e["status"] == "failed" for e in ev)
        )
        rec["run_in_progress"] = bool(
            r["timestamp"] == newest and profile_max and r["run_duration_s"] < profile_max * 0.99
        )
        validity = q.get("validity", {})
        for f in catalogued:
            rec[f + INVALID_SUFFIX] = validity.get(f, "")
        records.append(rec)
    extra = pd.DataFrame(records, index=df.index)
    return pd.concat([df, extra], axis=1)


def analysis_view(df: pd.DataFrame, include_invalid: bool) -> pd.DataFrame:
    """Runs used for analysis: never a run still being written, and by default
    only runs that passed the setpoint check. The whole run goes, not just the
    failed events, because each event starts from the state the previous ones
    left behind. A run that couldn't be checked has nothing vouching for it.
    Feature values are raw: use mask_invalid() before any statistic."""
    if df.empty:
        return df
    out = df[~df["run_in_progress"]]
    return out if include_invalid else out[out["discharge_tracking_ok"]]


def mask_invalid(df: pd.DataFrame) -> pd.DataFrame:
    """Invalid feature values set to NaN, per feature per run (see
    testday_v2_features.feature_validity). Use for every statistic."""
    if df.empty:
        return df
    out = df.copy()
    for col in df.columns:
        if col.endswith(INVALID_SUFFIX):
            feat = col[: -len(INVALID_SUFFIX)]
            if feat in out.columns:
                out.loc[out[col].fillna("") != "", feat] = np.nan
    return out


def split_valid(df: pd.DataFrame, feature: str) -> tuple:
    """(rows with a valid value, rows with an invalid raw value) for one feature."""
    if df.empty or feature not in df.columns:
        return df.iloc[0:0], df.iloc[0:0]
    reason_col = feature + INVALID_SUFFIX
    reason = df[reason_col].fillna("") if reason_col in df.columns else pd.Series("", index=df.index)
    has = df[feature].notna()
    return df[has & (reason == "")], df[has & (reason != "")]


def _add_invalid_markers(fig: go.Figure, inv: pd.DataFrame, x_col: str, y_col: str, x_values=None, **subplot) -> None:
    """Grey ✕ (FLAGGED_MARKER) for invalid points; they never feed a line or a statistic."""
    if inv.empty:
        return
    reasons = inv.get(y_col + INVALID_SUFFIX, pd.Series("", index=inv.index)).fillna("")
    labels = [f"Block {int(b)}, SoC {s:.0f} %" if pd.notna(s) else f"Block {int(b)}"
              for b, s in zip(inv["block"], inv["soc_pct"])]
    fig.add_trace(go.Scatter(
        x=x_values if x_values is not None else inv[x_col], y=inv[y_col], mode="markers",
        marker={**FLAGGED_MARKER, "size": 10, "color": "#9a9890"}, name="invalid (not used)",
        legendgroup="invalid", showlegend=not any(t.legendgroup == "invalid" for t in fig.data),
        customdata=np.stack([labels, reasons], axis=-1),
        hovertemplate="%{customdata[0]}<br>%{y}<br>invalid: %{customdata[1]}<extra></extra>",
    ), **subplot)


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


def build_sweep_quality_fig(sweep: pd.DataFrame, invalid_net_ah_files: set = frozenset()) -> go.Figure:
    """invalid_net_ah_files: runs whose profile didn't really replay. Their SoC and
    OCV are fine (set by the step-down, before the profile), their net Ah isn't."""
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
        net = g[~g["testday_file"].isin(invalid_net_ah_files)]
        fig.add_trace(go.Scatter(x=net["soc_cc_pct"], y=net["profile_net_ah"], showlegend=False, **kw), row=1, col=3)
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
    proj = _projected_stop(table)
    if proj is not None:
        b0, b1 = proj["blocks"]
        xs = np.array([b0, proj["first_block_below"]], dtype=float)
        fig.add_trace(go.Scatter(
            x=xs, y=proj["slope"] * xs + proj["intercept"], mode="lines",
            name=f"Trend of Blocks {b0}–{b1} ({proj['slope']:.1f} %/block)",
            line=dict(color="#898781", dash="dot", width=1.5), hoverinfo="skip",
        ))
    fig.add_hline(y=SOH_STOP_PCT, line=dict(color=_STATUS_CRITICAL, dash="dash"),
                  annotation_text=f"Stop at {SOH_STOP_PCT:.0f} %", annotation_position="bottom left")
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
    proj = _projected_stop(table)
    known = table.dropna(subset=["pi_soh_pct"]) if not table.empty else table
    if not known.empty and float(known["pi_soh_pct"].iloc[-1]) < SOH_STOP_PCT:
        b = int(known["block"].iloc[-1])
        lines.append(f"- **Stop line reached**: Block {b} measured {known['pi_soh_pct'].iloc[-1]:.1f} %. "
                     "The Pi should end the plan after this C/5.")
    elif proj is not None:
        b0, b1 = proj["blocks"]
        when = ""
        if proj.get("next_c5_at") is not None and pd.notna(proj["next_c5_at"]):
            hours = proj["block_duration"].total_seconds() / 3600
            when = f", expected to start around **{proj['next_c5_at']:%a %d %b %H:%M}** (median block {hours:.0f} h)"
        lines.append(
            f"- Trend of Blocks {b0}–{b1}: **{proj['slope']:.1f} %-point per block**. The next C/5 "
            f"(Block {b1 + 1}) should read about **{proj['next_soh_pct']:.1f} %**{when}. "
            f"First block expected below {SOH_STOP_PCT:.0f} %: **{proj['first_block_below']}**. "
            "An estimate: the rate has varied from block to block."
        )
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
    flag = "  ·  ⚠ setpoint check failed" if r.get("setpoint_failed") else ""
    return f"Block {r['block']}  ·  {soc}OCV {r['ocv_label']:.2f}V  ·  {r['ts']}{flag}"


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


# ── Pairwise scatter (Diagnostics) ─────────────────────────────────────────────
_CORR_EXCLUDE_COLS = {"battery_id", "kind", "timestamp", "filename", "soc_source", "profile_cap",
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
        # Profile constants and duplicates follow the profile or repeat a column.
        and (c not in v2feat.FEATURE_CATALOG or v2feat.is_analysis_feature(c))
        and pd.api.types.is_numeric_dtype(df[c])
        and df[c].nunique(dropna=True) > 1
    ]


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


def charge_matched_r(y: pd.Series, removed_ah: pd.Series, run_cap: pd.Series) -> tuple:
    """(r_runs, r_charge_matched, R² of the charge fit), each None without enough data.

    r_runs: Spearman r of the feature with the block's capacity, all runs
    pooled, SoC treated as unknown, as in a vehicle. r_charge_matched: the same
    after removing what the absolute charge removed explains (quadratic fit
    pooled over all blocks): what the feature adds beyond the charge level.
    Runs of one block share one capacity, so the effective n is the number of blocks."""
    m = y.notna() & run_cap.notna() & removed_ah.notna()
    if m.sum() < 6 or run_cap[m].nunique() < 3:
        return None, None, None
    yy, xx, cap = y[m].astype(float), removed_ah[m].astype(float), run_cap[m].astype(float)
    r_runs = yy.corr(cap, method="spearman")
    resid = yy - np.polyval(np.polyfit(xx, yy, 2), xx)
    r_cm = resid.corr(cap, method="spearman")
    var = float(yy.var())
    r2 = 1 - float(resid.var()) / var if var > 0 else None
    return r_runs, r_cm, r2


# ── SoC Sweep / Degradation Trends / SOH figures ──────────────────────────────

def _feature_options(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    return [
        c for c in df.columns
        if c not in _META_COLS and pd.api.types.is_numeric_dtype(df[c])
    ]


_X_TITLES = {
    "soc_pct": ("SoC, Coulomb-counted [%]", "SoC"),
    "removed_ah": ("Ah removed from full before the run (Pi count) [Ah]", "Ah removed"),
}


def build_sweep_fig(df: pd.DataFrame, feature: str, show_invalid: bool = False, x_col: str = "soc_pct") -> go.Figure:
    """One line per block. df holds raw values; invalid ones never join a line
    and appear as grey ✕ when show_invalid."""
    fig = go.Figure()
    x_title, x_short = _X_TITLES.get(x_col, (x_col, x_col))
    if df.empty or feature not in df.columns or x_col not in df.columns or df[feature].dropna().empty:
        fig.update_layout(title="No data for this feature yet", height=420, template="plotly_white")
        return fig
    valid, inv = split_valid(df.dropna(subset=[x_col]), feature)
    blocks = sorted(df["block"].dropna().unique())
    for b, color in zip(blocks, _ordinal_ramp(len(blocks))):
        g = valid[valid["block"] == b].sort_values(x_col)
        if g.empty:
            continue
        fig.add_trace(go.Scatter(
            x=g[x_col], y=g[feature], mode="markers+lines", name=f"Block {b}",
            line=dict(color=color), marker=dict(size=9, color=color),
            customdata=g["filename"], hovertemplate="%{customdata}<br>" + x_short + " %{x:.2f}<br>%{y}<extra></extra>",
        ))
    if show_invalid:
        _add_invalid_markers(fig, inv, x_col, feature)
    if valid.empty:
        fig.update_layout(title=f"{feature}: no valid values")
    fig.update_xaxes(title_text=x_title)
    fig.update_yaxes(title_text=feature)
    fig.update_layout(height=420, template="plotly_white", legend=dict(orientation="v"),
                      title=fig.layout.title.text or f"{feature} vs {x_short}, per block")
    return fig


TREND_EACH_SOC = -1  # trend_soc_sel value: one line per SoC level


def build_trend_fig(df: pd.DataFrame, feature: str, soc=None, soh_by_block: dict | None = None,
                    show_invalid: bool = False) -> go.Figure:
    """Feature per block. Averaging across SoC only compares blocks fairly when
    each has the same points, so a single SoC can be selected, or every SoC as
    its own line (soc == TREND_EACH_SOC). With soh_by_block (block -> Pi SOH %)
    the x axis is SOH instead of the block number; blocks without one are dropped.
    df holds raw values: invalid ones are left out of the means and appear as
    grey ✕ (one per run) when show_invalid."""
    fig = go.Figure()
    if df.empty or feature not in df.columns or df[feature].dropna().empty:
        fig.update_layout(title="No data for this feature yet", height=420, template="plotly_white")
        return fig
    on_soh = soh_by_block is not None
    valid, inv = split_valid(df, feature)
    if soc not in (None, TREND_EACH_SOC):
        valid = valid[valid["target_soc_pct"] == soc]
        inv = inv[inv["target_soc_pct"] == soc]
    to_x = (lambda d: d["block"].map(soh_by_block)) if on_soh else (lambda d: d["block"])
    sub = valid.assign(_x=to_x(valid)).dropna(subset=["_x"])
    inv = inv.assign(_x=to_x(inv)).dropna(subset=["_x"])

    def _trace(g, name, color, legend):
        agg = g.groupby("block").agg(x=("_x", "first"), y=(feature, "mean")).reset_index().sort_values("block")
        return go.Scatter(
            x=agg["x"], y=agg["y"], mode="markers+lines", name=name, showlegend=legend,
            line=dict(color=color), marker=dict(size=9, color=color),
            customdata=agg["block"],
            hovertemplate=f"{name}<br>Block %{{customdata}}<br>%{{x}}<br>%{{y}}<extra></extra>",
        )

    if soc == TREND_EACH_SOC:
        levels = sorted(df["target_soc_pct"].dropna().unique(), reverse=True)
        # Lighter = higher SoC, darker = lower, same ordinal ramp as "colour by".
        for level, color in zip(levels, _ordinal_ramp(len(levels))):
            g = sub[sub["target_soc_pct"] == level]
            if not g.empty:
                fig.add_trace(_trace(g, f"SoC {level:.0f} %", color, True))
        where = "one line per SoC"
    else:
        if not sub.empty:
            fig.add_trace(_trace(sub, feature, _COLORS[0], False))
        where = f"at SoC {soc} %" if soc is not None else "mean across SoC"
    if show_invalid:
        _add_invalid_markers(fig, inv, "_x", feature)
    n_blocks = sub["block"].nunique()
    note = "" if n_blocks > 1 else "  (one valid block so far)"
    if on_soh:
        # Ageing reads left to right: SOH falls along the axis.
        fig.update_xaxes(title_text="SOH, C/5 as % of Block 1 (Pi)", autorange="reversed")
    else:
        fig.update_xaxes(title_text="Block #", dtick=1)
    fig.update_yaxes(title_text=feature)
    fig.update_layout(height=460, template="plotly_white",
                      title=f"{feature} vs {'SOH' if on_soh else 'block'}, {where}{note}",
                      legend=dict(orientation="v"))
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


# ── Feature Explorer ───────────────────────────────────────────────────────────

_KIND_LABELS = {
    "feature": "Features", "diagnostic": "Diagnostics",
    "profile_constant": "Profile constants (follow the profile)", "duplicate_of": "Duplicates",
}
_RULE_TEXT = {
    "tracking": "the event's sink current followed the profile (not voltage_limited or failed)",
    "not_failed": "the event's sink current did not fail for a reason other than the voltage floor",
    "current_low": f"I_peak ≥ {v2feat.I_PEAK_MIN_FRAC:.0%} of the setpoint reachable after the "
                   f"{v2feat.CURRENT_RISE_S:g} s current rise",
    "v_pre_floor": f"V_pre more than {v2feat.V_PRE_FLOOR_MARGIN:g} V above the {v2feat.SINK_V_FLOOR:g} V sink floor",
    "delta_i_small": f"current step ≥ {v2feat.DELTA_I_MIN_FRAC:.0%} of the step the profile commands",
    "i_range_small": f"current range ≥ {v2feat.DELTA_I_MIN_FRAC:.0%} of the range the profile commands",
    "few_samples": f"≥ {v2feat.MIN_WINDOW_SAMPLES} samples in each window",
    "never": "never valid",
}


def catalog_md(feature: str, short: bool = False) -> str:
    e = v2feat.FEATURE_CATALOG.get(feature)
    if e is None:
        return f"`{feature}` is not in the feature catalogue (testday_v2_features.FEATURE_CATALOG)."
    ev = f"`{e['event'][0]}` #{e['event'][1]}" if e["event"] else "whole run"
    win = ", ".join(v2feat._window_text(w) for w in e["windows"])
    if short:
        return f"**{feature}** ({e['kind']}, {e['unit']}): {e['meaning']} *Formula:* {e['formula']}."
    rules = []
    for r in e["rules"]:
        name, _, target = r.partition("@")
        text = _RULE_TEXT.get(name, name)
        rules.append(f"{text} (on `{target.replace(':', '` #')})" if target else text)
    lines = [
        f"### `{feature}`",
        f"**Kind:** {e['kind']}  ·  **Unit:** {e['unit']}  ·  **Event:** {ev}  ·  **Window:** {win}",
        f"**Formula:** {e['formula']}",
        f"**Meaning:** {e['meaning']}",
        f"**Expected with ageing:** {e['ageing']}  ·  **SoC dependence:** {e['soc']}",
        f"**Field counterpart:** {e['field']}",
        "**Valid when:** " + ("; ".join(rules) if rules else "always (whole-run quantity)"),
    ]
    if e["note"]:
        lines.append(f"**Note:** {e['note']}")
    return "\n\n".join(lines)


def estimate_adc_step(path: str):
    """Voltage resolution of the BDPS log: the most common non-zero step
    between consecutive samples during the 390 s rest_baseline (0 A, so the
    voltage only moves by single ADC steps)."""
    def _impl():
        try:
            df = pd.read_csv(path, usecols=["Voltage", "Event_Type"], on_bad_lines="skip")
        except Exception:
            return None
        v = df.loc[df["Event_Type"] == "rest_baseline", "Voltage"].to_numpy(float)
        d = np.round(np.abs(np.diff(v)), 4)
        d = d[d > 0]
        if len(d) < 10:
            return None
        vals, counts = np.unique(d, return_counts=True)
        return float(vals[np.argmax(counts)])
    return v2feat.cached_call("unit3_adc_step", (path, v2feat._fingerprint(path)), _impl)


def _event_slice(df: pd.DataFrame, event):
    """Rows of the catalogued event in one run, or None."""
    if event is None:
        return df
    for etype, occ, g in v2feat.event_occurrences(df):
        if (etype, occ) == tuple(event):
            return g
    return None


def _downsample(g: pd.DataFrame, n_max: int = 2500) -> pd.DataFrame:
    step = int(np.ceil(len(g) / n_max)) if len(g) > n_max else 1
    return g.iloc[::step]


def build_raw_event_fig(feature: str, rows: pd.DataFrame, soc) -> go.Figure:
    """V and I of the feature's event, one trace per block at one target SoC,
    on a time axis relative to the event start. The feature's windows are
    shaded. Runs where the feature is invalid are drawn grey and dotted.
    Reads only these runs, uncached (raw frames are too big to cache)."""
    entry = v2feat.FEATURE_CATALOG.get(feature)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        subplot_titles=["Voltage", "Current"], row_heights=[0.6, 0.4])
    if entry is None or rows.empty:
        return fig.update_layout(title="No runs", height=520, template="plotly_white")
    sub = rows[rows["target_soc_pct"] == soc].sort_values("block")
    if sub.empty:
        return fig.update_layout(title=f"No runs at SoC {soc} %", height=520, template="plotly_white")

    def _load(r):
        df = v2feat.load_run(os.path.join(LOG_DIR, r["filename"]))
        return None if df is None else df[["Elapsed_s", "Voltage", "Current", "Event_Index", "Event_Type"]]

    frames = v2feat.parallel_map(_load, [r for _, r in sub.iterrows()])
    windows = entry["windows"]
    whole_run = entry["event"] is None
    pre_need = max([w[1] for w in windows if w[0] == "prev_tail"], default=0.0)
    colors = _ordinal_ramp(len(sub))
    ref_dur = None
    for (_, r), df, color in zip(sub.iterrows(), frames, colors):
        g = _event_slice(df, entry["event"]) if df is not None else None
        if g is None or g.empty:
            continue
        t0, t1 = float(g["Elapsed_s"].min()), float(g["Elapsed_s"].max())
        dur = t1 - t0
        ref_dur = ref_dur if ref_dur is not None else dur
        if whole_run:
            seg = df
        else:
            pad = max(1.0, min(0.1 * dur, 20.0))
            lo, hi = t0 - max(pad, pre_need + 0.5), t1 + pad
            seg = df[(df["Elapsed_s"] >= lo) & (df["Elapsed_s"] <= hi)]
        seg = _downsample(seg)
        x = seg["Elapsed_s"] - (0 if whole_run else t0)
        reason = str(r.get(feature + INVALID_SUFFIX, "") or "")
        name = f"Block {int(r['block'])}" + (" (invalid)" if reason else "")
        line = dict(color=_GREY, width=1.2, dash="dot") if reason else dict(color=color, width=1.4)
        hover = f"{name}<br>%{{x:.2f}} s<br>%{{y}}" + (f"<br>invalid: {reason}" if reason else "") + "<extra></extra>"
        fig.add_trace(go.Scattergl(x=x, y=seg["Voltage"], mode="lines", name=name, legendgroup=name,
                                   line=line, hovertemplate=hover), row=1, col=1)
        fig.add_trace(go.Scattergl(x=x, y=seg["Current"], mode="lines", name=name, legendgroup=name,
                                   showlegend=False, line=line, hovertemplate=hover), row=2, col=1)
    if ref_dur is not None and not whole_run:
        for w in windows:
            kind = w[0]
            if kind == "at":
                spans = [(w[1] - 0.5, w[1] + 0.5)]
            elif kind == "head":
                spans = [(0, w[1])]
            elif kind == "tail":
                spans = [(ref_dur - w[1], ref_dur)]
            elif kind == "prev_tail":
                spans = [(-w[1], 0)]
            else:
                spans = [(0, ref_dur)]
            for x0, x1 in spans:
                for row in (1, 2):
                    fig.add_vrect(x0=x0, x1=x1, fillcolor=_COLORS[2], opacity=0.12, line_width=0, row=row, col=1)
        fig.add_vline(x=0, line=dict(color="#898781", width=1, dash="dash"))
    ev = f"{entry['event'][0]} #{entry['event'][1]}" if entry["event"] else "whole run"
    fig.update_xaxes(title_text="Elapsed [s]" if whole_run else f"Time from the start of {ev} [s]", row=2, col=1)
    fig.update_yaxes(title_text="Voltage [V]", row=1, col=1)
    fig.update_yaxes(title_text="Current [A]", row=2, col=1)
    fig.update_layout(
        height=560, template="plotly_white", legend=dict(orientation="v"), margin=dict(t=60),
        title=dict(text=f"Raw signal at SoC {soc} %: {ev}, shaded = where {feature} is measured", x=0.02, xanchor="left"),
    )
    return fig


CONCL_MIN_ADC_STEPS = 2.0     # campaign shift below this many ADC steps: below resolution
CONCL_R_CM_SOH = 0.5          # |r_charge_matched| from here: information beyond the charge level
CONCL_CHARGE_R2 = 0.8         # share of variance the Ah-removed fit explains: follows charge
# Loaded voltages fall off faster at low SoC than a quadratic in Ah removed
# follows (glow V_mean: R² 0.71 with r_soc 0.85), so a strong rank correlation
# with SoC also counts as following the charge.
CONCL_R_SOC_CHARGE = 0.8


def feature_stats(rows: pd.DataFrame, feature: str, soh_by_block: dict, adc_step) -> dict:
    """Key numbers for the Explorer. rows: raw analysis view (one per run).

    shift: change of the feature over the campaign at a fixed SoC level,
    i.e. the slope against SOH (straight line per level, needs 3 blocks)
    times that level's SOH range; median over the levels.
    spread_in_block: median over blocks of the std across that block's runs,
    i.e. how much the feature moves with SoC inside one block.
    resolution: one ADC step in feature units (catalogue adc_scale)."""
    entry = v2feat.FEATURE_CATALOG.get(feature, {})
    out = {"n_total": len(rows), "n_valid": 0}
    if rows.empty or feature not in rows.columns:
        return out
    valid, _ = split_valid(rows, feature)
    col = feature + INVALID_SUFFIX
    # Invalid includes runs where the value could not be computed at all for the
    # same reason (e.g. no current step at the floor), not only flagged values.
    bad = rows[col].fillna("") != "" if col in rows else pd.Series(False, index=rows.index)
    out.update(n_valid=len(valid), n_invalid=int(bad.sum()),
               invalid_socs=sorted(rows.loc[bad, "target_soc_pct"].dropna().unique().tolist()))
    if len(valid) < 6:
        return out
    y = valid[feature].astype(float)
    out["r_soc"] = y.corr(valid["soc_pct"], method="spearman")
    cap = valid["block"].map(soh_by_block)
    out["r_runs"], out["r_cm"], out["charge_r2"] = charge_matched_r(y, valid["removed_ah"], cap)
    shifts = []
    for _, g in valid.assign(_soh=cap).dropna(subset=["_soh"]).groupby("target_soc_pct"):
        if g["block"].nunique() >= 3:
            slope = np.polyfit(g["_soh"], g[feature].astype(float), 1)[0]
            shifts.append(slope * (g["_soh"].min() - g["_soh"].max()))  # sign: from new to aged
    out["shift"] = float(np.median(shifts)) if shifts else None
    spreads = valid.groupby("block")[feature].std().dropna()
    out["spread_in_block"] = float(spreads.median()) if not spreads.empty else None
    scale = entry.get("adc_scale")
    out["resolution"] = adc_step * scale if adc_step and scale else None
    return out


def feature_conclusion(s: dict, feature: str) -> tuple:
    """(label, explanation). Labels: 'volgt lading', 'volgt SOH', 'volgt SOH en lading',
    'ruis' (shift below the ADC resolution), 'zwak' (nothing explains it well),
    'volgt profiel', 'duplicaat', 'ongeldig'; ' · ongeldig bij lage SoC' is
    appended when the invalid runs are all at 20 % SoC or below."""
    tags = []
    low = s.get("invalid_socs", [])
    if low and max(low) <= 20:
        tags.append("ongeldig bij lage SoC")
    kind = v2feat.FEATURE_CATALOG.get(feature, {}).get("kind", "")
    if kind == "profile_constant":
        return " · ".join(["volgt profiel"] + tags), "The profile sets this value; it is not a battery measurement."
    if kind.startswith("duplicate_of:"):
        return "duplicaat", f"Same values as `{kind.split(':', 1)[1]}`; read that one."
    if s.get("n_valid", 0) < 6:
        return "ongeldig", "Fewer than 6 valid runs: no statistic."
    r_cm, r2, shift, res = s.get("r_cm"), s.get("charge_r2"), s.get("shift"), s.get("resolution")
    r_soc = s.get("r_soc")
    follows_charge = (r2 is not None and r2 >= CONCL_CHARGE_R2) or (
        r_soc is not None and not np.isnan(r_soc) and abs(r_soc) >= CONCL_R_SOC_CHARGE)
    why = []
    if res and shift is not None and abs(shift) < CONCL_MIN_ADC_STEPS * res:
        label = "ruis"
        why.append(f"the campaign shift ({shift:+.4g}) is below {CONCL_MIN_ADC_STEPS:g} ADC steps ({res:.3g} each)")
    elif r_cm is not None and abs(r_cm) >= CONCL_R_CM_SOH:
        label = "volgt SOH en lading" if follows_charge else "volgt SOH"
        why.append(f"|r_charge_matched| = {abs(r_cm):.2f} ≥ {CONCL_R_CM_SOH}: capacity information remains "
                   "after the charge removed is accounted for")
        if follows_charge:
            why.append(f"it also follows the charge (R² of the Ah-removed fit {r2:.2f}, r_soc {r_soc:+.2f})")
    elif follows_charge:
        label = "volgt lading"
        why.append(f"the charge explains it (R² of the Ah-removed fit {r2:.2f}, r_soc {r_soc:+.2f}) and only "
                   f"|r_charge_matched| = {abs(r_cm or 0):.2f} remains: a block-level SOH correlation would be "
                   "the SoC grid shrinking with capacity")
    else:
        label = "zwak"
        why.append(f"neither the charge (R² {r2 if r2 is not None else float('nan'):.2f}, r_soc "
                   f"{r_soc if r_soc is not None else float('nan'):+.2f}) nor capacity (|r_charge_matched| "
                   f"{abs(r_cm) if r_cm is not None else float('nan'):.2f}) explains it well")
    return " · ".join([label] + tags), "; ".join(why) + "."


def feature_stats_md(s: dict, feature: str) -> str:
    def f(v, fmt="{:+.2f}"):
        return "–" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)
    label, why = feature_conclusion(s, feature)
    unit = v2feat.FEATURE_CATALOG.get(feature, {}).get("unit", "")
    res, shift, spread = s.get("resolution"), s.get("shift"), s.get("spread_in_block")
    lines = [
        f"### Conclusion: **{label}**",
        why,
        "| | |", "|---|---|",
        f"| Valid runs | **{s.get('n_valid', 0)} / {s.get('n_total', 0)}**"
        + (f" (invalid at SoC {', '.join(f'{x:.0f}' for x in s['invalid_socs'])} %)" if s.get("invalid_socs") else "")
        + " |",
        f"| r_soc (Spearman, valid runs) | {f(s.get('r_soc'))} |",
        f"| r_runs (capacity, SoC unknown) | {f(s.get('r_runs'))} |",
        f"| r_charge_matched | **{f(s.get('r_cm'))}** |",
        f"| R² of the Ah-removed fit | {f(s.get('charge_r2'), '{:.2f}')} |",
        f"| Shift over the campaign at fixed SoC | {f(shift, '{:+.4g}')} {unit} |",
        f"| Spread within a block (across SoC) | {f(spread, '{:.4g}')} {unit}"
        + (f"  ·  shift / spread = {abs(shift) / spread:.1f}" if shift is not None and spread else "") + " |",
        f"| ADC resolution | {f(res, '{:.3g}')} {unit if res else ''}"
        + (f"  ·  shift = {abs(shift) / res:.0f} steps" if shift is not None and res else "") + " |",
        "",
        "*Effective n for the r values is the number of blocks (runs in one block share one capacity).*",
    ]
    return "\n".join(lines)


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
        dataset.to_excel(writer, sheet_name="Run Summary", index=False)
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
    global _DATASET, _DEGR_DF, _SOH_DF, _SWEEP_DF, _SOH_TABLE, _EVENTS_DF, RUN_OPTIONS, _ADC_STEP
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
    # Median over a few runs spread over the campaign, so one odd file can't set it.
    files = _DATASET["filename"].tolist() if not _DATASET.empty else []
    picks = files[:: max(1, len(files) // 5)][:6]
    steps = [s for s in (estimate_adc_step(os.path.join(LOG_DIR, f)) for f in picks) if s]
    _ADC_STEP = float(np.median(steps)) if steps else None
    v2feat.flush_cache()


def _failed_setpoint_files() -> set:
    if _DATASET.empty:
        return set()
    return set(_DATASET.loc[~_DATASET["discharge_tracking_ok"], "filename"])


def _run_options() -> dict:
    bad = _failed_setpoint_files()
    infos = [
        {**r, "setpoint_failed": os.path.basename(r["bdps_path"]) in bad}
        for r in v2feat.find_v2_testday_runs(LOG_DIR) if r["ocv_label"] is not None
    ]
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
    name="Test-day runs passing setpoint check", value=0, format="{value}",
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
run_pane = plot_pane()
run_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=400, sizing_mode="stretch_width")

compare_sel = pn.widgets.MultiSelect(
    name="Runs to compare  (Ctrl+click for multiple)",
    options=list(RUN_OPTIONS), size=10, width=420,
    value=list(RUN_OPTIONS)[:3],
)
compare_pane = plot_pane()

sweep_feat_sel = pn.widgets.Select(name="Feature", options=_feature_options(_DATASET), width=320)
sweep_pane = plot_pane()
sweep_tbl = pn.widgets.Tabulator(pd.DataFrame(), pagination="local", page_size=15, height=340, sizing_mode="stretch_width")

sweep_def = pn.pane.Markdown("", sizing_mode="stretch_width")


def _explorer_groups() -> dict:
    groups: dict = {}
    for name in v2feat.FEATURE_CATALOG:
        groups.setdefault(_KIND_LABELS[v2feat.catalog_kind(name)], []).append(name)
    return {k: groups[k] for k in _KIND_LABELS.values() if k in groups}


explorer_feat_sel = pn.widgets.Select(name="Feature (catalogue)", groups=_explorer_groups(),
                                      value="rest_baseline_Vmean", width=360)
explorer_soc_sel = pn.widgets.Select(name="Raw signal at SoC", options={f"{s} %": s for s in range(100, 0, -10)},
                                     value=50, width=180)
explorer_def = pn.pane.Markdown("", sizing_mode="stretch_width")
explorer_stats = pn.pane.Markdown("", sizing_mode="stretch_width")
explorer_raw_pane = plot_pane()
explorer_soc_pane = plot_pane()
explorer_ah_pane = plot_pane()
explorer_soh_pane = plot_pane()

soh_pane = plot_pane()
soh_tbl = pn.widgets.Tabulator(_SOH_TABLE, height=300, sizing_mode="stretch_width")

campaign_status = pn.pane.Markdown("", sizing_mode="stretch_width")
campaign_pane = plot_pane()
events_tbl = pn.widgets.Tabulator(_EVENTS_DF, height=220, sizing_mode="stretch_width")

sweepq_pane = plot_pane()
sweepq_tbl = pn.widgets.Tabulator(pd.DataFrame(), pagination="local", page_size=20, height=480, sizing_mode="stretch_width")

track_overview_pane = plot_pane()
track_run_sel = pn.widgets.Select(name="Test-day run", options=list(RUN_OPTIONS), width=460)
track_pane = plot_pane()
track_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=320, sizing_mode="stretch_width")

show_invalid_toggle = pn.widgets.Checkbox(
    name="Also show runs that failed the setpoint check, and invalid feature values as grey ✕", value=False)
quality_banner = pn.pane.Alert("", alert_type="warning", sizing_mode="stretch_width")

degr_pane = plot_pane()
degr_tbl = pn.widgets.Tabulator(_DEGR_DF, pagination="local", page_size=15, height=340, sizing_mode="stretch_width")

inventory_tbl = pn.widgets.Tabulator(
    build_file_inventory(LOG_DIR), pagination="local", page_size=25,
    height=500, sizing_mode="stretch_width",
)

corr_x_sel = pn.widgets.Select(name="X axis", options=[], width=260)
corr_y_sel = pn.widgets.Select(name="Y axis", options=[], width=260)
corr_color_sel = pn.widgets.Select(name="Colour by", options=[], width=260)
corr_scatter_pane = plot_pane()


def _detailed_groups() -> dict:
    labels = {"testday": "Field: test-day events", "c5": "Lab: start of the C/5",
              "charge": "Lab: CC/CV charges", "rest": "Lab: rest after a step-down"}
    groups: dict = {}
    for name, e in dfm.DETAILED_CATALOG.items():
        if not name.endswith("_rmse_mV"):
            groups.setdefault(labels[e["source"]], []).append(name)
    return groups


detail_feat_sel = pn.widgets.Select(name="Feature (detailed)", groups=_detailed_groups(),
                                    value="relax_hot_tau2_s", width=360)
detail_soc_sel = pn.widgets.Select(name="Raw signal at SoC", options={f"{s} %": s for s in range(90, 0, -10)},
                                   value=50, width=180)
detail_ah_sel = pn.widgets.FloatSlider(name="Equal charge removed for the SOH plot [Ah]", start=1.0, end=6.0,
                                       step=0.5, value=3.0, width=320)
detail_def = pn.pane.Markdown("", sizing_mode="stretch_width")
detail_raw_pane = plot_pane()
detail_ah_pane = plot_pane()
detail_soh_pane = plot_pane()
detail_rank_tbl = pn.widgets.Tabulator(pd.DataFrame(), height=520, sizing_mode="stretch_width",
                                       pagination="local", page_size=25, disabled=True, show_index=False)
detail_export_btn = pn.widgets.Button(name="⬇  Export for paper", button_type="primary", width=200)
detail_export_status = pn.pane.Markdown("", sizing_mode="stretch_width")

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
    stat_runs.value = int(_DATASET["discharge_tracking_ok"].sum()) if not _DATASET.empty else 0
    stat_runs.format = f"{{value}} / {len(_DATASET)}"
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
            "the Pi fix of 2026-11-02, when discharge current stayed frozen. They are left out of the feature analyses "
            "(Feature Explorer, Detailed Features, SoC Sweep) unless you tick the box below. They stay on Setpoint "
            "Check, and with a ⚠ in Run Inspector and Compare Runs. Sweep Quality keeps their SoC and OCV, which "
            "are sound, and hides only their net Ah. The Excel export keeps the raw values, with "
            "`discharge_tracking_ok` next to them."
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
    """Rows for the analysis tabs, raw feature values (plots split valid/invalid)."""
    return analysis_view(_DATASET, show_invalid_toggle.value)


def _stats_view() -> pd.DataFrame:
    """_view() with invalid feature values blanked: the input of every statistic."""
    return mask_invalid(_view())


def _soh_by_block() -> dict:
    if _SOH_TABLE.empty:
        return {}
    known = _SOH_TABLE.dropna(subset=["pi_soh_pct"])
    return dict(zip(known["block"].astype(int), known["pi_soh_pct"].astype(float)))


def _update_sweep(event=None):
    sweep_pane.object = build_sweep_fig(_view(), sweep_feat_sel.value, show_invalid_toggle.value)
    sweep_def.object = catalog_md(sweep_feat_sel.value, short=True) if sweep_feat_sel.value else ""


def _update_sweep_table(event=None):
    # Apart from _update_sweep: a new feature doesn't change the rows, and
    # resending the whole table on each selection costs ~100 websocket messages.
    tbl = _stats_view()
    sweep_tbl.value = tbl.drop(columns=[c for c in tbl.columns if c.endswith(INVALID_SUFFIX)])


def _update_sweep_quality(event=None):
    bad = set() if show_invalid_toggle.value else _failed_setpoint_files()
    sweepq_pane.object = build_sweep_quality_fig(_SWEEP_DF, bad)
    tbl = _SWEEP_DF.drop(columns=["battery_id"], errors="ignore")
    if bad:
        tbl = tbl.copy()
        tbl.loc[tbl["testday_file"].isin(bad), "profile_net_ah"] = np.nan
    sweepq_tbl.value = tbl


def _update_scatter(event=None):
    corr_scatter_pane.object = build_scatter_pair(_stats_view(), corr_x_sel.value, corr_y_sel.value,
                                                  color_col=corr_color_sel.value)


def _update_diagnostics(event=None):
    """Compare Runs and the pairwise scatter, both on the Diagnostics tab."""
    _update_compare()
    cols = _corr_columns(_stats_view())
    corr_x_sel.options = cols
    corr_y_sel.options = cols
    corr_color_sel.options = cols
    if cols:
        # Not block against block: that default plotted a column against itself (r = 1).
        if corr_x_sel.value not in cols:
            corr_x_sel.value = "removed_ah" if "removed_ah" in cols else cols[0]
        if corr_y_sel.value not in cols:
            corr_y_sel.value = ("crank_cold_R_int_deltaI_mohm" if "crank_cold_R_int_deltaI_mohm" in cols
                                else cols[-1])
        if corr_color_sel.value not in cols:
            corr_color_sel.value = "block" if "block" in cols else cols[0]
    _update_scatter()


# ── Detailed Features (curve-based features, see DETAILED_FEATURES_BRIEF.md) ──
_DETAILED: dict = {}


def _detailed_run_features(fn: str) -> dict:
    path = os.path.join(LOG_DIR, fn)

    def _impl():
        df, sp, _ = _read_run_with_setpoint(path)
        return dfm.run_features(df, sp) if df is not None else {}
    key = (path, v2feat._fingerprint(path), tuple(sorted(_PROFILES)))
    return v2feat.cached_call(f"{dfm.CACHE_VERSION}_run_unit3", key, _impl)


def _ensure_detailed(force: bool = False) -> dict:
    """Run-level detailed features joined to _DATASET, the lab tables and the
    ranking. Built on first use of the tab (~10 s uncached), then cached."""
    if _DETAILED and not force:
        return _DETAILED
    runs = _DATASET
    if not runs.empty:
        feats = v2feat.parallel_map(_detailed_run_features, list(runs["filename"]))
        runs = dfm.attach_run_validity(pd.concat([runs, pd.DataFrame(feats, index=runs.index)], axis=1),
                                       INVALID_SUFFIX)
    labs = {"c5": (dfm.build_c5_table(LOG_DIR), None, 6),
            "charge": (dfm.build_charge_table(LOG_DIR), None, 20),
            "rest": (dfm.build_rest_table(LOG_DIR, _SWEEP_DF), "rest_ocv_end_V", 30)}
    v2feat.flush_cache()
    _DETAILED.clear()
    _DETAILED.update({"runs": runs, "labs": labs})
    _update_detailed_ranking()
    return _DETAILED


def _detailed_runs_view() -> pd.DataFrame:
    return analysis_view(_DETAILED.get("runs", pd.DataFrame()), show_invalid_toggle.value)


def _update_detailed_ranking() -> None:
    runs = mask_invalid(analysis_view(_DETAILED["runs"], False))
    labs = {k: (dfm.mask(t), ocv, n) for k, (t, ocv, n) in _DETAILED["labs"].items()}
    existing = [c for c in v2feat.FEATURE_CATALOG if v2feat.is_analysis_feature(c) and c != se.OCV_COL]
    tbl = dfig.ranking_table(runs, labs, _soh_by_block(), existing)
    _DETAILED["ranking"] = tbl
    shown = tbl.round({"r2_ocv": 2, "rho": 2, "same_sign": 2, "lobo_rmse": 1,
                       "baseline_rmse": 1, "gain": 1, "worst_err": 1})
    for c in ("n", "blocks", "worst_block"):
        shown[c] = shown[c].astype("Int64")
    detail_rank_tbl.value = shown


def _detailed_feature_rows(feature: str) -> pd.DataFrame:
    src = dfm.DETAILED_CATALOG[feature]["source"]
    if src == "testday":
        return _detailed_runs_view()
    return _DETAILED["labs"][src][0]


def _detailed_figs(feature: str) -> dict:
    rows = _detailed_feature_rows(feature)
    blocks = sorted(rows["block"].dropna().unique()) if not rows.empty else []
    colors = _ordinal_ramp(len(blocks))
    src = dfm.DETAILED_CATALOG[feature]["source"]
    soc = detail_soc_sel.value
    if src == "testday":
        at = rows[(rows["target_soc_pct"] == soc)].sort_values("block") if not rows.empty else rows
        raw = dfig.raw_testday_fig(feature, at, _ordinal_ramp(len(at)),
                                   lambda fn: _read_run_with_setpoint(os.path.join(LOG_DIR, fn))[:2])
    else:
        raw = dfig.raw_lab_fig(feature, LOG_DIR, rows, colors, soc)
    return {"raw": raw,
            "vs_ah": dfig.vs_ah_fig(feature, rows, colors),
            "vs_soh": dfig.vs_soh_fig(feature, rows, _soh_by_block(), detail_ah_sel.value, colors)}


def _detailed_def_md(feature: str) -> str:
    e = dfm.DETAILED_CATALOG[feature]
    parents = e["parent"] if isinstance(e["parent"], tuple) else ((e["parent"],) if e["parent"] else ())
    rules = [f"valid when `{p}` is valid" for p in parents] + list(e["rules"])
    lit = "; ".join(dfm.LIT.get(k, k) for k in e["lit"])
    lines = [f"### `{feature}`",
             f"**Availability:** {e['availability']} ({dfig.SOURCE_LABEL[e['source']]})  ·  **Unit:** {e['unit']}",
             f"**Formula:** {e['formula']}", f"**Meaning:** {e['meaning']}",
             "**Validity:** " + ("; ".join(rules) if rules else "always"), f"**Literature:** {lit}"]
    if e["note"]:
        lines.append(f"**Note:** {e['note']}")
    r = _DETAILED.get("ranking")
    if r is not None and feature in set(r["feature"]):
        x = r.set_index("feature").loc[feature]
        lines.append(f"**Leave-one-block-out:** {x['lobo_rmse']:.1f} SOH %-points against a baseline of "
                     f"{x['baseline_rmse']:.1f} on the same rows (n = {int(x['n'])}, {int(x['blocks'])} blocks).")
    return "\n\n".join(lines)


def _update_detailed(event=None):
    _ensure_detailed()
    feat = detail_feat_sel.value
    figs = _detailed_figs(feat)
    detail_def.object = _detailed_def_md(feat)
    detail_raw_pane.object = figs["raw"]
    detail_ah_pane.object = figs["vs_ah"]
    detail_soh_pane.object = figs["vs_soh"]


def _run_detailed_export(event=None):
    try:
        _ensure_detailed()
        feat = detail_feat_sel.value
        out = os.path.join(BASE_DIR, "exports", "unit3_paper")
        figs = {f"{feat}_{k}": v for k, v in _detailed_figs(feat).items()}
        files = dfig.export_for_paper(out, _DETAILED["ranking"], figs)
        detail_export_status.object = f"Saved {len(files)} files to `{out}`"
    except Exception as e:  # kaleido or a locked file
        detail_export_status.object = f"Export failed: {e}"


def _explorer_rows() -> pd.DataFrame:
    return _view()


def _update_explorer_raw(event=None):
    explorer_raw_pane.object = build_raw_event_fig(explorer_feat_sel.value, _explorer_rows(), explorer_soc_sel.value)


def _update_explorer(event=None):
    feat = explorer_feat_sel.value
    rows = _explorer_rows()
    soh = _soh_by_block()
    explorer_def.object = catalog_md(feat)
    explorer_stats.object = feature_stats_md(feature_stats(rows, feat, soh, _ADC_STEP), feat)
    # Every Explorer plot shows invalid points, whatever the checkbox says.
    explorer_soc_pane.object = build_sweep_fig(rows, feat, True, "soc_pct")
    explorer_ah_pane.object = build_sweep_fig(rows, feat, True, "removed_ah")
    explorer_soh_pane.object = build_trend_fig(rows, feat, TREND_EACH_SOC, soh, show_invalid=True)
    _update_explorer_raw()


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
    _update_sweep_table()
    _update_sweep_quality()
    if _lazy_loaded[_DIAG_TAB_INDEX]:
        _update_diagnostics()
    if _lazy_loaded[_EXPLORER_TAB_INDEX]:
        _update_explorer()
    if _lazy_loaded[_DETAILED_TAB_INDEX]:
        _update_detailed()


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

    soh_tbl.value = _SOH_TABLE
    degr_tbl.value = _DEGR_DF
    inventory_tbl.value = build_file_inventory(LOG_DIR)

    _update_header()
    _update_run_inspector()
    _update_sweep()
    _update_sweep_table()
    _update_campaign()
    _update_tracking()
    _update_sweep_quality()
    soh_pane.object = build_soh_fig(_SOH_DF)
    degr_pane.object = build_degr_fig(_DEGR_DF)
    if _lazy_loaded[_EXPLORER_TAB_INDEX]:
        _update_explorer()
    if _lazy_loaded[_DIAG_TAB_INDEX]:
        _update_diagnostics()
    if _lazy_loaded[_DETAILED_TAB_INDEX]:
        _ensure_detailed(force=True)
        _update_detailed()


def _run_export(event=None):
    try:
        path = export_excel(export_dir_input.value, _DATASET, _DEGR_DF, _SOH_DF)
        export_status.object = f"Saved: `{path}`"
    except Exception as e:
        export_status.object = f"Export failed: {e}"


run_sel.param.watch(_update_run_inspector, "value")
compare_sel.param.watch(_update_compare, "value")
sweep_feat_sel.param.watch(_update_sweep, "value")
track_run_sel.param.watch(_update_track_run, "value")
show_invalid_toggle.param.watch(_on_invalid_toggle, "value")
corr_x_sel.param.watch(_update_scatter, "value")
corr_y_sel.param.watch(_update_scatter, "value")
corr_color_sel.param.watch(_update_scatter, "value")
explorer_feat_sel.param.watch(_update_explorer, "value")
explorer_soc_sel.param.watch(_update_explorer_raw, "value")
detail_feat_sel.param.watch(_update_detailed, "value")
detail_soc_sel.param.watch(_update_detailed, "value")
detail_ah_sel.param.watch(_update_detailed, "value_throttled")
detail_export_btn.on_click(_run_detailed_export)
rescan_btn.on_click(_rescan)
export_btn.on_click(_run_export)

if RUN_OPTIONS:
    run_sel.value = list(RUN_OPTIONS)[0]
# Diagnostics, Feature Explorer and Detailed Features are deferred to first
# view, see "Lazy sub-tab loading" below.
if RUN_OPTIONS:
    track_run_sel.value = list(RUN_OPTIONS)[0]
_update_run_inspector()
_update_campaign()
_update_tracking()
_update_sweep_quality()
_update_sweep()
_update_sweep_table()
soh_pane.object = build_soh_fig(_SOH_DF)
degr_pane.object = build_degr_fig(_DEGR_DF)


# ── Layout ─────────────────────────────────────────────────────────────────────
campaign_tab = pn.Column(
    pn.pane.Markdown("## Campaign"),
    campaign_status,
    pn.pane.Markdown("*Question:* How did the capacity label develop over the campaign?  ·  *Paper:* Fig. 2a, label paragraph of Results", styles={"color": "#5f5e57"}),
    campaign_pane,
    pn.pane.Markdown("*Question:* What is the measured C/5 capacity per block, in Ah?  ·  *Paper:* label paragraph of Results (15.25 → 7.11 Ah); the table below compares the Pi and log counts (0.2 Ah difference)", styles={"color": "#5f5e57"}),
    soh_pane,
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
        "step-down that hit the cutoff before reaching its target.\n\n"
        "Runs that failed the setpoint check stay here: SoC and OCV are set by the step-down and rest, before "
        "the profile replays. Only their net Ah is hidden, because that does depend on the replay."
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
    sweep_feat_sel, sweep_def, sweep_pane,
    pn.pane.Markdown("### Run feature table"),
    sweep_tbl,
    sizing_mode="stretch_width",
)

explorer_tab = pn.Column(
    pn.pane.Markdown("## Feature Explorer"),
    pn.pane.Markdown(
        "One feature at a time: what it measures, where in the raw signal it comes from, whether each run's "
        "value is valid, and how it moves with SoC, with the charge removed and with SOH. **Grey ✕ = invalid "
        "value** (the reason is in the hover); it never feeds a line or a number below. A feature that only "
        "follows the charge has lines that collapse onto one curve against *Ah removed*; one that follows "
        "SOH keeps its blocks apart there."
    ),
    pn.Row(explorer_feat_sel, explorer_soc_sel),
    pn.Row(explorer_def, explorer_stats, sizing_mode="stretch_width"),
    pn.pane.Markdown("*Question:* Is the feature measured where we think?  ·  *Paper:* method description of each feature", styles={"color": "#5f5e57"}),
    explorer_raw_pane,
    pn.pane.Markdown("*Question:* Does the feature follow the charge removed, or does it keep the blocks apart there?  ·  *Paper:* Fig. 2b/2c", styles={"color": "#5f5e57"}),
    explorer_ah_pane,
    pn.pane.Markdown("*Question:* How does it move with relative SoC? (the protocol grid; read with the caveat above)  ·  *Paper:* SoC-levels paragraph", styles={"color": "#5f5e57"}),
    explorer_soc_pane,
    pn.pane.Markdown("*Question:* Does it follow SOH at each SoC level?  ·  *Paper:* Results, Which features carry SOH information", styles={"color": "#5f5e57"}),
    explorer_soh_pane,
    sizing_mode="stretch_width",
)

detailed_tab = pn.Column(
    pn.pane.Markdown("## Detailed Features"),
    pn.pane.Markdown(
        "Curve-based features (`detailed_features.py`): ohmic vs polarisation split per load step, an "
        "equivalent-circuit fit, relaxation after the cranks and the ramp, charge acceptance (field: from the "
        "test-day events), and the start of the C/5, the CC/CV charges and the rests after each step-down "
        "(lab: tests a vehicle never runs). Every value has validity rules; **grey ✕ = invalid**, never used. "
        "The SOH comparison is at an **equal charge removed**, not at an equal relative SoC, because at a "
        "relative SoC an aged battery is tested less deeply discharged (the artefact in the paper's Results). "
        "The first open builds the features of all runs (~10 s), later opens are cached."
    ),
    pn.Row(detail_feat_sel, detail_soc_sel, detail_ah_sel),
    detail_def,
    pn.pane.Markdown("*Question:* Is the feature measured where we think, and does the fit follow the signal?  ·  *Paper:* method figure", styles={"color": "#5f5e57"}),
    detail_raw_pane,
    pn.pane.Markdown("*Question:* Does the feature separate the blocks at an equal charge removed?  ·  *Paper:* Fig. 2c type panel", styles={"color": "#5f5e57"}),
    detail_ah_pane,
    pn.pane.Markdown("*Question:* Does the feature follow SOH at that equal charge?  ·  *Paper:* Results", styles={"color": "#5f5e57"}),
    detail_soh_pane,
    pn.pane.Markdown(
        "### Which features carry SOH information beyond the charge level?\n"
        "Leave-one-block-out (`soh_eval.py`): SOH ~ OCV + OCV² + feature for field features (rest OCV before the "
        "day; for rests the OCV at the end of the rest), SOH ~ feature for C/5 and charge features (fixed state, "
        "no OCV). `baseline_rmse` is the same model without the feature on the same rows, `gain` the "
        "improvement. Field features: blocks 2–11 (Block 1's replay was frozen); lab features: blocks 1–11. "
        "With ~40 candidates and 10 blocks, the best few are partly selected by chance: treat a gain of a few "
        "points as a lead, not a result. *Paper:* tab:features."
    ),
    detail_rank_tbl,
    pn.Row(detail_export_btn, detail_export_status),
    sizing_mode="stretch_width",
)

degr_tab = pn.Column(
    pn.pane.Markdown("## Degradation Cycles"),
    pn.pane.Markdown(
        "Charge/discharge Ah and coulombic efficiency per degradation cycle (5 cycles/block). The first "
        "discharge of a block starts after the SoC sweep, so it is small by design and left out of the "
        "efficiency. Fragment or still-in-progress cycles are marked with **✕**."
    ),
    pn.pane.Markdown("*Question:* How much charge does each cycle return for what it took out?  ·  *Paper:* Discussion: undercharge and sulphation", styles={"color": "#5f5e57"}),
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
diag_tab = pn.Column(
    pn.pane.Markdown("## Diagnostics"),
    pn.pane.Markdown(
        "Views for checking single runs and odd values. They have no paper element of their own; the paper "
        "figures come from Campaign, Sweep Quality, Setpoint Check, Feature Explorer and Detailed Features."
    ),
    pn.Tabs(
        ("Run Inspector", inspector_tab),
        ("Compare Runs", compare_tab),
        ("SoC Sweep", sweep_tab),
        ("Pairwise scatter", pn.Column(
            pn.pane.Markdown("Any two run-level columns against each other, invalid values blanked."),
            pn.Row(corr_x_sel, corr_y_sel, corr_color_sel, sizing_mode="stretch_width"),
            corr_scatter_pane, sizing_mode="stretch_width")),
        dynamic=True, sizing_mode="stretch_width",
    ),
    sizing_mode="stretch_width",
)

# ── Lazy sub-tab loading ─────────────────────────────────────────────────────
# Feature Explorer, Detailed Features and Diagnostics are populated on first
# view rather than at import time. Rescan refreshes the ones already opened.
_EXPLORER_TAB_INDEX = 3  # reads ten raw runs per feature: only when opened
_DETAILED_TAB_INDEX = 4  # builds the detailed features of all runs on first open
_DIAG_TAB_INDEX = 6
_lazy_loaded = {_EXPLORER_TAB_INDEX: False, _DETAILED_TAB_INDEX: False, _DIAG_TAB_INDEX: False}


def _on_main_tab_active(event) -> None:
    idx = event.new
    if idx in _lazy_loaded and not _lazy_loaded[idx]:
        _lazy_loaded[idx] = True
        if idx == _EXPLORER_TAB_INDEX:
            _update_explorer()
        elif idx == _DETAILED_TAB_INDEX:
            _update_detailed()
        elif idx == _DIAG_TAB_INDEX:
            _update_diagnostics()


# Graph -> paper element: see analytics/CLAUDE.md (unit3 section). Removed on
# 2026-11-18 because they had no paper use or showed the relative-SoC artefact:
# Degradation Trends, the correlation heatmap, the this/next-block predictor
# and SoC-robust tables (replaced by the ranking on Detailed Features), and the
# separate SOH History tab (now on Campaign).
main_tabs = pn.Tabs(
    ("Campaign", campaign_tab),
    ("Sweep Quality", sweepq_tab),
    ("Setpoint Check", track_tab),
    ("Feature Explorer", explorer_tab),
    ("Detailed Features", detailed_tab),
    ("Degradation Cycles", degr_tab),
    ("Diagnostics", diag_tab),
    ("File Inventory", inventory_tab),
    ("Export", export_tab),
    # Only the open tab is in the browser. Otherwise all its plots draw at page
    # load, each resizing and reporting back to the server.
    dynamic=True,
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
