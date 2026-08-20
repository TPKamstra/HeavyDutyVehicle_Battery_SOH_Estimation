#!/usr/bin/env python3
"""
build_boxer_can_features.py

One-time (re-runnable) feature-engineering step for the Boxer motor-start
CAN-tap export ("can_tapperNN" archives, one per physically tapped
vehicle/channel; tapper11 is the original 41-start dataset already used
elsewhere in this project -- see dataset_boxer/start_data_battery/my_start_csv/,
which is start-for-start identical to tapper11's features.csv).

Each archive (`motor_starts_can_tapperNN_tb<T_before>_ta<T_after>_*.zip`)
already ships small per-start feature tables (features.csv,
features_packs.csv, start_metadata.csv) plus an engine_events.csv giving
the CAN "commando" markers (Engine starting command / Glow plugs
preheating / Engine running command) and large per-start raw/gridded
voltage-current-temperature series. Only the small tables are checked into
this repo (dataset_boxer_can/<tapper>/); the multi-hundred-MB grid/raw
series stay in the original export and are read directly from the zip by
this script to derive two things not already in features.csv:

  1. Commando-derived timing: crank_duration_s (Engine starting command ->
     Engine running command) and glow_lead_s (how long before the starting
     command glow-plug preheat began; negative if it began after).
  2. post_start_V_mean_1_10V / post_start_V_std_1_10V: mean/std of the
     per-group-average voltage (grid's `V_mean_groups` column) over a
     POST_WIN_START_S..POST_WIN_END_S window after the start, i.e. the
     field-side analogue of the lab campaign's `driving_aux_load_V_std`
     feature (see lab_field_feature_comparison.py).

IMPORTANT unit note: features.csv's own `V_pre_total_1_10V` /
`V_min_total_1_10V` are the SUM of all four battery-group voltages
(~2x a single 12V-class group) -- not the vehicle's real "FM-Total
Voltage" CAN signal. This script uses the grid's `V_mean_groups` column
(mean of the four measured/estimated per-group voltages) for the new
post-start feature so it stays on the same per-group scale as
features_packs.csv's V_pre_1_10V/V_min_1_10V.

Multi-part tappers: as of the 2026-08-06 upload, some tappers are split
across two zips with non-overlapping query date-ranges because a single
export got too large (currently can_tapper14, can_tapper15). Both zips
share the same can_tapper_id and each restarts its own start_index at/near
0, so they are merged per-tapper with an incrementing index offset applied
*after* each part's own internal joins (features<->events<->grid), so
nothing collides. Exact byte-identical duplicate zips (e.g. a browser
"file (1).zip" re-download) are detected by content hash and skipped
entirely before grouping.

Usage:
    python build_boxer_can_features.py --zips-dir /path/to/Accu_dataset_Boxer_anonimised
    python build_boxer_can_features.py --zips-dir /path/to/... --extract-outlier-traces

Writes into dataset_boxer_can/:
    <tapper>/features.csv, features_packs.csv, start_metadata.csv,
             engine_events.csv, metadata.json, README.txt
             (single-part tappers, as before)
    <tapper>/part<N>/...same files...  (multi-part tappers, one subdir per zip)
    boxer_can_features_enriched.csv     (all tappers, all parts, + new columns)
    boxer_can_features_packs.csv        (all tappers, all parts, per-group rows)
    example_start_trace.csv             (one full -60..+120s grid trace)
    example_start_events.csv            (its commando events)

--extract-outlier-traces additionally writes:
    outlier_raw_traces.csv / outlier_raw_events.csv -- the full -60..+120s
    per-group voltage grid + commando events, but ONLY for the
    OUTLIER_TRACE_TOP_N most extreme starts *per tracked feature*
    (V_drop_total_pct, R_internal_est_ohm_pack, post_start_V_std_1_10V),
    ranked by IQR-normalized distance from the median -- NOT "every start
    beyond a fixed IQR fence". That fence-based approach was tried first and
    produced a 1 GB file: post_start_V_std_1_10V alone flagged 653 of 4785
    starts (13.6%) at the dashboard's loosest fence (k=1.5), because a
    *global* fence pooled across 15 different vehicles mostly just detects
    normal vehicle-to-vehicle differences in baseline accessory-load
    behaviour, not real anomalies -- the tail is fat by nature, not by
    fault. Ranking and capping at a fixed N bounds the output regardless of
    how fat any one feature's tail is. Consequence: this covers the most
    extreme rows only, not literally every row the dashboard's live
    sensitivity slider can flag -- boxer_can_dashboard.py degrades
    gracefully ("not available") for a selected start that isn't covered.
    Re-run after any re-export to pick up new extremes. This is a deliberate
    second pass over the zips (grid CSVs can't be randomly row-accessed, so
    each part's full grid is read once and filtered down).
"""
import argparse
import glob
import hashlib
import os
import shutil
import zipfile
from collections import defaultdict

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SCRIPT_DIR, "dataset_boxer_can")

# Window used for the new post-start ("driving/running") stability feature.
# Starts after the crank + fixed recovery horizons (features_packs.csv
# already reports recovery at 1/2/5/10/30/60s), ends with margin inside the
# +120s post-start data boundary.
POST_WIN_START_S = 15.0
POST_WIN_END_S = 115.0

SMALL_FILES = [
    "features.csv", "features_packs.csv", "start_metadata.csv",
    "engine_events.csv", "metadata.json", "README.txt",
]

# Field R_int reliability diagnostic (added 2026-08-20, after applying the
# same low-current/ΔV-I-noise-blowup diagnostic already run on the lab data
# -- see exports/RESULTS_SUMMARY_FOR_PAPER.md §1 for that precedent and the
# physical explanation: R_int = ΔV / I, so a small I turns ordinary voltage
# noise into a wildly inflated "resistance"). Spearman r(I_load_A, R_int) is
# -0.52 to -0.59 across all three R_internal_est_ohm* columns; below this
# threshold they cross from a bounded, physically plausible range (median
# 9-12 mOhm, max 58-100 mOhm) into implausible territory (max in the
# thousands of mOhm). All three columns divide by the same I_load_A, so they
# share ONE flag rather than three -- checked: the columns' own NaN patterns
# differ slightly (the from_ocv method fails more often for unrelated
# reasons), but whether I_load_A itself is trustworthy for a given row is
# identical across all three, so a shared flag is correct, not a
# simplification.
R_INT_MIN_CURRENT_A = 200.0
R_INT_COLS = [
    "R_internal_est_ohm_pack",
    "R_internal_est_ohm_min_voltage_method_pack",
    "R_internal_est_ohm_from_ocv_pack",
]
# can_tapper31's documented ~26h current-sensing dropout (start_index
# 388-404, 2026-06-11 19:38 to 2026-06-12 21:52) -- see
# dashboard_notes/outlier_known_incidents.md. Flagged separately from the
# general low-current pattern above because it has a known, specific root
# cause (a temporary fault on that one tap's current channel), not because
# the underlying invalidity criterion differs -- every row in this window is
# already covered by invalid_R_int too.
R_INT_DROPOUT_TAPPER = "can_tapper31"
R_INT_DROPOUT_START_RANGE = (388, 404)


def add_r_int_validity_flags(combined: pd.DataFrame) -> pd.DataFrame:
    """
    Additive only -- never drops or overwrites an existing column. Same
    flag-don't-filter convention as the lab side's `{col}_invalid` /
    `_low_current_flag` columns (export_ul18_12_unit2_summary.py,
    export_old_ul18_12_summary.py), just named for what they are here since
    one flag now covers three R_int columns at once (see comment above).

    invalid_R_int: I_load_A < R_INT_MIN_CURRENT_A or missing -- the general
        reliability flag; use this to filter any of the three R_int columns.
    R_int_current_sensor_dropout: True for can_tapper31's documented dropout
        window -- a subset of invalid_R_int with a known root cause.
    R_int_negative_current: True where I_load_A < 0 -- a sign-convention
        artifact (seen concentrated on can_tapper14), also a subset of
        invalid_R_int, also flagged separately since it's a distinct pattern.
    """
    combined["invalid_R_int"] = (combined["I_load_A"] < R_INT_MIN_CURRENT_A) | combined["I_load_A"].isna()
    combined["R_int_current_sensor_dropout"] = (
        (combined["can_tapper_id"] == R_INT_DROPOUT_TAPPER)
        & combined["start_index"].between(*R_INT_DROPOUT_START_RANGE)
    )
    combined["R_int_negative_current"] = combined["I_load_A"] < 0
    return combined


# Same three features as boxer_can_dashboard.py's Outlier Inspector tab
# (OUTLIER_FEATURES). Deliberately NOT a fixed IQR fence -- see module
# docstring for why that produced a 1 GB file (post_start_V_std_1_10V's
# fat tail is real vehicle-to-vehicle variation, not a bounded set of
# anomalies). Instead this ranks by IQR-normalized distance from the median
# and takes the top N per feature, which is bounded no matter how fat the
# tail is. Ranking is scale-invariant, so it runs directly on the raw
# (pre-mΩ/pre-volt-scaled) columns.
OUTLIER_RAW_COLS = ["V_drop_total_pct", "R_internal_est_ohm_pack", "post_start_V_std_1_10V"]
OUTLIER_TRACE_TOP_N = 60


def _tapper_id_from_zipname(fn: str) -> str:
    # motor_starts_can_tapperNN_tb60_ta120_<timestamp>[ (1)].zip -> can_tapperNN
    base = os.path.basename(fn)
    return base.split("_tb")[0].replace("motor_starts_", "")


def _file_hash(path: str, block: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _dedupe_zips(zip_paths: list) -> list:
    """Drop byte-identical duplicate zips (e.g. a re-downloaded 'name (1).zip')."""
    seen: dict = {}
    keep = []
    for zp in sorted(zip_paths):
        h = _file_hash(zp)
        if h in seen:
            print(f"Skipping exact duplicate of {os.path.basename(seen[h])}: {os.path.basename(zp)}")
            continue
        seen[h] = zp
        keep.append(zp)
    return keep


def _outlier_keys(combined: pd.DataFrame, top_n: int = OUTLIER_TRACE_TOP_N) -> set:
    """(can_tapper_id, GLOBAL start_index) pairs for the top_n most extreme
    starts on EACH tracked feature (union across features, deduped) -- bounded
    at 3 * top_n keys regardless of any feature's tail shape. 'Extreme' =
    |value - median| / IQR; a fixed IQR fence isn't used here on purpose (see
    module docstring)."""
    keys = set()
    for col in OUTLIER_RAW_COLS:
        if col not in combined.columns:
            continue
        valid = combined[col].dropna()
        if len(valid) < 5:
            continue
        median = valid.median()
        q1, q3 = valid.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr == 0:
            continue
        severity = (valid - median).abs() / iqr
        top = severity.nlargest(top_n)
        flagged = combined.loc[top.index, ["can_tapper_id", "start_index"]]
        keys.update(map(tuple, flagged.itertuples(index=False, name=None)))
    return keys


def _process_part(z: zipfile.ZipFile):
    """Read one zip's small tables + grid, return (feat, feat_pk, events) with
    crank/glow/post-start features merged in, all still on that zip's own
    local start_index (no offset applied yet)."""
    feat = pd.read_csv(z.open("features.csv"), parse_dates=["start_time"])
    feat_pk = pd.read_csv(z.open("features_packs.csv"), parse_dates=["start_time"])
    events = pd.read_csv(z.open("engine_events.csv"), parse_dates=["start_time", "event_time"])

    ev = events.pivot_table(index="start_index", columns="event_label", values="t_rel", aggfunc="first")
    ev = ev.rename(columns={
        "Engine starting command": "t_starting_cmd_s",
        "Glow plugs preheating": "t_glow_plug_s",
        "Engine running command": "t_running_cmd_s",
    })
    ev["crank_duration_s"] = ev.get("t_running_cmd_s") - ev.get("t_starting_cmd_s")
    ev["glow_lead_s"] = -ev.get("t_glow_plug_s")
    ev = ev.reset_index()

    with z.open("voltage_series_grid.csv") as f:
        grid = pd.read_csv(f, usecols=["start_index", "t_rel", "V_mean_groups"])
    grid = grid.rename(columns={"V_mean_groups": "V_group_avg_1_10V"})
    post = grid[(grid["t_rel"] >= POST_WIN_START_S) & (grid["t_rel"] <= POST_WIN_END_S)]
    post_stats = post.groupby("start_index")["V_group_avg_1_10V"].agg(
        post_start_V_mean_1_10V="mean",
        post_start_V_std_1_10V="std",
        n_post_window_samples="count",
    ).reset_index()
    del grid

    feat = feat.merge(ev, on="start_index", how="left")
    feat = feat.merge(post_stats, on="start_index", how="left")
    return feat, feat_pk, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips-dir", required=True, help="Directory containing motor_starts_can_tapper*.zip")
    ap.add_argument("--example-tapper", default="can_tapper11")
    ap.add_argument("--extract-outlier-traces", action="store_true",
                     help="Also write outlier_raw_traces.csv/outlier_raw_events.csv (see module docstring)")
    args = ap.parse_args()

    zip_paths = sorted(glob.glob(os.path.join(args.zips_dir, "motor_starts_*.zip")))
    if not zip_paths:
        raise SystemExit(f"No motor_starts_*.zip files found in {args.zips_dir}")
    zip_paths = _dedupe_zips(zip_paths)

    by_tapper = defaultdict(list)
    for zp in zip_paths:
        by_tapper[_tapper_id_from_zipname(zp)].append(zp)

    os.makedirs(OUT_DIR, exist_ok=True)
    all_feat, all_feat_pk = [], []
    example_trace = example_events = None
    part_manifest = []  # (tapper, zip_path, offset) -- reused for --extract-outlier-traces' 2nd pass

    for tapper, parts in sorted(by_tapper.items()):
        parts = sorted(parts)  # timestamp suffix sorts chronologically
        multi_part = len(parts) > 1
        tdir = os.path.join(OUT_DIR, tapper)
        os.makedirs(tdir, exist_ok=True)

        part_feats, part_feat_pks = [], []
        index_offset = 0

        for part_i, zp in enumerate(parts):
            part_manifest.append((tapper, zp, index_offset))
            dest_dir = os.path.join(tdir, f"part{part_i}") if multi_part else tdir
            os.makedirs(dest_dir, exist_ok=True)

            with zipfile.ZipFile(zp) as z:
                for name in SMALL_FILES:
                    with z.open(name) as src, open(os.path.join(dest_dir, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)

                feat, feat_pk, events = _process_part(z)

                if tapper == args.example_tapper and example_trace is None:
                    ex_idx = feat["start_index"].iloc[0]
                    with z.open("voltage_series_grid.csv") as f2:
                        full_grid = pd.read_csv(f2)
                    example_trace = full_grid[full_grid["start_index"] == ex_idx].copy()
                    example_trace["can_tapper_id"] = tapper
                    example_events = events[events["start_index"] == ex_idx].copy()

            # Offset AFTER this part's own internal joins are done, so multiple
            # parts of the same tapper never collide once concatenated.
            feat["start_index"] = feat["start_index"] + index_offset
            feat_pk["start_index"] = feat_pk["start_index"] + index_offset
            feat["zip_part"] = part_i
            feat_pk["zip_part"] = part_i

            part_feats.append(feat)
            part_feat_pks.append(feat_pk)
            index_offset = int(feat["start_index"].max()) + 1

        tapper_feat = pd.concat(part_feats, ignore_index=True)
        tapper_feat_pk = pd.concat(part_feat_pks, ignore_index=True)
        tapper_feat["can_tapper_id"] = tapper
        tapper_feat_pk["can_tapper_id"] = tapper

        all_feat.append(tapper_feat)
        all_feat_pk.append(tapper_feat_pk)
        print(f"{tapper}: {len(tapper_feat)} starts processed across {len(parts)} zip part(s)")

    combined = pd.concat(all_feat, ignore_index=True)
    combined_pk = pd.concat(all_feat_pk, ignore_index=True)
    combined = add_r_int_validity_flags(combined)

    combined.to_csv(os.path.join(OUT_DIR, "boxer_can_features_enriched.csv"), index=False)
    combined_pk.to_csv(os.path.join(OUT_DIR, "boxer_can_features_packs.csv"), index=False)
    example_trace.to_csv(os.path.join(OUT_DIR, "example_start_trace.csv"), index=False)
    example_events.to_csv(os.path.join(OUT_DIR, "example_start_events.csv"), index=False)

    print(f"\nWrote {len(combined)} starts across {combined['can_tapper_id'].nunique()} tappers to {OUT_DIR}")

    if args.extract_outlier_traces:
        outlier_keys = _outlier_keys(combined)
        print(f"\n{len(outlier_keys)} (tapper, start_index) pairs in the top {OUTLIER_TRACE_TOP_N} most extreme "
              f"per feature -- extracting raw traces...")
        trace_cols = ["start_index", "t_rel"] + list({
            "FM-Center Voltage PG1_est in 1/10V", "FM-Center Voltage PG2 in 1/10V",
            "FM-Center Voltage PG3_est in 1/10V", "FM-Center Voltage PG4 in 1/10V",
        })
        trace_parts, event_parts = [], []
        for tapper, zp, offset in part_manifest:
            local_targets = {gi - offset for (t, gi) in outlier_keys if t == tapper}
            if not local_targets:
                continue
            with zipfile.ZipFile(zp) as z:
                with z.open("voltage_series_grid.csv") as f:
                    grid = pd.read_csv(f, usecols=lambda c: c in trace_cols)
                sub = grid[grid["start_index"].isin(local_targets)].copy()
                if not sub.empty:
                    sub["start_index"] = sub["start_index"] + offset
                    sub["can_tapper_id"] = tapper
                    trace_parts.append(sub)
                del grid

                with z.open("engine_events.csv") as f2:
                    events = pd.read_csv(f2, parse_dates=["start_time", "event_time"])
                esub = events[events["start_index"].isin(local_targets)].copy()
                if not esub.empty:
                    esub["start_index"] = esub["start_index"] + offset
                    esub["can_tapper_id"] = tapper
                    event_parts.append(esub)

        if trace_parts:
            all_traces = pd.concat(trace_parts, ignore_index=True)
            all_traces.to_csv(os.path.join(OUT_DIR, "outlier_raw_traces.csv"), index=False)
            all_events = pd.concat(event_parts, ignore_index=True) if event_parts else pd.DataFrame()
            all_events.to_csv(os.path.join(OUT_DIR, "outlier_raw_events.csv"), index=False)
            print(f"Wrote raw traces for {all_traces[['can_tapper_id', 'start_index']].drop_duplicates().shape[0]} "
                  f"flagged starts -> outlier_raw_traces.csv / outlier_raw_events.csv")
        else:
            print("No flagged starts found -- outlier_raw_traces.csv not written.")


if __name__ == "__main__":
    main()
