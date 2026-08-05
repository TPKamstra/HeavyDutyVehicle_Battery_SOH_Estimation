#!/usr/bin/env python3
"""
build_boxer_can_features.py

One-time (re-runnable) feature-engineering step for the Boxer motor-start
CAN-tap export (7 "can_tapperNN" archives as of 2026-08-05, one per
physically tapped vehicle/channel; tapper11 is the original 41-start
dataset already used elsewhere in this project -- see
dataset_boxer/start_data_battery/my_start_csv/, which is start-for-start
identical to tapper11's features.csv).

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
     feature -- the single strongest SoC-robust lab indicator (see
     main.tex, Section 5.1) and the reason this dataset was worth pulling
     in the first place: the existing 41-start field dataset only ever
     captured crank-window features, so it could not test whether that
     lab finding transfers.

IMPORTANT unit note: features.csv's own `V_pre_total_1_10V` /
`V_min_total_1_10V` are the SUM of all four battery-group voltages
(~2x a single 12V-class group, ~49 V at rest for this 2S2P 24V-nominal
pack) -- not the vehicle's real "FM-Total Voltage" CAN signal, which
(confirmed against the grid file directly) reads ~24-29V. This script
therefore deliberately uses the grid's `V_mean_groups` column (mean of the
four measured/estimated per-group voltages, ~12-15V scale) for the new
post-start feature, so it is on the same per-group scale as
features_packs.csv's V_pre_1_10V/V_min_1_10V and comparable pre- vs
post-start, rather than silently mixing two different "total voltage"
conventions.

Usage:
    python build_boxer_can_features.py --zips-dir /path/to/Accu_dataset_Boxer_anonimised

Writes into dataset_boxer_can/:
    <tapper>/features.csv, features_packs.csv, start_metadata.csv,
             engine_events.csv, metadata.json, README.txt   (copied as-is)
    boxer_can_features_enriched.csv     (all tappers, + new columns)
    boxer_can_features_packs.csv        (all tappers, per-group rows)
    example_start_trace.csv             (one full -60..+120s grid trace)
    example_start_events.csv            (its commando events)
"""
import argparse
import glob
import os
import shutil
import zipfile

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


def _tapper_id_from_zipname(fn: str) -> str:
    # motor_starts_can_tapperNN_tb60_ta120_<timestamp>.zip -> can_tapperNN
    base = os.path.basename(fn)
    return base.split("_tb")[0].replace("motor_starts_", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips-dir", required=True, help="Directory containing motor_starts_can_tapper*.zip")
    ap.add_argument("--example-tapper", default="can_tapper11")
    args = ap.parse_args()

    zip_paths = sorted(glob.glob(os.path.join(args.zips_dir, "motor_starts_*.zip")))
    if not zip_paths:
        raise SystemExit(f"No motor_starts_*.zip files found in {args.zips_dir}")

    os.makedirs(OUT_DIR, exist_ok=True)

    all_feat, all_feat_pk = [], []
    example_trace = example_events = None

    for zp in zip_paths:
        tapper = _tapper_id_from_zipname(zp)
        tdir = os.path.join(OUT_DIR, tapper)
        os.makedirs(tdir, exist_ok=True)

        with zipfile.ZipFile(zp) as z:
            for name in SMALL_FILES:
                with z.open(name) as src, open(os.path.join(tdir, name), "wb") as dst:
                    shutil.copyfileobj(src, dst)

            feat = pd.read_csv(os.path.join(tdir, "features.csv"), parse_dates=["start_time"])
            feat_pk = pd.read_csv(os.path.join(tdir, "features_packs.csv"), parse_dates=["start_time"])
            events = pd.read_csv(os.path.join(tdir, "engine_events.csv"), parse_dates=["start_time", "event_time"])

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

            if tapper == args.example_tapper and example_trace is None:
                ex_idx = feat["start_index"].iloc[0]
                with z.open("voltage_series_grid.csv") as f:
                    full_grid = pd.read_csv(f)
                example_trace = full_grid[full_grid["start_index"] == ex_idx].copy()
                example_trace["can_tapper_id"] = tapper
                example_events = events[events["start_index"] == ex_idx].copy()
            del grid

        feat = feat.merge(ev, on="start_index", how="left")
        feat = feat.merge(post_stats, on="start_index", how="left")
        feat["can_tapper_id"] = tapper

        all_feat.append(feat)
        all_feat_pk.append(feat_pk.assign(can_tapper_id=tapper))
        print(f"{tapper}: {len(feat)} starts processed")

    combined = pd.concat(all_feat, ignore_index=True)
    combined_pk = pd.concat(all_feat_pk, ignore_index=True)

    combined.to_csv(os.path.join(OUT_DIR, "boxer_can_features_enriched.csv"), index=False)
    combined_pk.to_csv(os.path.join(OUT_DIR, "boxer_can_features_packs.csv"), index=False)
    example_trace.to_csv(os.path.join(OUT_DIR, "example_start_trace.csv"), index=False)
    example_events.to_csv(os.path.join(OUT_DIR, "example_start_events.csv"), index=False)

    print(f"\nWrote {len(combined)} starts across {combined['can_tapper_id'].nunique()} tappers to {OUT_DIR}")


if __name__ == "__main__":
    main()
