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
import re

import numpy as np
import pandas as pd

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
        run = {
            "battery_id": battery_id,
            "block": block,
            "ocv_label": ocv_label,
            "kind": kind,
            "ts": gd["ts"],
            "bdps_path": os.path.join(log_dir, fn),
            "sensor_path": sensor_path if os.path.exists(sensor_path) else None,
            "filename": fn,
        }
        key = (
            ("profile_single", battery_id, gd["ts"])
            if kind == "profile_single"
            else (kind, battery_id, block, ocv_label)
        )
        existing = runs_by_key.get(key)
        if existing is None or run["ts"] > existing["ts"]:
            runs_by_key[key] = run
    return sorted(runs_by_key.values(), key=lambda r: r["ts"])


# ── Data loading ───────────────────────────────────────────────────────────────

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


def _emit_crank_feats(feats: dict, label: str, rec: dict) -> None:
    feats[f"crank_{label}_V_pre"] = rec["V_pre"]
    feats[f"crank_{label}_V_min"] = rec["V_min"]
    feats[f"crank_{label}_I_peak_A"] = rec["I_peak"]
    feats[f"crank_{label}_V_sustained"] = rec["V_sustained"]
    if rec["V_pre"] is not None and rec["I_peak"] > 0:
        feats[f"crank_{label}_R_int_apparent_mohm"] = (
            (rec["V_pre"] - rec["V_min"]) / rec["I_peak"] * 1000
        )


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
                "idx": idx, "V_pre": ref_v, "V_min": v_min,
                "I_peak": i_peak, "V_sustained": v_sustained,
            })
            last_crank_idx = idx

        elif etype == "recovery_rest":
            v_recover = _value_at_offset(g, RECOVERY_OFFSET_S)
            recovery_records.append({
                "idx": idx, "prior_crank_idx": last_crank_idx, "V_recover": v_recover,
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
        label = crank_label_by_idx.get(rec["prior_crank_idx"], f"evt{rec['idx']}")
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
    out = {}
    for fn in os.listdir(log_dir):
        m = _V2_SOH_C5_PAT.match(fn)
        if not m:
            continue
        block = int(m.group("block"))
        try:
            df = pd.read_csv(os.path.join(log_dir, fn))
        except Exception:
            continue
        bid = m.group("battery_id") or "unknown"
        if "Battery_ID" in df.columns and pd.notna(df["Battery_ID"].iloc[0]):
            bid = str(df["Battery_ID"].iloc[0])
        ah, reached_cutoff = _block_c5_capacity_ah(df)
        if ah is not None:
            out[(bid, block)] = (ah, reached_cutoff)
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

    for info in find_v2_testday_runs(log_root):
        df = load_run(info["bdps_path"], info["sensor_path"])
        if df is None:
            n_skipped_legacy += 1
            continue
        if df["Profile_Hash"].nunique() > 1:
            n_skipped_corrupt += 1
            continue

        # Prefer an actual Battery_ID column value once backend.py adds one;
        # fall back to the filename-parsed id (currently always "unknown",
        # since no run on disk carries this column yet).
        row_battery_id = info["battery_id"]
        if "Battery_ID" in df.columns and pd.notna(df["Battery_ID"].iloc[0]):
            row_battery_id = str(df["Battery_ID"].iloc[0])
        if battery_id and row_battery_id != battery_id:
            continue

        row = {
            "battery_id": row_battery_id,
            "block": info["block"],
            "ocv_label_V": info["ocv_label"],
            "kind": info["kind"],
            "timestamp": df["Timestamp"].iloc[0],
            "filename": info["filename"],
        }
        row.update(extract_features(df))
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
