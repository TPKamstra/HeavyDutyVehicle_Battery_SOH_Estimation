#!/usr/bin/env python3
"""
export_ul18_12_unit2_summary.py

Static summary export of battery_feature_dashboard_unit2.py's already-computed
results (ul18_12_unit2 — fresh battery, 20-block test completed 2026-08-02)
into a small, dependency-light bundle. No raw dataset, no interactive HTML.

IMPORTANT — machine-specific data path: ul18_12_unit2/ (~1.17 GB, 1500+
files) is gitignored and does not exist in this repo. This script imports
battery_feature_dashboard_unit2.py from the sibling BatPi_Download working
copy (DATA_REPO_PATH below), where the real data lives. It only *writes*
into this repo's analytics/exports/ — nothing is read from or copied out of
the raw dataset itself. Adjust DATA_REPO_PATH if run on a different machine.

Chart images are NOT exported here — same kaleido/Chrome launch failure as
the boxer export (see exports/boxer_summary_export/NOTES.md).

Run with:
    python export_ul18_12_unit2_summary.py
"""
import json
import os
import sys

import pandas as pd

DATA_REPO_PATH = r"C:\Users\TPKam\Documents\BatPi_Download"
sys.path.insert(0, DATA_REPO_PATH)

import battery_feature_dashboard_unit2 as d  # noqa: E402  (path must be set first)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", "ul18_12_unit2_summary_export")
os.makedirs(OUT_DIR, exist_ok=True)

_EXCLUDE = {
    "testday": {"battery_id", "block", "kind", "timestamp", "filename"},
    "degr": {"block", "cycle", "discharge_complete", "discharge_coarse_sampling",
             "charge_complete", "charge_coarse_sampling", "status"},
    "soh": {"battery_id", "block", "reached_cutoff"},
}


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


def main():
    written = []
    d._rescan()

    # 1 — flat summary stats
    rows = []
    rows += numeric_summary_rows(d._DATASET, "testday_features (SoC-sweep runs)", _EXCLUDE["testday"])
    rows += numeric_summary_rows(d._DEGR_DF, "degradation_cycles", _EXCLUDE["degr"])
    rows += numeric_summary_rows(d._SOH_DF, "soh_history (per block, C/5)", _EXCLUDE["soh"])

    csv_path = os.path.join(OUT_DIR, "summary_stats.csv")
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    written.append(csv_path)

    json_path = os.path.join(OUT_DIR, "summary_stats.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, default=str, ensure_ascii=False)
    written.append(json_path)

    # 2 — underlying result tables
    table_map = {
        "testday_features.csv": d._DATASET,
        "degradation_cycles.csv": d._DEGR_DF,
        "soh_history.csv": d._SOH_DF,
    }
    for fname, df in table_map.items():
        path = os.path.join(OUT_DIR, fname)
        df.to_csv(path, index=False, encoding="utf-8")
        written.append(path)

    # 3 — Feature Correlations tab's own result tables (already flat)
    predictor_df = d.build_block_predictor_table(d._DATASET, d._SOH_DF)
    predictor_path = os.path.join(OUT_DIR, "soh_predictors_this_next_block.csv")
    predictor_df.to_csv(predictor_path, index=False, encoding="utf-8")
    written.append(predictor_path)

    soc_robust_df = d.build_soc_robust_indicator_table(d._DATASET, d._SOH_DF)
    soc_robust_path = os.path.join(OUT_DIR, "soc_robust_soh_indicators.csv")
    soc_robust_df.to_csv(soc_robust_path, index=False, encoding="utf-8")
    written.append(soc_robust_path)

    # 4 — full pairwise Spearman correlation matrix, clean runs only (mirrors
    # the heatmap's default view — see build_run_corr_heatmap/_corr_columns)
    clean = d._DATASET[~d._DATASET["cutoff_hit"]]
    cols = d._corr_columns(clean)
    corr = clean[cols].corr(method="spearman").round(3)
    corr_path = os.path.join(OUT_DIR, "correlation_matrix_clean_runs.csv")
    corr.to_csv(corr_path, encoding="utf-8")
    written.append(corr_path)

    # 5 — a small set of representative chart images (PNG via kaleido)
    import plotly.io as pio
    figs = {
        "soh_history": d.build_soh_fig(d._SOH_DF),
        "degradation_cycles": d.build_degr_fig(d._DEGR_DF),
        "correlation_heatmap_clean_runs": d.build_run_corr_heatmap(clean, cols),
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
