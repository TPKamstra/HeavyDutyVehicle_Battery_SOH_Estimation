#!/usr/bin/env python3
"""
export_boxer_summary.py

One-off export of the analysis already performed by boxer_battery_dashboard.py
into a small, static, dependency-light bundle — no raw dataset, no interactive
HTML. Read-only with respect to boxer_battery_dashboard.py: imports it to reuse
its already-loaded feature tables (FEAT, FEAT_PK) and figure-builder functions
rather than recomputing anything independently, so the export always matches
what the live dashboard actually shows.

Writes into exports/boxer_summary_export/:
    summary_stats.csv / .json      - flat, one row per metric (min/mean/max/n)
    features_enriched.csv          - FEAT (features.csv + derived R_int_mohm)
    features_packs_enriched.csv    - FEAT_PK (features_packs.csv + derived cols)
    01_start_profile.png
    02_feature_trends.png
    03_group_comparison.png
    04_soc_temp_effects.png

Run with:
    python export_boxer_summary.py
"""
import json
import os

import pandas as pd
import plotly.io as pio

import boxer_battery_dashboard as bd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports", "boxer_summary_export")
os.makedirs(OUT_DIR, exist_ok=True)


def build_summary_rows() -> list:
    """Flat (metric, category, unit, min, mean, max, n) rows — one per metric,
    reproducing exactly what _build_summary()'s markdown table shows, including
    its existing quirk of pulling V_pre_V/V_min_V from the per-group table
    (FEAT_PK, 164 rows = 41 starts x 4 groups) rather than the pack-level one
    (FEAT, 41 rows) that the other five pack-level metrics use — preserved
    faithfully rather than "fixed," since this export mirrors the dashboard's
    existing analysis rather than re-deriving it.
    """
    f, fp = bd.FEAT, bd.FEAT_PK
    rows = []

    pack_metrics = [
        ("V_drop_total_pct", "V_drop_total", "%", f),
        ("R_int_mohm", "R_int", "mOhm", f),
        ("soc_pct_est_pack", "SoC_at_start", "%", f),
        ("temp_avg_start_C", "Temp_at_start", "degC", f),
        ("t_recovery_s_max", "Recovery_time", "s", f),
        ("V_pre_V", "V_pre_start", "V", fp),
        ("V_min_V", "V_min_start", "V", fp),
    ]
    for col, metric, unit, src in pack_metrics:
        s = src[col]
        rows.append({
            "metric": metric, "category": "pack", "unit": unit,
            "min": round(float(s.min()), 3), "mean": round(float(s.mean()), 3),
            "max": round(float(s.max()), 3), "n": int(s.notna().sum()),
        })

    for g in ["PG1_est", "PG2", "PG3_est", "PG4"]:
        s = fp.loc[fp["group"] == g, "R_int_mohm"]
        rows.append({
            "metric": "R_int", "category": g, "unit": "mOhm",
            "min": round(float(s.min()), 3), "mean": round(float(s.mean()), 3),
            "max": round(float(s.max()), 3), "n": int(s.notna().sum()),
        })
    return rows


def main():
    written = []

    # 1 — summary statistics (CSV + JSON)
    rows = build_summary_rows()
    summary_df = pd.DataFrame(rows)
    csv_path = os.path.join(OUT_DIR, "summary_stats.csv")
    summary_df.to_csv(csv_path, index=False)
    written.append(csv_path)

    json_path = os.path.join(OUT_DIR, "summary_stats.json")
    with open(json_path, "w") as fh:
        json.dump(rows, fh, indent=2, default=str)
    written.append(json_path)

    # 2 — enriched per-event feature tables (adds the derived columns the
    # dashboard computes at load time — not identical to the raw checked-in CSVs)
    feat_path = os.path.join(OUT_DIR, "features_enriched.csv")
    bd.FEAT.to_csv(feat_path, index=False)
    written.append(feat_path)

    feat_pk_path = os.path.join(OUT_DIR, "features_packs_enriched.csv")
    bd.FEAT_PK.to_csv(feat_pk_path, index=False)
    written.append(feat_pk_path)

    # 3 — static PNG exports of the dashboard's actual figure objects
    figs = {
        "01_start_profile": bd._build_start_fig(),
        "02_feature_trends": bd._build_trends_fig(),
        "03_group_comparison": bd._build_group_fig(),
        "04_soc_temp_effects": bd._build_soc_temp_fig(),
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
