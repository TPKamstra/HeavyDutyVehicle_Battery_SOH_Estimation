"""
testday_v2_features.py

Feature extraction for the "test-day profile v2" logging format (see
CLAUDE.md → "Test-Day Profile Format (v2)" on the Pi side, and the companion
analysis note this module implements).

Unlike the original testday_run files (plain rest/discharge/charge pulses,
detected purely from current thresholds — see battery_feature_dashboard.py's
detect_pulses), v2 profile runs replay an explicit event script and log which
event was active on every row via Event_Type / Event_Index columns. Feature
extraction here is event-driven, not threshold-driven.

As of 2026-07-02 the rollout is partial: logged testday_bdps files carry
Event_Type/Event_Index/Profile_Hash, but not yet a Battery_ID column (and
filenames don't carry a battery_id prefix either) — every run currently
resolves to battery_id "unknown" until backend.py adds it. Older files
(pre-rollout) still use the plain 5-column schema (Timestamp, Elapsed_s,
Voltage, Current, Mode); load_run() detects and skips those rather than
erroring, since both schemas currently share the same filename convention.

Two independent labels fall out of this data (do not conflate them):
  1. Per-run label = starting OCV, parsed from the filename (`OCV<v>V`).
  2. Per-block label = measured C/5 Ah capacity, shared by all runs in a
     block (`Block_<nn>_SOH_C5_bdps_*.csv`).

Battery_ID is load-bearing: never aggregate across battery IDs implicitly.
Once backend.py starts logging it, build_dataset() will pick it up
automatically (it prefers the logged column over the filename-parsed id).
"""

import os
import pickle
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

# ── Disk cache for the expensive per-file work below ─────────────────────────
# CSV read + sensor merge_asof in load_run(), CSV read + capacity calc in
# find_block_soh(), and battery_feature_dashboard_unit2.py's own _cycle_ah --
# all keyed on (path, mtime, size) via _fingerprint(), so a file that changes
# (e.g. the "live-updating SOH C5 file" case in CLAUDE.md, or a retried/
# re-run cycle) is reprocessed, but an unchanged file across dashboard
# restarts/Rescans is served from cache.
#
# A first pass used joblib.Memory (one cache subdirectory + several small
# files per call) but measured ~25-30ms/call even on a full cache *hit* for
# this dataset's ~550 per-file calls -- dominated by per-call filesystem
# round-trips on this machine, not actual (de)serialization cost (most
# cached values are tiny tuples). This instead keeps ONE dict, loaded from a
# single pickle file once per process and read from memory after that -- a
# cache hit is a dict lookup, not a filesystem round-trip.
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".v2_feature_cache")
_CACHE_PATH = os.path.join(_CACHE_DIR, "cache.pkl")
_cache_lock = threading.Lock()
_cache_dict: dict | None = None


def _fingerprint(path: str | None) -> tuple | None:
    if path is None or not os.path.exists(path):
        return None
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


def _get_cache_dict() -> dict:
    global _cache_dict
    if _cache_dict is None:
        with _cache_lock:
            if _cache_dict is None:  # re-check inside the lock
                try:
                    with open(_CACHE_PATH, "rb") as f:
                        _cache_dict = pickle.load(f)
                except (FileNotFoundError, EOFError, pickle.PickleError):
                    _cache_dict = {}
    return _cache_dict


def flush_cache() -> None:
    """Persist any new cache entries to disk. Called once after each batch
    of cached_call()s (build_dataset()/find_degr_cycles()/find_block_soh())
    rather than after every individual call, so a big scan costs one write,
    not hundreds."""
    cache = _get_cache_dict()
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp_path = _CACHE_PATH + f".tmp{os.getpid()}"
    with _cache_lock:
        with open(tmp_path, "wb") as f:
            pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, _CACHE_PATH)


def cached_call(tag: str, key: tuple, compute_fn):
    """compute_fn() is only ever invoked once per unique (tag, *key); every
    later call with the same key returns the stored result. `key` should
    include each input file's _fingerprint() so a changed file gets a fresh
    key (and is recomputed) rather than serving a stale cached value."""
    cache = _get_cache_dict()
    full_key = (tag,) + key
    with _cache_lock:
        if full_key in cache:
            return cache[full_key]
    result = compute_fn()
    with _cache_lock:
        cache[full_key] = result
    return result


def parallel_map(fn, items: list, max_workers: int = 8) -> list:
    """Plain ThreadPoolExecutor .map(), collected eagerly. I/O-bound work
    (CSV parsing releases the GIL) benefits from real parallelism here; safe
    to call with any mix of cache hits/misses since cached_call() above
    guards its shared dict with a lock."""
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [fn(items[0])]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(fn, items))


# ── Constants ─────────────────────────────────────────────────────────────────
# Battery_ID is intentionally NOT required: as of 2026-07-02 logged runs already
# carry Event_Type/Event_Index/Profile_Hash but backend.py hasn't added the
# Battery_ID column yet. Requiring it here would (and did) silently misclassify
# every real v2 run as legacy. build_dataset() falls back to the filename-parsed
# battery_id (or "unknown") when the column is absent.
REQUIRED_V2_COLS = {"Event_Type", "Event_Index", "Profile_Hash"}

# Two crank_pulse events are expected per run. Real logged current magnitudes
# have NOT reliably followed the cold(~75A) > hot(~55A) ordering the profile
# spec describes (observed files show the *second* crank with a higher |I|
# than the first, and both were single-digit amps, well below any realistic
# crank current — likely a low-current bench/validation run). The event
# script order is fixed and consistent across every file inspected so far
# (crank_pulse always at Event_Index 5 then 11), so classify by chronological
# occurrence (1st = cold, 2nd = hot) rather than by current magnitude.
CRANK_VMIN_WIN_S       = 0.3    # window from event start used to find V_min
CRANK_SUSTAIN_WIN_S    = 1.0    # trailing window used for V_sustained
OCV_WINDOW_TAIL_S      = 20.0   # tail of the 60 s ocv_window used for V_pre
PREV_TAIL_WIN_S        = 1.0    # trailing window of any event, used as the next event's V_pre reference
RECOVERY_OFFSET_S      = 30.0   # fixed offset into recovery_rest for V_recover
DISCHARGE_I_THRESH_A   = 0.1    # |mean current| below this -> not a discharge/charge event

# Cutoff-hit detection: there is no explicit "aborted early" flag logged yet,
# and a fixed voltage floor is unreliable — normal, complete runs are observed
# to dip to ~9.7-9.8 V during driving_aux_load without that being an abort.
# Instead, build_dataset() flags a run as cutoff-hit if its event count is
# below the max seen for that battery_id (a truncated event sequence is the
# actual symptom of an early cutoff-voltage abort per the profile script).
CUTOFF_V_THRESHOLD     = 10.0   # heuristic floor; no explicit cutoff flag is logged yet

_TIMESTAMP_RE = r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"

_V2_TESTDAY_PAT = re.compile(
    r"^(?:(?P<battery_id>.+)_)?"
    r"(?:SoCsweep|Block_(?P<block>\d+))_"
    # Ah-based sweep (ul18_12_unit3 onward) adds the target SoC before the OCV
    # measured after that run's rest, e.g. Block_02_SoC070_OCV12p80V_testday_...
    r"(?:SoC(?P<soc>\d+)_)?"
    r"OCV(?P<ocv>\d+p\d+)V_testday_(?P<log>bdps|sensor)_"
    r"(?P<ts>" + _TIMESTAMP_RE + r")\.csv$"
)
_V2_PROFILE_SINGLE_PAT = re.compile(
    r"^(?:(?P<battery_id>.+)_)?profile_single_"
    r"(?P<ts>" + _TIMESTAMP_RE + r")_(?P<log>bdps|sensor)\.csv$"
)
_V2_SOH_C5_PAT = re.compile(
    r"^(?:(?P<battery_id>.+)_)?Block_(?P<block>\d+)_SOH_C5_bdps_"
    r"(?P<ts>" + _TIMESTAMP_RE + r")\.csv$"
)

_EMPTY_DATASET_COLS = [
    "battery_id", "block", "ocv_label_V", "kind", "timestamp", "filename",
    "ocv_window_Vpre", "total_ah_discharged", "total_ah_charged",
    "run_duration_s", "n_events", "min_voltage", "final_voltage",
    "cutoff_hit", "block_c5_capacity_ah", "block_c5_reached_cutoff",
]


def _ocv_label_to_volts(raw: str) -> float:
    return float(raw.replace("p", "."))


# ── File discovery ─────────────────────────────────────────────────────────────

def find_v2_testday_runs(log_dir: str) -> list:
    """
    Discover candidate v2 test-day profile bdps logs (SoCsweep/Block testday
    runs plus ad hoc profile_single runs), paired with their sensor log when
    present. Filename-matching only — does not open the files, so legacy
    (pre-v2) files sharing the same naming convention are included here and
    filtered out later by load_run().

    A given (battery_id, block, OCV point) can have more than one bdps file
    on disk — e.g. a run aborted/coarse-sampled during the logging-rate
    regression, then re-run cleanly later. `ts` sorts lexicographically the
    same as chronologically (fixed-width YYYY-MM-DD_HH-MM-SS), so only the
    most recent file per identity is kept — the retry, not the original.
    """
    runs_by_key: dict = {}
    for fn in sorted(os.listdir(log_dir)):
        kind = None
        m = _V2_TESTDAY_PAT.match(fn)
        if m:
            kind = "SoCsweep" if m.group("block") is None else "Block"
        else:
            m = _V2_PROFILE_SINGLE_PAT.match(fn)
            if m:
                kind = "profile_single"
        if not m or m.group("log") != "bdps":
            continue
        gd = m.groupdict()
        sensor_fn = fn.replace("_bdps_", "_sensor_")
        sensor_path = os.path.join(log_dir, sensor_fn)
        battery_id = gd.get("battery_id") or "unknown"
        block = int(gd["block"]) if gd.get("block") else None
        ocv_label = _ocv_label_to_volts(gd["ocv"]) if gd.get("ocv") else None
        target_soc = int(gd["soc"]) if gd.get("soc") else None
        run = {
            "battery_id": battery_id,
            "block": block,
            "ocv_label": ocv_label,
            "target_soc": target_soc,
            "kind": kind,
            "ts": gd["ts"],
            "bdps_path": os.path.join(log_dir, fn),
            "sensor_path": sensor_path if os.path.exists(sensor_path) else None,
            "filename": fn,
        }
        key = (
            ("profile_single", battery_id, gd["ts"])
            if kind == "profile_single"
            # A re-run of an Ah-based sweep point lands on a slightly different
            # OCV, so the target SoC (when present) is the point's identity.
            else (kind, battery_id, block, ("soc", target_soc) if target_soc is not None else ocv_label)
        )
        existing = runs_by_key.get(key)
        if existing is None or run["ts"] > existing["ts"]:
            runs_by_key[key] = run
    return sorted(runs_by_key.values(), key=lambda r: r["ts"])


# ── Data loading ───────────────────────────────────────────────────────────────
# load_run() itself is intentionally NOT cached: it returns the full raw
# per-run DataFrame (bdps rows merge_asof'd against the sensor log), and
# caching that wholesale for every one of the ~150 test-day runs bloated the
# single cache file to 500+ MB, making the "cache hit" path slower to load
# than reading the CSVs fresh -- worse than no caching. It's called two
# ways: (1) interactively, one run at a time, from the Run Inspector/Compare
# Runs tabs -- a single CSV read/merge is already fast, no caching needed;
# (2) in bulk from build_dataset(), which only ever needs this run's
# *extracted features* (a small dict), not the raw frame -- so build_dataset
# caches at that smaller granularity instead (see _dataset_row() below).

def load_run(bdps_path: str, sensor_path: str | None = None) -> pd.DataFrame | None:
    """
    Load one profile run's BDPS log. Returns None if this file predates the
    v2 schema (missing Event_Type/Event_Index/Profile_Hash) so callers can
    skip it rather than crash — old and new schemas currently share the same
    filename convention. Battery_ID is not required here (not logged as a
    column yet); build_dataset() falls back to the filename-parsed battery id.
    """
    df = pd.read_csv(bdps_path)
    if not REQUIRED_V2_COLS.issubset(df.columns):
        return None
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], format="ISO8601")
    df = df.sort_values("Timestamp").reset_index(drop=True)

    if sensor_path and os.path.exists(sensor_path):
        sdf = pd.read_csv(sensor_path)
        if "timestamp" in sdf.columns:
            sdf["timestamp"] = pd.to_datetime(sdf["timestamp"], format="ISO8601")
            sdf = sdf.sort_values("timestamp")
            df = pd.merge_asof(
                df, sdf,
                left_on="Timestamp", right_on="timestamp",
                direction="nearest", tolerance=pd.Timedelta("0.1s"),
            )
    return df


# ── Event-level feature extraction ─────────────────────────────────────────────

def _event_groups(df: pd.DataFrame) -> list:
    """(event_index, event_type, sub_df) tuples in Event_Index order."""
    groups = []
    for idx, g in df.groupby("Event_Index", sort=True):
        groups.append((idx, g["Event_Type"].iloc[0], g))
    groups.sort(key=lambda x: x[0])
    return groups


def _tail_mean(g: pd.DataFrame, window_s: float) -> float:
    t = g["Elapsed_s"].to_numpy(float)
    v = g["Voltage"].to_numpy(float)
    mask = t >= (t.max() - window_s)
    return float(v[mask].mean()) if mask.any() else float(v.mean())


def _head_min(g: pd.DataFrame, window_s: float) -> tuple:
    t = g["Elapsed_s"].to_numpy(float)
    v = g["Voltage"].to_numpy(float)
    mask = t <= (t.min() + window_s)
    sub = v[mask] if mask.any() else v
    j = int(np.argmin(sub))
    return float(sub[j]), float(t[mask][j] if mask.any() else t[j])


def _value_at_offset(g: pd.DataFrame, offset_s: float) -> float:
    t = g["Elapsed_s"].to_numpy(float)
    v = g["Voltage"].to_numpy(float)
    target = t.min() + offset_s
    idx = int(np.argmin(np.abs(t - target)))
    return float(v[idx])


def _event_ah(g: pd.DataFrame) -> float:
    t = g["Elapsed_s"].to_numpy(float)
    i = g["Current"].to_numpy(float)
    dt = np.clip(np.diff(t, prepend=t[0]), 0, 10)
    return float(np.sum(i * dt) / 3600.0)  # signed: +charge, -discharge


def _tail_abs_current(g: pd.DataFrame, window_s: float) -> float:
    t = g["Elapsed_s"].to_numpy(float)
    i = np.abs(g["Current"].to_numpy(float))
    mask = t >= (t.max() - window_s)
    return float(np.median(i[mask])) if mask.any() else float(np.median(i))


# Below this current step a ΔV/ΔI ratio is not computed at all (division guard
# only; whether a computed value means anything is decided by feature_validity).
DELTA_I_COMPUTE_MIN_A = 1.0

# Short labels for the event a recovery_rest follows, when that is not a crank.
_RECOVERY_PREV_LABEL = {"ramp_like_load": "ramp", "driving_aux_load": "driving"}


def _emit_crank_feats(feats: dict, label: str, rec: dict) -> None:
    feats[f"crank_{label}_V_pre"] = rec["V_pre"]
    feats[f"crank_{label}_V_min"] = rec["V_min"]
    feats[f"crank_{label}_I_peak_A"] = rec["I_peak"]
    feats[f"crank_{label}_V_sustained"] = rec["V_sustained"]
    if rec["V_pre"] is not None and rec["I_peak"] > 0:
        feats[f"crank_{label}_R_int_apparent_mohm"] = (
            (rec["V_pre"] - rec["V_min"]) / rec["I_peak"] * 1000
        )
    # The cold crank starts from the -8 A glow load, so dividing by I_peak alone
    # reads low; dividing by the actual step does not (24.7 vs 28.5 mOhm, unit3).
    d_i = rec["I_peak"] - (rec["I_pre"] or 0.0)
    if rec["V_pre"] is not None and d_i > DELTA_I_COMPUTE_MIN_A:
        feats[f"crank_{label}_R_int_deltaI_mohm"] = (rec["V_pre"] - rec["V_min"]) / d_i * 1000


def extract_features(bdps_df: pd.DataFrame) -> dict:
    """
    Group one run's rows by Event_Index and derive one flat feature dict.
    Robust to degenerate/short runs (early cutoff-voltage hits truncate the
    event sequence) — missing event types simply produce missing features
    rather than raising. Repeatable event types (wakeup_load, ramp_like_load,
    driving_aux_load) are suffixed by occurrence count so repeats never
    silently overwrite each other.

    crank_pulse / recovery_rest / alternator_charge labelling (cold vs hot)
    is resolved in a second pass, after all events have been seen, since it
    depends on which occurrence a given event was — see the module-level
    comment on crank classification.
    """
    feats: dict = {}
    groups = _event_groups(bdps_df)

    prev_tail_v = None               # trailing voltage of the previous event -> next event's V_pre
    prev_tail_i = None               # trailing |current| of the previous event -> crank I_pre
    prev_etype = None
    ah_discharged_running = 0.0      # running tally, used by alternator_charge ratio
    ah_discharged_total = 0.0
    ah_charged_total = 0.0
    wakeup_count = 0
    glow_count = 0
    ramp_count = 0
    driving_count = 0

    crank_records = []       # [{idx, V_pre, V_min, I_peak, V_sustained}, ...] in chronological order
    recovery_records = []    # [{idx, prior_crank_idx, V_recover}, ...]
    alt_records = []         # [{idx, prior_crank_idx, Ah, ratio}, ...]
    last_crank_idx = None

    for idx, etype, g in groups:
        signed_ah = _event_ah(g)
        if signed_ah < 0:
            ah_discharged_total += -signed_ah
        else:
            ah_charged_total += signed_ah

        ref_v = prev_tail_v  # voltage right before this event started

        if etype == "rest_baseline":
            feats["rest_baseline_Vmean"] = float(g["Voltage"].mean())

        elif etype == "ocv_window":
            feats["ocv_window_Vpre"] = _tail_mean(g, OCV_WINDOW_TAIL_S)

        elif etype == "wakeup_load":
            wakeup_count += 1
            v_load = float(g["Voltage"].mean())
            i_load = float(g["Current"].mean())
            if ref_v is not None and abs(i_load) > DISCHARGE_I_THRESH_A:
                feats[f"wakeup_load_{wakeup_count}_sag_V"] = ref_v - v_load
                feats[f"wakeup_load_{wakeup_count}_R_int_mohm"] = (
                    (ref_v - v_load) / abs(i_load) * 1000
                )

        elif etype == "glow_plug_like_load":
            glow_count += 1
            feats[f"glow_plug_like_load_{glow_count}_Vmean"] = float(g["Voltage"].mean())
            # No rest before crank by design -> this event's tail voltage
            # (captured generically via prev_tail_v below) becomes the next
            # crank_pulse's V_pre reference.

        elif etype == "crank_pulse":
            i_peak = float(g["Current"].abs().max())
            v_min, _ = _head_min(g, CRANK_VMIN_WIN_S)
            v_sustained = _tail_mean(g, CRANK_SUSTAIN_WIN_S)
            crank_records.append({
                "idx": idx, "V_pre": ref_v, "V_min": v_min, "I_pre": prev_tail_i,
                "I_peak": i_peak, "V_sustained": v_sustained,
            })
            last_crank_idx = idx

        elif etype == "recovery_rest":
            v_recover = _value_at_offset(g, RECOVERY_OFFSET_S)
            # Only the rest that directly follows a crank is "after" it. Until
            # 2026-11-17 every rest took the label of the last crank, so the rest
            # after the ramp (event 10) overwrote recovery_after_cold_V_plus30s.
            recovery_records.append({
                "idx": idx, "V_recover": v_recover,
                "prior_crank_idx": last_crank_idx if prev_etype == "crank_pulse" else None,
                "prev_label": _RECOVERY_PREV_LABEL.get(prev_etype, prev_etype),
            })

        elif etype == "alternator_charge":
            ratio = (
                signed_ah / ah_discharged_running * 100
                if ah_discharged_running > 0 else None
            )
            alt_records.append({
                "idx": idx, "prior_crank_idx": last_crank_idx,
                "Ah": signed_ah, "ratio": ratio,
            })

        elif etype in ("ramp_like_load", "driving_aux_load"):
            if etype == "ramp_like_load":
                ramp_count += 1
                n = ramp_count
            else:
                driving_count += 1
                n = driving_count
            v = g["Voltage"].to_numpy(float)
            i = g["Current"].to_numpy(float)
            feats[f"{etype}_{n}_V_mean"] = float(v.mean())
            feats[f"{etype}_{n}_V_std"] = float(v.std())
            if len(np.unique(i)) >= 2:
                slope = float(np.polyfit(i, v, 1)[0])
                feats[f"{etype}_{n}_R_int_est_mohm"] = abs(slope) * 1000

        # Running discharge tally feeds the *next* alternator_charge event's ratio.
        if float(g["Current"].mean()) < -DISCHARGE_I_THRESH_A:
            ah_discharged_running += -signed_ah

        prev_tail_v = _tail_mean(g, PREV_TAIL_WIN_S)
        prev_tail_i = _tail_abs_current(g, PREV_TAIL_WIN_S)
        prev_etype = etype

    # ── resolve crank cold/hot labels by chronological occurrence ───────────
    crank_label_by_idx: dict = {}
    if len(crank_records) == 1:
        crank_label_by_idx[crank_records[0]["idx"]] = "1"
        _emit_crank_feats(feats, "1", crank_records[0])
    else:
        labels = ["cold", "hot"] + [f"extra{n}" for n in range(1, len(crank_records))]
        for rec, label in zip(crank_records, labels):
            crank_label_by_idx[rec["idx"]] = label
            _emit_crank_feats(feats, label, rec)

    for rec in recovery_records:
        label = crank_label_by_idx.get(rec["prior_crank_idx"]) or rec["prev_label"] or f"evt{rec['idx']}"
        feats[f"recovery_after_{label}_V_plus{int(RECOVERY_OFFSET_S)}s"] = rec["V_recover"]

    for rec in alt_records:
        label = crank_label_by_idx.get(rec["prior_crank_idx"], f"evt{rec['idx']}")
        feats[f"alternator_charge_after_{label}_Ah"] = rec["Ah"]
        feats[f"alternator_charge_after_{label}_ratio_pct"] = rec["ratio"]

    feats["total_ah_discharged"] = ah_discharged_total
    feats["total_ah_charged"] = ah_charged_total
    feats["run_duration_s"] = float(bdps_df["Elapsed_s"].max())
    feats["n_events"] = int(bdps_df["Event_Index"].nunique())
    feats["min_voltage"] = float(bdps_df["Voltage"].min())
    feats["final_voltage"] = float(bdps_df["Voltage"].iloc[-1])

    return feats


# ── Feature catalogue ──────────────────────────────────────────────────────────
# One entry per column extract_features() returns for a complete run of the
# standard test-day profile (tests/test_feature_catalog.py checks both ways).
# Truncated runs can produce other names (crank_1_*, *_evt<n>_*); those are not
# catalogued, and dashboards treat them as plain numbers.
#
# event   : (Event_Type, occurrence) the feature is measured on, None = whole run
# windows : where the samples come from, relative to that event:
#           ("all",) whole event, ("head", s) first s seconds, ("tail", s) last
#           s seconds, ("prev_tail", s) last s seconds of the event before,
#           ("at", s) the sample nearest to s seconds after the event start
# rules   : validity rules, see feature_validity(). "rule@type:occ" applies the
#           rule to another event (e.g. a recovery is void if its crank was).
# kind    : feature | diagnostic | profile_constant | duplicate_of:<name>
#           profile_constant and duplicates follow the profile, not the battery,
#           and are left out of correlation views; they stay in exports.
# adc_scale: feature units per volt of ADC step, for the resolution check
#           (None = not a voltage-derived quantity)

FEATURE_KINDS = ("feature", "diagnostic", "profile_constant", "duplicate_of")

# Head-window crank features (V_min, R_int, I_peak) use "not_failed" instead of
# "tracking": the median-based status describes the whole crank, and at 10 % SoC
# the sustained part can reach the floor after a full-current inrush (Blocks 2
# and 6: I_peak 99 % and 136 % of the reachable setpoint). The inrush is checked
# by current_low and delta_i_small instead.
_CRANK_HEAD_RULES = ("not_failed", "current_low", "v_pre_floor", "few_samples")
_CRANK_R_RULES = ("not_failed", "current_low", "v_pre_floor", "delta_i_small", "few_samples")


def _cat(event, windows, formula, unit, meaning, ageing, soc, rules, field, kind="feature",
         adc_scale=None, note=""):
    return {
        "event": event, "windows": tuple(windows), "formula": formula, "unit": unit,
        "meaning": meaning, "ageing": ageing, "soc": soc, "rules": tuple(rules),
        "field": field, "kind": kind, "adc_scale": adc_scale, "note": note,
    }


def _crank_entries(label: str, occ: int, step_a: float, prev_event: str) -> dict:
    ev = ("crank_pulse", occ)
    cold = label == "cold"
    pre = ("the -8 A glow load" if cold else "a 120 s rest at 0 A")
    out = {
        f"crank_{label}_V_pre": _cat(
            ev, [("prev_tail", PREV_TAIL_WIN_S)],
            f"mean V over the last {PREV_TAIL_WIN_S:g} s of {prev_event}", "V",
            f"Voltage just before the crank, under {pre}.",
            "falls (more polarisation under load)" if cold else "little change at fixed charge",
            "strong: follows the OCV curve", ("v_pre_floor", "few_samples"),
            "V_pre_1_10V / V_pre_total_1_10V (mean of -0.5 to -0.1 s before the start)", adc_scale=1.0),
        f"crank_{label}_V_min": _cat(
            ev, [("head", CRANK_VMIN_WIN_S)],
            f"min V in the first {CRANK_VMIN_WIN_S:g} s of the crank", "V",
            "Deepest voltage dip at the crank inrush: OCV minus the ohmic and fast polarisation drop.",
            "falls (higher resistance)", "strong: starts from a lower V_pre at low SoC",
            _CRANK_HEAD_RULES,
            "V_min_1_10V / V_min_total_1_10V (min in the 2 s crank window)", adc_scale=1.0),
        f"crank_{label}_I_peak_A": _cat(
            ev, [("all",)], "max |I| over the crank", "A",
            "Peak crank current. The sink imposes it, so it follows the profile unless the "
            "voltage floor limits it.",
            "none while the current is delivered", "none, until the floor limits the current",
            _CRANK_HEAD_RULES,
            "I_load_A (field: ~400 A, set by the starter, not imposed)", kind="profile_constant"),
        f"crank_{label}_V_sustained": _cat(
            ev, [("tail", CRANK_SUSTAIN_WIN_S)],
            f"mean V over the last {CRANK_SUSTAIN_WIN_S:g} s of the crank", "V",
            "Voltage after the inrush, at the sustained crank current: adds slower polarisation.",
            "falls", "strong", ("tracking", "current_low", "few_samples"),
            "none exact; closest V_rec_1s_1_10V", adc_scale=1.0),
        f"crank_{label}_R_int_apparent_mohm": _cat(
            ev, [("prev_tail", PREV_TAIL_WIN_S), ("head", CRANK_VMIN_WIN_S)],
            "(V_pre - V_min) / I_peak x 1000", "mOhm",
            "Apparent resistance, dividing by the peak current rather than the step.",
            "rises", "moderate: rises at low SoC", _CRANK_R_RULES,
            "R_internal_est_ohm (per group / _pack)",
            kind="diagnostic" if cold else "duplicate_of:crank_hot_R_int_deltaI_mohm",
            adc_scale=1000.0 / step_a,
            note=("Reads low: the cold crank starts from -8 A, so I_peak overstates the step. "
                  "Use crank_cold_R_int_deltaI_mohm.") if cold else
                 "The hot crank starts from ~0 A, so this equals the deltaI version to <0.1 %."),
        f"crank_{label}_R_int_deltaI_mohm": _cat(
            ev, [("prev_tail", PREV_TAIL_WIN_S), ("head", CRANK_VMIN_WIN_S)],
            f"(V_pre - V_min) / (I_peak - I_pre) x 1000; I_pre = median |I| over the last "
            f"{PREV_TAIL_WIN_S:g} s of {prev_event}", "mOhm",
            "DC resistance at the crank step: ohmic plus the polarisation reached within "
            f"{CRANK_VMIN_WIN_S:g} s.",
            "rises", "moderate: rises at low SoC", _CRANK_R_RULES,
            "R_internal_est_ohm (per group / _pack), check with invalid_R_int",
            adc_scale=1000.0 / step_a),
    }
    return out


FEATURE_CATALOG: dict = {
    "rest_baseline_Vmean": _cat(
        ("rest_baseline", 1), [("all",)], "mean V over the whole rest_baseline event (390 s, 0 A)", "V",
        "Open-circuit voltage after the sweep rest: a charge-level indicator.",
        "none at a fixed charge; at a fixed relative SoC it rises, because less Ah is removed",
        "strong: the OCV curve", ("few_samples",),
        "V_ocv_1_10V / V_ocv_total_1_10V (30 s window ending 5 s before the start)", adc_scale=1.0),
    "ocv_window_Vpre": _cat(
        ("ocv_window", 1), [("tail", OCV_WINDOW_TAIL_S)],
        f"mean V over the last {OCV_WINDOW_TAIL_S:g} s of ocv_window (0 A)", "V",
        "Open-circuit voltage just before the day starts: a charge-level indicator.",
        "as rest_baseline_Vmean", "strong: the OCV curve", ("few_samples",),
        "V_ocv_* ; field window is often not a true rest (I_pre_A)", adc_scale=1.0),
    "wakeup_load_1_sag_V": _cat(
        ("wakeup_load", 1), [("prev_tail", PREV_TAIL_WIN_S), ("all",)],
        f"V_ref - mean V over the event; V_ref = mean V over the last {PREV_TAIL_WIN_S:g} s of ocv_window",
        "V", "Voltage drop under the 1 A wake-up load: ohmic plus early polarisation.",
        "rises", "weak; rises at low SoC", ("tracking", "few_samples"),
        "derivable as V_ocv - V_pre with I_pre_A; not computed yet", adc_scale=1.0),
    "wakeup_load_1_R_int_mohm": _cat(
        ("wakeup_load", 1), [("prev_tail", PREV_TAIL_WIN_S), ("all",)],
        "wakeup_load_1_sag_V / mean |I| x 1000", "mOhm",
        "Resistance at 1 A over 120 s: includes slow polarisation, so higher than the crank value.",
        "rises", "weak; rises at low SoC", ("tracking", "delta_i_small", "few_samples"),
        "derivable as (V_ocv - V_pre) / I_pre_A; not computed yet", adc_scale=1000.0 / 1.0),
    "wakeup_load_2_sag_V": _cat(
        ("wakeup_load", 2), [("prev_tail", PREV_TAIL_WIN_S), ("all",)],
        "V_ref - mean V over the event; V_ref = mean V over the last 1 s of wakeup_load 1 (under 1 A)",
        "V", "Mixes the relaxation after wakeup 1 with a variable 0-2 A load; not a clean sag.",
        "unclear", "moderate", ("tracking", "few_samples"), "none", kind="diagnostic", adc_scale=1.0,
        note="Reference is taken under load, so the value is often negative."),
    "wakeup_load_2_R_int_mohm": _cat(
        ("wakeup_load", 2), [("prev_tail", PREV_TAIL_WIN_S), ("all",)],
        "wakeup_load_2_sag_V / mean |I| x 1000", "mOhm",
        "Not a resistance: the reference voltage is taken under the wakeup-1 load.",
        "n/a", "n/a", ("never",), "none", kind="diagnostic",
        note="Unusable: negative in 132 of 133 unit2 runs (V_ref under load, see unit2 NOTES.md)."),
    "glow_plug_like_load_1_Vmean": _cat(
        ("glow_plug_like_load", 1), [("all",)], "mean V over the 8 A glow load (45 s)", "V",
        "Loaded voltage at a moderate current: charge level plus resistance.",
        "falls", "strong", ("tracking", "few_samples"),
        "voltage during pre-glow is in the CAN grid series, not yet a feature; timing only "
        "(t_glow_plug_s, glow_lead_s)", adc_scale=1.0),
    **_crank_entries("cold", 1, 52.0, "glow_plug_like_load"),
    **_crank_entries("hot", 2, 30.0, "the recovery_rest after the ramp"),
    "recovery_after_cold_V_plus30s": _cat(
        ("recovery_rest", 1), [("at", RECOVERY_OFFSET_S)],
        f"V at {RECOVERY_OFFSET_S:g} s into the rest after the cold crank (0 A)", "V",
        "Relaxation 30 s after the cold crank.",
        "falls slightly (slower recovery)", "strong: relaxes toward the OCV",
        ("few_samples", "current_low@crank_pulse:1"),
        "V_rec_30s_1_10V, but in the field the alternator charges by then (~14.2 V): not comparable",
        adc_scale=1.0),
    "recovery_after_ramp_V_plus30s": _cat(
        ("recovery_rest", 2), [("at", RECOVERY_OFFSET_S)],
        f"V at {RECOVERY_OFFSET_S:g} s into the rest after the 0-30 A ramp (0 A)", "V",
        "Relaxation 30 s after the ramp load.",
        "falls slightly", "strong: relaxes toward the OCV",
        ("few_samples", "tracking@ramp_like_load:1"), "none", adc_scale=1.0,
        note="Before 2026-11-17 this value was stored as recovery_after_cold_V_plus30s by mistake."),
    "recovery_after_hot_V_plus30s": _cat(
        ("recovery_rest", 3), [("at", RECOVERY_OFFSET_S)],
        f"V at {RECOVERY_OFFSET_S:g} s into the rest after the hot crank (0 A)", "V",
        "Relaxation 30 s after the hot crank.",
        "falls slightly", "strong: relaxes toward the OCV",
        ("few_samples", "current_low@crank_pulse:2"),
        "V_rec_30s_1_10V (alternator active in the field: not comparable)", adc_scale=1.0),
    "alternator_charge_after_cold_Ah": _cat(
        ("alternator_charge", 1), [("all",)],
        "integral of I dt over alternator_charge (+1.8 to +4.5 A, 15 V ceiling)", "Ah",
        "Charge accepted: below the setpoint once the voltage meets the 15 V ceiling.",
        "falls (less charge acceptance)", "strong: a full battery accepts less",
        ("few_samples",), "I_mean_post_A, I_max_post_A, t_charge_onset_s (not yet linked)"),
    "alternator_charge_after_cold_ratio_pct": _cat(
        ("alternator_charge", 1), [("all",)],
        "alternator_charge_after_cold_Ah / Ah discharged so far in the run x 100", "%",
        "Charge returned relative to what the morning events took out.",
        "falls", "strong", ("few_samples",), "none"),
    "driving_aux_load_1_V_mean": _cat(
        ("driving_aux_load", 1), [("all",)], "mean V over driving_aux_load (180 s, -1 to -10 A)", "V",
        "Loaded voltage under a varying load without alternator.",
        "falls", "strong", ("tracking", "few_samples"), "post_start_V_mean_1_10V (alternator active)",
        adc_scale=1.0),
    "driving_aux_load_1_V_std": _cat(
        ("driving_aux_load", 1), [("all",)], "std of V over driving_aux_load", "V",
        "Voltage spread under the varying load: resistance times the current spread.",
        "rises", "moderate", ("tracking", "few_samples"), "post_start_V_std_1_10V (alternator active)",
        adc_scale=1.0),
    "driving_aux_load_1_R_int_est_mohm": _cat(
        ("driving_aux_load", 1), [("all",)], "|slope of a linear fit V(I)| over the event x 1000", "mOhm",
        "Resistance from the voltage response to the varying load (includes polarisation).",
        "rises", "moderate", ("tracking", "i_range_small", "few_samples"), "none",
        adc_scale=1000.0 / 9.0),
    "ramp_like_load_1_V_mean": _cat(
        ("ramp_like_load", 1), [("all",)], "mean V over the 0-30 A ramp (20 s)", "V",
        "Loaded voltage over the ramp.", "falls", "strong", ("tracking", "few_samples"), "none",
        adc_scale=1.0),
    "ramp_like_load_1_V_std": _cat(
        ("ramp_like_load", 1), [("all",)], "std of V over the ramp", "V",
        "Voltage spread over the ramp: mostly the ramp itself times the resistance.",
        "rises", "moderate", ("tracking", "few_samples"), "none", adc_scale=1.0),
    "ramp_like_load_1_R_int_est_mohm": _cat(
        ("ramp_like_load", 1), [("all",)], "|slope of a linear fit V(I)| over the ramp x 1000", "mOhm",
        "Resistance from the ramp's voltage-current slope.",
        "rises", "moderate", ("tracking", "i_range_small", "few_samples"), "none",
        adc_scale=1000.0 / 30.0),
    "total_ah_discharged": _cat(
        None, [("all",)], "sum of discharged Ah over all events", "Ah",
        "What the profile took out; set by the profile.", "none", "none, unless the floor limits",
        (), "none", kind="profile_constant"),
    "total_ah_charged": _cat(
        None, [("all",)], "sum of charged Ah over all events", "Ah",
        "Only alternator_charge charges, so this is that event's Ah.", "see that feature", "",
        (), "none", kind="duplicate_of:alternator_charge_after_cold_Ah"),
    "run_duration_s": _cat(
        None, [("all",)], "last Elapsed_s of the run", "s", "Profile length.", "none", "none",
        (), "none", kind="profile_constant"),
    "n_events": _cat(
        None, [("all",)], "number of distinct Event_Index values", "-",
        "Completeness check: fewer events means the run stopped early (cutoff_hit).",
        "none", "none", (), "none", kind="diagnostic"),
    "min_voltage": _cat(
        None, [("all",)], "min V over the run", "V",
        "Lowest voltage of the day, usually the cold crank's V_min.", "falls", "strong", (), "none",
        kind="diagnostic", adc_scale=1.0),
    "final_voltage": _cat(
        None, [("all",)], "last V of the run (end of a 600 s rest)", "V",
        "Near-open-circuit voltage at the end: a charge-level indicator.", "as the OCV", "strong",
        (), "none", kind="diagnostic", adc_scale=1.0),
}


def catalog_kind(name: str) -> str | None:
    """'feature', 'diagnostic', 'profile_constant', 'duplicate_of' or None (not catalogued)."""
    entry = FEATURE_CATALOG.get(name)
    return entry["kind"].split(":")[0] if entry else None


def is_analysis_feature(name: str) -> bool:
    """False for columns that follow the profile or repeat another column."""
    return catalog_kind(name) in ("feature", "diagnostic")


def catalog_markdown(names: list | None = None) -> str:
    """Markdown table of the catalogue (for FEATURE_OVERVIEW.md)."""
    rows = ["| Feature | Kind | Event · window | Formula | Unit | Ageing | SoC dependence | Validity rules |",
            "|---|---|---|---|---|---|---|---|"]
    for name in names or FEATURE_CATALOG:
        e = FEATURE_CATALOG[name]
        ev = f"{e['event'][0]} #{e['event'][1]}" if e["event"] else "whole run"
        win = ", ".join(_window_text(w) for w in e["windows"])
        rules = ", ".join(e["rules"]) or "-"
        rows.append(f"| `{name}` | {e['kind']} | {ev} · {win} | {e['formula']} | {e['unit']} | "
                    f"{e['ageing']} | {e['soc']} | {rules} |")
    return "\n".join(rows)


def _window_text(w: tuple) -> str:
    kind = w[0]
    if kind == "all":
        return "whole event"
    if kind == "head":
        return f"first {w[1]:g} s"
    if kind == "tail":
        return f"last {w[1]:g} s"
    if kind == "prev_tail":
        return f"last {w[1]:g} s of the event before"
    return f"at +{w[1]:g} s"


# ── Profiles, setpoints and feature validity ─────────────────────────────────

SETPOINT_ZERO_A    = 0.2    # backend.py's _snap_zero: |setpoint| below this is 0 A
SINK_V_FLOOR       = 10.2   # BDPS voltage floor while sinking; reaching it limits the current
SINK_V_FLOOR_MARGIN = 0.05  # status "voltage_limited" when V_min is this close to the floor
V_PRE_FLOOR_MARGIN = 0.3    # V_pre this close to the floor leaves no room for the pulse's dip
TRACKING_TOL_FRAC  = 0.10   # same criterion as the Pi's own "PROFILE TRACKING FAILED" check
TRACKING_TOL_MIN_A = 0.2
MIN_SINK_SAMPLES   = 4      # fewer sink samples in an event: a stray sample of the next event
# The sink needs about 0.1 s to reach a new current from 0 A (hot crank: -35.6 A
# measured against a -55 A setpoint that lasts 0.05 s). A setpoint held shorter
# than that cannot be reached by any battery, so peak checks skip it.
CURRENT_RISE_S     = 0.1
I_PEAK_MIN_FRAC    = 0.90   # measured peak below this share of the reachable setpoint
# A ratio, not an absolute ampere value: the commanded step is set by the profile
# and does not change as the battery ages, so the threshold cannot drift with it.
# Normal runs track at 0.95-1.05; a sink held back by the floor is far below.
# Half the commanded step is where the V/I ratio stops describing the pulse the
# feature is named after.
DELTA_I_MIN_FRAC   = 0.5
MIN_WINDOW_SAMPLES = 3      # samples needed in a feature's window
AT_OFFSET_TOL_S    = 1.0    # ("at", s) windows: nearest sample must be this close


def load_profiles(paths: list) -> dict:
    """Profile_Hash -> (time, current, file name). The log's hash is over the
    Pi's CRLF file; git may rewrite line endings, so both spellings are indexed."""
    import hashlib
    out = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            raw = f.read()
        lf = raw.replace(b"\r\n", b"\n")
        df = pd.read_csv(path)
        profile = (df["Time (s)"].to_numpy(), df["Current (A)"].to_numpy(), os.path.basename(path))
        for variant in (lf, lf.replace(b"\n", b"\r\n")):
            out[hashlib.sha256(variant).hexdigest()] = profile
    return out


def setpoint_at(elapsed: np.ndarray, times: np.ndarray, currents: np.ndarray) -> np.ndarray:
    idx = np.clip(np.searchsorted(times, elapsed, side="right") - 1, 0, len(times) - 1)
    sp = currents[idx].astype(float).copy()
    sp[np.abs(sp) < SETPOINT_ZERO_A] = 0.0
    return sp


def event_occurrences(df: pd.DataFrame) -> list:
    """(event_type, occurrence, sub_df) in Event_Index order; occurrence counts
    per type from 1, the same numbering extract_features() uses."""
    counts: dict = {}
    out = []
    for _, etype, g in _event_groups(df):
        counts[etype] = counts.get(etype, 0) + 1
        out.append((etype, counts[etype], g))
    return out


def sink_status(meas: np.ndarray, sp: np.ndarray, v_min: float):
    """Tracking status of one event over its sink samples: 'ok',
    'voltage_limited' (not following, but the voltage reached the floor),
    'failed', or None when the event has no discharge setpoint."""
    sink = sp < 0
    if sink.sum() < MIN_SINK_SAMPLES:
        return None, None, None
    sp_med = float(np.median(sp[sink]))
    meas_med = float(np.median(meas[sink]))
    err = abs(meas_med - sp_med)
    tol = max(TRACKING_TOL_MIN_A, TRACKING_TOL_FRAC * abs(sp_med))
    if err <= tol:
        status = "ok"
    elif v_min <= SINK_V_FLOOR + SINK_V_FLOOR_MARGIN:
        status = "voltage_limited"
    else:
        status = "failed"
    return status, sp_med, meas_med


def event_checks(df: pd.DataFrame, sp: np.ndarray) -> dict:
    """(event_type, occurrence) -> measurements the validity rules use.
    df must be sorted by time with sp aligned to its rows."""
    df = df.assign(_sp=sp)
    checks = {}
    prev = None
    for etype, occ, g in event_occurrences(df):
        t = g["Elapsed_s"].to_numpy(float)
        v = g["Voltage"].to_numpy(float)
        i = g["Current"].to_numpy(float)
        s = g["_sp"].to_numpy(float)
        t0, t1 = float(t.min()), float(t.max())
        status, _, _ = sink_status(i, s, float(v.min()))
        settled = t >= t0 + CURRENT_RISE_S
        reach = float(np.abs(s[settled]).max()) if settled.any() else float(np.abs(s).max())
        i_peak = float(np.abs(i).max())
        c = {
            "t0": t0, "t1": t1, "n": len(g), "status": status,
            "i_peak": i_peak, "sp_reach": reach,
            "i_peak_ratio": i_peak / reach if reach >= SETPOINT_ZERO_A else None,
            "v_pre": None, "i_pre": None, "n_prev_tail": 0,
            "delta_i_ratio": None, "i_range_ratio": None,
        }
        if prev is not None:
            pt = prev["Elapsed_s"].to_numpy(float)
            tail = pt >= pt.max() - PREV_TAIL_WIN_S
            c["v_pre"] = float(prev["Voltage"].to_numpy(float)[tail].mean())
            c["i_pre"] = float(np.median(np.abs(prev["Current"].to_numpy(float)[tail])))
            c["n_prev_tail"] = int(tail.sum())
            sp_pre = float(np.median(np.abs(prev["_sp"].to_numpy(float)[tail])))
            step_sp = reach - sp_pre
            if step_sp >= SETPOINT_ZERO_A:
                c["delta_i_ratio"] = (i_peak - c["i_pre"]) / step_sp
        sink = (s < 0) & settled
        if sink.sum() >= MIN_SINK_SAMPLES:
            sp_range = float(np.ptp(np.abs(s[sink])))
            if sp_range >= SETPOINT_ZERO_A:
                c["i_range_ratio"] = float(np.ptp(np.abs(i[sink]))) / sp_range
        c["_t"] = t  # sample times for window counts; dropped before returning
        checks[(etype, occ)] = c
        prev = g
    return checks


def _window_count(c: dict, w: tuple) -> tuple:
    """(samples in window, samples needed)."""
    t = c["_t"]
    kind = w[0]
    if kind == "all":
        return len(t), MIN_WINDOW_SAMPLES
    if kind == "head":
        return int((t <= c["t0"] + w[1]).sum()), MIN_WINDOW_SAMPLES
    if kind == "tail":
        return int((t >= c["t1"] - w[1]).sum()), MIN_WINDOW_SAMPLES
    if kind == "prev_tail":
        return c["n_prev_tail"], MIN_WINDOW_SAMPLES
    near = np.abs(t - (c["t0"] + w[1])).min() if len(t) else np.inf
    return int(near <= AT_OFFSET_TOL_S), 1


def _rule_reason(rule: str, c: dict, entry: dict) -> str | None:
    if rule == "never":
        return entry["note"] or "unusable as computed"
    if rule == "tracking":
        if c["status"] in ("voltage_limited", "failed"):
            return f"event {c['status']}"
    elif rule == "not_failed":
        if c["status"] == "failed":
            return "event failed (sink not following, not at the floor)"
    elif rule == "current_low":
        r = c["i_peak_ratio"]
        if r is not None and r < I_PEAK_MIN_FRAC:
            return f"I_peak {c['i_peak']:.1f} A = {r:.0%} of the reachable setpoint {c['sp_reach']:.0f} A"
    elif rule == "v_pre_floor":
        if c["v_pre"] is not None and c["v_pre"] <= SINK_V_FLOOR + V_PRE_FLOOR_MARGIN:
            return f"V_pre {c['v_pre']:.2f} V within {V_PRE_FLOOR_MARGIN:g} V of the {SINK_V_FLOOR:g} V floor"
    elif rule == "delta_i_small":
        r = c["delta_i_ratio"]
        if r is not None and r < DELTA_I_MIN_FRAC:
            return f"dI {r:.0%} of the commanded step"
    elif rule == "i_range_small":
        r = c["i_range_ratio"]
        if r is not None and r < DELTA_I_MIN_FRAC:
            return f"current range {r:.0%} of the commanded range"
    elif rule == "few_samples":
        for w in entry["windows"]:
            n, need = _window_count(c, w)
            if n < need:
                return f"{n} sample(s) in window '{_window_text(w)}'"
    else:
        raise ValueError(f"unknown validity rule {rule!r}")
    return None


def feature_validity(df: pd.DataFrame, sp: np.ndarray, checks: dict | None = None) -> dict:
    """Catalogued feature -> reason it is invalid in this run, for invalid ones
    only. Rules are per feature, not per run: a voltage-limited crank voids its
    current, V_min and resistance, while its V_pre stays valid unless V_pre
    itself sits at the floor. Whole-run features have no rules."""
    if checks is None:
        checks = event_checks(df, sp)
    out = {}
    for name, entry in FEATURE_CATALOG.items():
        if entry["event"] is None:
            continue
        reasons = []
        for rule in entry["rules"]:
            rule_name, _, target = rule.partition("@")
            if target:
                etype, occ = target.split(":")
                key = (etype, int(occ))
            else:
                key = entry["event"]
            c = checks.get(key)
            if c is None:
                reasons.append(f"{key[0]} #{key[1]} missing")
                continue
            r = _rule_reason(rule_name, c, entry)
            if r:
                reasons.append(f"{key[0]} #{key[1]}: {r}" if target else r)
        if reasons:
            out[name] = "; ".join(dict.fromkeys(reasons))
    return out


def public_checks(checks: dict) -> dict:
    """event_checks() without the per-sample arrays (small enough to cache)."""
    return {k: {kk: vv for kk, vv in c.items() if not kk.startswith("_")} for k, c in checks.items()}


# ── Block-level SOH join ────────────────────────────────────────────────────────

SAMPLE_GAP_COARSE_S = 5.0  # median inter-sample gap above this -> dt-clip integration undercounts
SOH_CUTOFF_V = 11.0  # a genuine full C/5 discharge should end at/below this; every completed
                     # SOH_C5 file inspected so far ends at ~10.78-10.79V. A file ending well
                     # above this (e.g. Block 6 read 12.07V at the moment it was synced) most
                     # often just means the test was still running/mid-discharge when this ran
                     # — not necessarily an aborted test — so its Ah figure is a snapshot lower
                     # bound, not the final SOH reading yet.

def _block_c5_capacity_ah(df: pd.DataFrame) -> tuple:
    """Ah capacity from an already-loaded Block_<nn>_SOH_C5_bdps_*.csv file
    (same integration as the legacy discharge_c5 calc in
    battery_feature_dashboard.py). Returns (ah, reached_cutoff).

    C/5 discharge is genuinely constant-current throughout (confirmed on
    every file inspected: I steady at ~-3.6A start to finish). Normally the
    logging rate is ~1 Hz, well under the 10s dt-clip below. But starting
    Block 4 the Pi's logging interval degraded badly (median gap up to
    ~45s by Block 5) — with dt clipped to 10s, that silently undercounts Ah
    by roughly (true gap / 10), e.g. Block 5's naive integration gave ~4.9 Ah
    against a real duration-implied capacity of ~12 Ah. Since current is
    constant here, duration x mean|I| is immune to sample sparsity and is
    used instead whenever the sampling is this coarse.

    Separately, `reached_cutoff` reports whether the discharge has actually
    run down to a real cutoff voltage yet. False most commonly just means
    the C/5 discharge is still in progress on the Pi at the moment this
    file was synced (a live-updating log, not a finished one) — rescanning
    later once it completes will pick up the real value. It can also mean a
    genuinely interrupted/aborted test; either way, an Ah figure from a
    file that hasn't reached cutoff isn't a valid SOH reading yet.

    No absolute Ah floor is applied here (earlier versions rejected < 1 Ah
    as presumed-bogus) — by block 20 this battery's real measured capacity
    is itself well under 1 Ah (e.g. 0.94 Ah, ending at a genuine ~10.75 V
    cutoff, healthy ~1 Hz sampling throughout), so a fixed floor started
    silently discarding real, valid SOH readings once the battery got this
    degraded. `dis.empty` above already rejects files with no discharge
    current at all; that's the only "this file has no real reading" guard
    needed.
    """
    if "Current" not in df.columns or "Elapsed_s" not in df.columns:
        return None, False
    dis = df[df["Current"] < -0.5]
    if dis.empty:
        return None, False
    median_gap = df["Elapsed_s"].diff().median()
    if pd.notna(median_gap) and median_gap > SAMPLE_GAP_COARSE_S:
        duration_s = df["Elapsed_s"].max() - df["Elapsed_s"].min()
        ah = float(dis["Current"].abs().mean() * duration_s / 3600)
    else:
        dt = df["Elapsed_s"].diff().clip(0, 10)
        ah = float((dis["Current"].abs() * dt.loc[dis.index]).sum() / 3600)
    v_end = float(df["Voltage"].iloc[-1])
    reached_cutoff = v_end <= SOH_CUTOFF_V
    return (ah if ah > 0 else None), reached_cutoff


def find_block_soh(log_dir: str) -> dict:
    """(battery_id, block) -> (measured C/5 Ah capacity, reached_cutoff).
    Prefers the file's own Battery_ID column over the filename-parsed id
    (SOH_C5 filenames don't carry a battery_id prefix when one battery's
    runs live in their own folder, e.g. ul18_12_unit2/Block_01_SOH_C5_bdps_*.csv).

    Public and independent of build_dataset()/testday runs on purpose: a
    block's SOH is measured before that block's SoC-sweep test-day runs
    exist, so callers that need "every SOH point known so far" (e.g. a SOH
    History chart) should use this directly rather than reading
    block_c5_capacity_ah off the per-run dataset, which only carries a
    value for blocks that already have at least one test-day run.
    """
    candidates = []
    for fn in os.listdir(log_dir):
        m = _V2_SOH_C5_PAT.match(fn)
        if not m:
            continue
        path = os.path.join(log_dir, fn)
        candidates.append((path, int(m.group("block")), m.group("battery_id") or "unknown"))

    def _block_soh_row_impl(path: str, fallback_bid: str):
        try:
            df = pd.read_csv(path)
        except Exception:
            return None
        bid = fallback_bid
        if "Battery_ID" in df.columns and pd.notna(df["Battery_ID"].iloc[0]):
            bid = str(df["Battery_ID"].iloc[0])
        ah, reached_cutoff = _block_c5_capacity_ah(df)
        if ah is None:
            return None
        return bid, ah, reached_cutoff

    def _load(item):
        path, block, fallback_bid = item
        fp = _fingerprint(path)
        result = cached_call("block_soh_row", (path, fp, fallback_bid), lambda: _block_soh_row_impl(path, fallback_bid))
        return block, result

    out = {}
    for block, result in parallel_map(_load, candidates):
        if result is None:
            continue
        bid, ah, reached_cutoff = result
        out[(bid, block)] = (ah, reached_cutoff)
    flush_cache()
    return out


def find_block_soh_df(log_dir: str, battery_id: str | None = None) -> pd.DataFrame:
    """find_block_soh() as a tidy (battery_id, block, capacity_ah, reached_cutoff)
    DataFrame, optionally filtered to one battery_id, sorted by block."""
    soh = find_block_soh(log_dir)
    rows = [
        {"battery_id": bid, "block": block, "block_c5_capacity_ah": ah, "reached_cutoff": reached_cutoff}
        for (bid, block), (ah, reached_cutoff) in soh.items()
        if battery_id is None or bid == battery_id
    ]
    df = pd.DataFrame(rows, columns=["battery_id", "block", "block_c5_capacity_ah", "reached_cutoff"])
    return df.sort_values("block").reset_index(drop=True)


# ── Dataset assembly ────────────────────────────────────────────────────────────

def _dataset_row_impl(bdps_path: str, sensor_path: str | None):
    """Loads + feature-extracts one run for build_dataset(). Returns "legacy"
    (pre-v2 schema), "corrupt" (Profile_Hash not constant within the run),
    or a small dict (battery_id override + timestamp + extracted features) —
    deliberately not the raw DataFrame itself, so caching this result stays
    cheap (see the note above load_run())."""
    df = load_run(bdps_path, sensor_path)
    if df is None:
        return "legacy"
    if df["Profile_Hash"].nunique() > 1:
        return "corrupt"
    row_battery_id = None
    if "Battery_ID" in df.columns and pd.notna(df["Battery_ID"].iloc[0]):
        row_battery_id = str(df["Battery_ID"].iloc[0])
    result = {"battery_id_override": row_battery_id, "timestamp": df["Timestamp"].iloc[0]}
    result.update(extract_features(df))
    return result


def _dataset_row(bdps_path: str, sensor_path: str | None):
    bdps_fp = _fingerprint(bdps_path)
    sensor_fp = _fingerprint(sensor_path) if sensor_path else None
    # Bump the tag whenever extract_features() changes, or old rows are served.
    # v2 (2026-11-17): crank_*_R_int_deltaI_mohm added, recovery labelling fixed.
    return cached_call(
        "dataset_row_v2", (bdps_path, sensor_path, bdps_fp, sensor_fp),
        lambda: _dataset_row_impl(bdps_path, sensor_path),
    )


def build_dataset(log_root: str, battery_id: str | None = None) -> pd.DataFrame:
    """
    Glob v2 test-day profile bdps logs under log_root, parse the OCV label +
    block index + battery id from each filename, skip pre-v2 (legacy-schema)
    files, verify Profile_Hash is constant within a run, extract features,
    and assemble one row per run. Joins in the block-level C/5 Ah capacity
    where a matching Block_<nn>_SOH_C5_bdps_*.csv exists for that
    battery_id/block. Never merges rows across battery_id implicitly.
    """
    rows = []
    n_skipped_legacy = 0
    n_skipped_corrupt = 0

    infos = find_v2_testday_runs(log_root)
    # I/O-bound (CSV parse + disk read, first-run cache miss) -- threads give
    # a real speedup here since pandas' C parser releases the GIL.
    results = parallel_map(lambda i: _dataset_row(i["bdps_path"], i["sensor_path"]), infos)
    flush_cache()

    for info, result in zip(infos, results):
        if result == "legacy":
            n_skipped_legacy += 1
            continue
        if result == "corrupt":
            n_skipped_corrupt += 1
            continue

        # Prefer an actual Battery_ID column value once backend.py adds one;
        # fall back to the filename-parsed id (currently always "unknown",
        # since no run on disk carries this column yet).
        row_battery_id = result["battery_id_override"] or info["battery_id"]
        if battery_id and row_battery_id != battery_id:
            continue

        row = {
            "battery_id": row_battery_id,
            "block": info["block"],
            "ocv_label_V": info["ocv_label"],
            "kind": info["kind"],
            "timestamp": result["timestamp"],
            "filename": info["filename"],
        }
        row.update({k: v for k, v in result.items() if k not in ("battery_id_override", "timestamp")})
        rows.append(row)

    if not rows:
        result = pd.DataFrame(columns=_EMPTY_DATASET_COLS)
    else:
        result = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        soh_by_block = find_block_soh(log_root)
        result["block_c5_capacity_ah"] = result.apply(
            lambda r: soh_by_block.get((r["battery_id"], r["block"]), (None, None))[0], axis=1
        )
        result["block_c5_reached_cutoff"] = result.apply(
            lambda r: soh_by_block.get((r["battery_id"], r["block"]), (None, None))[1], axis=1
        )
        # A run is flagged cutoff-hit if its event sequence is shorter than
        # the max observed for that battery_id (see module-level note above).
        max_events = result.groupby("battery_id")["n_events"].transform("max")
        result["cutoff_hit"] = result["n_events"] < max_events

    result.attrs["n_skipped_legacy_schema"] = n_skipped_legacy
    result.attrs["n_skipped_corrupt_hash"] = n_skipped_corrupt
    return result
