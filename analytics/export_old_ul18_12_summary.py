#!/usr/bin/env python3
"""
export_old_ul18_12_summary.py

Static summary export of battery_feature_dashboard.py's already-computed
results (old_ul18_12 — heavily degraded, retired UL18-12) into a small,
dependency-light bundle. No raw dataset, no interactive HTML.

IMPORTANT — machine-specific data path: LOGBATTEST_Complete/ (~2.16 GB,
1230 files) is gitignored and does not exist in this repo. This script
imports battery_feature_dashboard.py from the sibling BatPi_Download working
copy (DATA_REPO_PATH below), where the real data lives, so LOG_DIR resolves
correctly. It only *writes* into this repo's analytics/exports/ — nothing is
read from or copied out of the raw dataset itself. Adjust DATA_REPO_PATH if
run on a different machine.

Chart images are NOT exported here — plotly's write_image() (via kaleido)
hangs at Chrome launch in this environment (missing mf.dll / Windows Media
Foundation; see exports/boxer_summary_export/NOTES.md for the full
diagnosis, which applies identically here since it's the same Python env).

Run with:
    python export_old_ul18_12_summary.py
"""
import json
import os
import sys

import pandas as pd

DATA_REPO_PATH = r"C:\Users\TPKam\Documents\BatPi_Download"
sys.path.insert(0, DATA_REPO_PATH)

import battery_feature_dashboard as bfd  # noqa: E402  (path must be set first)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", "old_ul18_12_summary_export")
os.makedirs(OUT_DIR, exist_ok=True)

# Non-metric / identifier columns to leave out of the numeric summary per table.
_EXCLUDE = {
    "trend": {"session", "date"},
    "soh": {"datetime", "date", "file", "complete", "run_1", "run_1_dt",
            "run_2", "run_2_dt", "soh_cycle", "sweep_session", "sweep_session_dt"},
    "sweep": {"file", "dt", "date", "session"},
    "v2": {"battery_id", "block", "kind", "timestamp", "filename"},
}

# DCIR = |ΔV| / |I|, so a pulse with small peak current turns ordinary voltage
# measurement noise into a wildly inflated "resistance." In this dataset DCIR_dis
# correlates -0.85 with |I_peak| (soc_sweep.csv): pulses below this floor average
# ~1208 mΩ, pulses at/above it average ~166 mΩ — a real bimodal split, not a
# continuous distribution with a long tail. Flagged, not dropped: the per-row
# CSVs keep a `_low_current_flag` column, and summary_stats.csv reports both the
# unfiltered and the filtered (>= threshold) view so nothing is silently lost.
DCIR_CURRENT_THRESHOLD_A = 15.0


def numeric_summary_rows(df: pd.DataFrame, source: str, exclude: set) -> list:
    rows = []
    for col in df.columns:
        if col in exclude or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        rows.append({
            "source": source, "metric": col,
            "min": round(float(s.min()), 4), "mean": round(float(s.mean()), 4),
            "max": round(float(s.max()), 4), "n": int(len(s)),
        })
    return rows


def add_low_current_flag(df: pd.DataFrame, dcir_col: str, current_col: str,
                          threshold: float = DCIR_CURRENT_THRESHOLD_A) -> pd.DataFrame:
    if dcir_col in df.columns and current_col in df.columns:
        df[f"{dcir_col}_low_current_flag"] = df[current_col].abs() < threshold
    return df


def filtered_summary_row(df: pd.DataFrame, dcir_col: str, flag_col: str, source: str) -> dict | None:
    if dcir_col not in df.columns or flag_col not in df.columns:
        return None
    clean = df.loc[~df[flag_col], dcir_col].dropna()
    if clean.empty:
        return None
    return {
        "source": f"{source} [filtered: |I_peak| >= {DCIR_CURRENT_THRESHOLD_A:.0f}A]",
        "metric": dcir_col,
        "min": round(float(clean.min()), 4), "mean": round(float(clean.mean()), 4),
        "max": round(float(clean.max()), 4), "n": int(len(clean)),
    }


def main():
    written = []

    bfd._compute_trends()
    bfd._compute_sweep()
    bfd._compute_soh()   # after sweep, so SOH<->sweep cross-linking is complete
    bfd._scan_v2()

    # Low-current DCIR flag (see DCIR_CURRENT_THRESHOLD_A above) — added to the
    # per-row tables before they're written out, so the flag ships with the data.
    bfd._TREND_DF = add_low_current_flag(bfd._TREND_DF, "DCIR_dis [mΩ]", "I_dis_peak [A]")
    bfd._SWEEP_DF = add_low_current_flag(bfd._SWEEP_DF, "DCIR_dis [mΩ]", "I_peak_dis [A]")

    # 1 — flat summary stats across all four computed result tables
    rows = []
    rows += numeric_summary_rows(bfd._TREND_DF, "degradation_trends (testday_run sessions)", _EXCLUDE["trend"])
    rows += numeric_summary_rows(bfd._SOH_DF, "soh_history (discharge_c5 files)", _EXCLUDE["soh"])
    rows += numeric_summary_rows(bfd._SWEEP_DF, "soc_sweep (SoCsweep/Block testday runs)", _EXCLUDE["sweep"])
    rows += numeric_summary_rows(bfd._V2_DF, "testday_v2_beta (partial rollout, 2026-07-02/03 only)", _EXCLUDE["v2"])

    for df, source in [(bfd._TREND_DF, "degradation_trends (testday_run sessions)"),
                        (bfd._SWEEP_DF, "soc_sweep (SoCsweep/Block testday runs)")]:
        r = filtered_summary_row(df, "DCIR_dis [mΩ]", "DCIR_dis [mΩ]_low_current_flag", source)
        if r is not None:
            rows.append(r)

    csv_path = os.path.join(OUT_DIR, "summary_stats.csv")
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    written.append(csv_path)

    json_path = os.path.join(OUT_DIR, "summary_stats.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, default=str, ensure_ascii=False)
    written.append(json_path)

    # 2 — the underlying result tables themselves
    table_map = {
        "degradation_trends.csv": bfd._TREND_DF,
        "soh_history.csv": bfd._SOH_DF,
        "soc_sweep.csv": bfd._SWEEP_DF,
        "testday_v2_beta_partial.csv": bfd._V2_DF,
    }
    for fname, df in table_map.items():
        path = os.path.join(OUT_DIR, fname)
        df.to_csv(path, index=False, encoding="utf-8")
        written.append(path)

    # 3 — a small set of representative chart images (PNG via kaleido). Unlike
    # boxer's fixed 4 tabs, most of this dashboard's tabs are parameterized by
    # a feature/x-axis selector rather than one fixed figure, so this picks
    # one headline chart per tab rather than every feature x every tab.
    import plotly.io as pio
    # x_mode="Cycle count" rather than "Date" for the first two: kaleido's
    # static-image pipeline can't JSON-serialize raw pandas Timestamp x-axis
    # values (unlike interactive Plotly rendering, which handles them fine).
    figs = {
        "soh_history": bfd.build_soh_fig(bfd._SOH_DF, "Cycle count"),
        "degradation_trend_DCIR_dis": bfd.build_trend_fig("DCIR_dis [mΩ]", "Cycle count"),
        "soc_sweep_DCIR_dis": bfd.build_sweep_fig("DCIR_dis [mΩ]", "OCV [V]"),
    }
    png_failures = []
    for stem, fig in figs.items():
        png_path = os.path.join(OUT_DIR, f"{stem}.png")
        try:
            pio.write_image(fig, png_path, width=1800, height=1100, scale=2)
            written.append(png_path)
        except Exception as e:
            png_failures.append((stem, str(e)))

    print(f"Wrote {len(written)} file(s) to {OUT_DIR}:")
    for p in written:
        print(" ", os.path.relpath(p, OUT_DIR))
    if png_failures:
        print("\nPNG export FAILED for:")
        for stem, err in png_failures:
            print(f"  {stem}: {err}")


if __name__ == "__main__":
    main()
