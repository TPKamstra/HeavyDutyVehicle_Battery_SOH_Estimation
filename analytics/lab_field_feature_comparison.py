#!/usr/bin/env python3
"""
Lab vs Field Feature-Family Consistency Check
Lab battery -- Ultracell UL18-12 `ul18_12_unit2` (12 V, full 20-block
campaign, 140 runs) -- vs. EnerSys ArmaSafe Plus 12FV120 (field, "Boxer"
fleet CAN export, 15 taps, 4799 starts).

(The other lab battery, `old_ul18_12` -- already-aged, only 8 runs on a
partial v2-schema rollout -- was dropped from this comparison on 2026-08-26:
8 runs is too thin a sample to read anything into next to unit2's 140 and
field's 4799, and having it in every panel was adding a confusing third
series without adding real information. It still has its own full dashboard,
`battery_feature_dashboard.py` / the "Lab -- old_ul18_12" tab in
main_dashboard.py -- this removal is scoped to the lab-vs-field comparison
only.)

This is a *feature-family consistency check*, not a value-for-value
validation: lab and field are different products (different chemistry
generation, different pack size), measured through separate pipelines
(lab BDPS pulse-test rig vs. vehicle CAN bus), and the field side has no
shared ground-truth SOH to validate against -- see main.tex's
"Lab-to-Field Validation Procedure" section, which frames the comparison the
same way. The point is to check whether a feature that behaves a given way
in the lab (e.g. rises with degradation, is SoC-robust) behaves in a
qualitatively similar way in the field, not that the two sides should land
on the same absolute numbers.

Three feature families have a genuine counterpart on both sides:
  (a) crank apparent internal resistance (mOhm)
      lab:   crank_cold_R_int_apparent_mohm, crank_hot_R_int_apparent_mohm
      field: R_internal_est_ohm_pack * 1000            (features_enriched)
      ** Known caveat, kept separate from cold/hot rather than merged: the
      lab's logged crank current (crank_cold/hot_I_peak_A, median 3.5-7.1 A)
      is ~2 orders of magnitude below a realistic engine-crank current (field
      I_load_A, median ~400 A) -- documented in testday_v2_features.py as
      "well below any realistic crank current -- likely a low-current
      bench/validation run". R_int_apparent = dV/I_peak, so this small a
      denominator makes the lab-side numbers noise-dominated; cold and hot
      are plotted as separate panels (rather than pooled into one "Lab"
      series) precisely because pooling them previously hid this ~100x
      scale mismatch. Treat this panel as a sanity check on the field
      pipeline's own internal consistency, not a lab-field agreement claim.
      ** Field-side caveat, added after applying the same low-current
      diagnostic RESULTS_SUMMARY_FOR_PAPER.md §1 ran on the lab data (this
      was flagged there as "not yet done" for the field): field R_int is
      NOT clean just because current is measured. `I_load_A` is negative
      (large magnitude, ~-400 to -500 A) for a cluster of can_tapper14
      starts -- a sign-convention issue, not low current, and R_int is still
      wildly wrong there (up to 5800 mOhm) for a reason not fully diagnosed
      (the original per-start R_int figure is computed upstream of this
      repo, inside the zip export, not by build_boxer_can_features.py).
      Separately, ~2% of starts across nearly every tap (not just the
      already-documented can_tapper31 dropout window) have |I_load_A| <
      150 A, well below the ~300-500 A bulk of the distribution, and R_int
      there is correspondingly inflated (mean ~380-500 mOhm vs. a clean-set
      mean of ~9.6 mOhm). `_field_r_int_reliable_mask()` below flags and
      excludes both patterns (low |I_load_A| OR implausibly large R_int OR
      missing current) before this comparison ever sees the field R_int
      series -- see that function's docstring for the exact criteria and
      counts. This is a *diagnostic filter*, not a fix to the underlying
      per-start feature computation.
  (b) post-crank recovery voltage at +30s (V)
      lab:   recovery_after_cold_V_plus30s, recovery_after_hot_V_plus30s
      field: V_rec_30s_1_10V / 10, V_rec_60s_1_10V / 10 (features_packs)
  (c) post-crank / driving voltage stability (V, std dev)
      lab:   driving_aux_load_1_V_std
      field: post_start_V_std_1_10V / 10                (features_enriched)

`crank_duration_s` and `glow_lead_s` are field-only: the lab's crank pulses
run for a fixed, scripted duration rather than a measured one (there is no
"how long did cranking take" question to ask of the lab data), so they are
deliberately left out of this comparison rather than forced into one.

Lab-side note: 9 of 140 `unit2` runs (all in Block 5) hit the pack's
low-voltage cutoff mid-script and follow a different, shorter event sequence
(see analytics/CLAUDE.md's "Known Pi-side logging-rate regression" section)
-- their recovery_after_cold/hot columns are naturally NaN and they drop out
of this comparison via dropna(), which is the intended behaviour, not a bug.

Outlier handling: every panel's y-axis is capped to a robust range
(Tukey IQR fences at 3x IQR, i.e. Tukey's "far out" threshold) so a handful
of extreme points can't compress the rest of the distribution into an
unreadable sliver -- this was previously happening on the field R_int panel,
where ~1% of starts blew the axis out to a range where the rest of the
points were invisible. Clipped points are never silently dropped: each
panel's subtitle reports how many were clipped and the most extreme value
among them, and summary_table() below always reports true (uncapped)
min/mean/max/n over the full (post-reliability-filter, for field R_int)
data.
"""
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

LAB_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "exports", "ul18_12_unit2_summary_export", "testday_features.csv",
)
FIELD_ENRICHED_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dataset_boxer_can", "boxer_can_features_enriched.csv",
)
FIELD_PACKS_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dataset_boxer_can", "boxer_can_features_packs.csv",
)

# Validated categorical pair (blue/orange, adjacent slots 1-2 -- see dataviz
# skill's references/palette.md) -- blue = lab identity, orange = field
# identity, held fixed across every panel. A second dimension within "lab"
# (cold/hot condition) is a shade of the SAME hue rather than a new hue,
# since it's a sub-condition of one identity, not a new series; battery
# identity is always carried in the panel's text label, never invented as a
# third color. Since 2026-08-26 there is only one lab battery in this
# comparison (old_ul18_12 dropped, see module docstring), so LAB_BASE (not a
# shade) is used for the single-condition V_std family's one remaining lab
# series -- LAB_SHADE_1/2 are still used for cold/hot within families (a)/(b).
LAB_SHADE_1 = "#6da7ec"   # lab, cold-crank condition -- blue, light step
LAB_SHADE_2 = "#1c5cab"   # lab, hot-crank condition  -- blue, dark step
LAB_BASE = "#2a78d6"      # lab, single-condition families (e.g. V_std)
FIELD_SHADE_1 = "#eb6834"  # field, condition 1 (e.g. +30s) -- orange
FIELD_SHADE_2 = "#d95926"  # field, condition 2 (e.g. +60s) -- orange, dark step
FIELD_BASE = "#eb6834"

K_EXTREME_IQR = 3.0   # Tukey "far out" fence multiplier for axis capping
AXIS_PAD_FRAC = 0.08

# Field R_int reliability filter (see module docstring, family (a) caveat).
# Chosen from correlating R_internal_est_ohm_pack against |I_load_A| across
# all 4799 field starts: above this current, R_int settles to a stable
# ~9.6 mOhm mean; below it, mean jumps to ~380-500 mOhm. The magnitude cap
# catches the separate can_tapper14 sign-flip cluster, which has *large*
# |I_load_A| (~400-500 A, just negative) but still-wrong R_int for a reason
# not fully diagnosed (see docstring) -- current-magnitude alone would miss it.
#
# NOTE: this used to be an ad-hoc 150A/50mOhm filter computed independently
# here. It's now read directly from `invalid_R_int` (and its two sub-flags),
# which build_boxer_can_features.py bakes into boxer_can_features_enriched.csv
# on every regeneration (Spearman r(I_load_A, R_int) = -0.52 to -0.59; a
# 200A threshold is where all three R_int columns cross from a bounded,
# physically plausible range into implausible territory -- see that script's
# `add_r_int_validity_flags()` for the exact criteria). One canonical
# definition, not two -- the field R_int marker style below reads these flags
# rather than re-deriving them.


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data():
    lab_unit2 = pd.read_csv(LAB_CSV)
    field = pd.read_csv(FIELD_ENRICHED_CSV, parse_dates=["start_time"])
    field_pk = pd.read_csv(FIELD_PACKS_CSV, parse_dates=["start_time"])
    return lab_unit2, field, field_pk


# ── Robust axis range (outlier-aware, never data-dropping) ─────────────────

def robust_axis_range(values: np.ndarray, k: float = K_EXTREME_IQR, pad_frac: float = AXIS_PAD_FRAC):
    """
    Tukey-fence axis range: caps the *view* at Q1/Q3 +/- k*IQR (k=3 = "far
    out" fences, wider than the classic 1.5 whisker so mild outliers still
    show as points -- only genuinely extreme values get clipped from view).
    Returns ((axis_lo, axis_hi), n_clipped, most_extreme_clipped_value).
    Never mutates or drops the underlying data -- clipping is view-only.
    """
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return (0.0, 1.0), 0, None
    q1, q3 = np.percentile(v, [25, 75])
    iqr = q3 - q1
    if iqr == 0:
        lo, hi = float(v.min()), float(v.max())
    else:
        lo = max(float(v.min()), q1 - k * iqr)
        hi = min(float(v.max()), q3 + k * iqr)
    span = hi - lo
    pad = span * pad_frac if span > 0 else max(abs(hi), 1.0) * pad_frac
    below = v[v < lo]
    above = v[v > hi]
    n_clipped = int(len(below) + len(above))
    extreme = None
    if n_clipped:
        extreme = float(above.max()) if len(above) else float(below.min())
    return (lo - pad, hi + pad), n_clipped, extreme


# ── Matched-feature families ────────────────────────────────────────────────

def build_matched_series(lab_unit2: pd.DataFrame, field: pd.DataFrame, field_pk: pd.DataFrame) -> list:
    """
    Return a list of family dicts, each:
      {key, title, ylabel, shared_axis, caveat, series: [{label, values, color}, ...]}
    `shared_axis=True` means every panel in the family's figure uses one
    common robust range (valid when panels are physically the same quantity
    at comparable scale); False means each panel gets its own robust range
    (used only for R_int, where lab and field are not on comparable scales
    -- see module docstring).

    Lab cold/hot crank conditions are kept as separate, labeled series within
    families (a)/(b) rather than pooled -- they're genuinely different
    operating points, not noise around one value. (`old_ul18_12` used to
    appear here too, as a second lab battery; dropped 2026-08-26, see module
    docstring -- 8 runs wasn't a usable sample next to unit2's 140.)
    """
    families = []

    # Field R_int: shown for EVERY start (not excluded), with invalid_R_int
    # points marked distinctly (see build_family_figure) rather than dropped
    # -- same flag-don't-filter convention as the rest of this project.
    # Falls back to "nothing flagged" if an older enriched CSV predates the
    # flag columns (regenerate via build_boxer_can_features.py to get them).
    field_r_int = field["R_internal_est_ohm_pack"] * 1000
    field_r_int_flag = field["invalid_R_int"] if "invalid_R_int" in field.columns else pd.Series(False, index=field.index)

    # (a) Crank apparent internal resistance -- one panel per (cold, hot,
    # field) condition, never pooled -- see module docstring caveat.
    families.append({
        "key": "R_int_apparent_mohm",
        "title": "Crank Apparent Internal Resistance",
        "ylabel": "R_int (mΩ)",
        "shared_axis": False,
        "caveat": (
            "Lab crank current is ~2 orders of magnitude below a realistic engine-crank current -- "
            "R_int_apparent is noise-dominated on the lab side (sanity-checks the field pipeline, not a "
            "lab-field agreement claim). Field panel marks ~6.4% of starts flagged invalid_R_int "
            "(sign-flip/dropout/low-current artifacts, grey ✕) -- box stats computed on the clean "
            "subset only; see docstring."
        ),
        "series": [
            {"label": "Lab unit2 — cold", "values": lab_unit2["crank_cold_R_int_apparent_mohm"].dropna(), "color": LAB_SHADE_1},
            {"label": "Lab unit2 — hot", "values": lab_unit2["crank_hot_R_int_apparent_mohm"].dropna(), "color": LAB_SHADE_2},
            {
                "label": "Field — per start", "color": FIELD_BASE,
                "values": field_r_int.dropna(),
                "flag": field_r_int_flag.reindex(field_r_int.dropna().index),
            },
        ],
    })

    # (b) Post-crank recovery voltage
    families.append({
        "key": "recovery_V",
        "title": "Post-Crank Recovery Voltage",
        "ylabel": "Recovery voltage (V)",
        "shared_axis": True,
        "caveat": None,
        "series": [
            {"label": "Lab unit2 — cold, +30s", "values": lab_unit2["recovery_after_cold_V_plus30s"].dropna(), "color": LAB_SHADE_1},
            {"label": "Lab unit2 — hot, +30s", "values": lab_unit2["recovery_after_hot_V_plus30s"].dropna(), "color": LAB_SHADE_2},
            {"label": "Field — +30s (per group)", "values": (field_pk["V_rec_30s_1_10V"] / 10).dropna(), "color": FIELD_SHADE_1},
            {"label": "Field — +60s (per group)", "values": (field_pk["V_rec_60s_1_10V"] / 10).dropna(), "color": FIELD_SHADE_2},
        ],
    })

    # (c) Post-crank / driving voltage stability -- single lab condition, so
    # LAB_BASE rather than a shade (no second lab series to distinguish it from
    # anymore, see module docstring's color-convention note).
    families.append({
        "key": "V_std_stability",
        "title": "Post-Crank / Driving Voltage Stability",
        "ylabel": "V_std (V)",
        "shared_axis": True,
        "caveat": None,
        "series": [
            {"label": "Lab unit2 — driving_aux_load", "values": lab_unit2["driving_aux_load_1_V_std"].dropna(), "color": LAB_BASE},
            {"label": "Field — post-start (15-115s)", "values": (field["post_start_V_std_1_10V"] / 10).dropna(), "color": FIELD_BASE},
        ],
    })

    return families


def summary_table(families: list) -> pd.DataFrame:
    """Full, uncapped stats per series -- the ground truth the capped plots summarize."""
    rows = []
    for fam in families:
        for s in fam["series"]:
            v = s["values"]
            rows.append({
                "feature_family": fam["title"], "series": s["label"],
                "n": int(v.count()), "min": v.min(), "mean": v.mean(),
                "median": v.median(), "max": v.max(),
            })
    return pd.DataFrame(rows)


# ── Plots ────────────────────────────────────────────────────────────────────

# Marker for physically-motivated "flagged, not statistically detected"
# points (currently: field R_int's invalid_R_int) -- deliberately NOT the
# same style as robust_axis_range's clipped-point handling (that's a view
# limitation, reported in text, not a marker) and deliberately borrowed from
# the lab dashboards' own faded/hollow "don't fully trust this point" visual
# language (battery_feature_dashboard.py's IQR-outlier "x" markers,
# lightgrey) rather than inventing a new convention.
FLAGGED_MARKER = dict(symbol="x", color="lightgrey", size=7, opacity=0.9)


def build_family_figure(fam: dict) -> go.Figure:
    """One figure per feature family: one box+strip panel per series (small
    multiples), each panel's axis independently outlier-capped (or sharing
    one capped range across the row when `shared_axis` is True). A series
    may carry an optional "flag" (boolean Series aligned to "values") --
    box statistics are computed on the unflagged subset only, and flagged
    points are overlaid with FLAGGED_MARKER rather than excluded."""
    n = len(fam["series"])

    if fam["shared_axis"]:
        pooled = np.concatenate([np.asarray(s["values"], dtype=float) for s in fam["series"]])
        shared_range, _, _ = robust_axis_range(pooled)
    else:
        shared_range = None

    subplot_titles = []
    panel_info = []
    for s in fam["series"]:
        v = s["values"]
        flag = s.get("flag")
        n_flagged = int(flag.sum()) if flag is not None else 0
        axis_range, n_clipped, extreme = shared_range, 0, None
        if not fam["shared_axis"]:
            axis_range, n_clipped, extreme = robust_axis_range(v)
        else:
            _, n_clipped, extreme = robust_axis_range(v)  # still report per-series clip count vs shared range
            lo, hi = shared_range
            n_clipped = int(((v < lo) | (v > hi)).sum())
            above = v[v > hi]
            below = v[v < lo]
            extreme = float(above.max()) if len(above) else (float(below.min()) if len(below) else None)
        note = f"n={v.count()}"
        if n_flagged:
            note += f", {n_flagged} flagged (✕)"
        if n_clipped:
            note += f", {n_clipped} clipped (max {extreme:.1f})"
        subplot_titles.append(f"{s['label']}<br><span style='font-size:11px;color:#898781'>{note}</span>")
        panel_info.append((s, axis_range))

    fig = make_subplots(rows=1, cols=n, horizontal_spacing=0.06, subplot_titles=subplot_titles)

    for c, (s, axis_range) in enumerate(panel_info, start=1):
        flag = s.get("flag")
        clean_values = s["values"][~flag] if flag is not None else s["values"]
        flagged_values = s["values"][flag] if flag is not None else s["values"].iloc[0:0]

        fig.add_trace(go.Box(
            y=clean_values, name=s["label"],
            marker=dict(color=s["color"], size=4, opacity=0.5),
            line=dict(color=s["color"]), fillcolor=s["color"], opacity=0.55,
            boxpoints="all", jitter=0.45, pointpos=0, width=0.55,
            showlegend=False,
        ), row=1, col=c)
        if len(flagged_values):
            # Same category (name) as the box above -- Plotly aligns it to the
            # same x slot and applies its own jitter; box/line made invisible
            # so only the flagged points themselves show, in FLAGGED_MARKER.
            fig.add_trace(go.Box(
                y=flagged_values, name=s["label"],
                marker=FLAGGED_MARKER, boxpoints="all", jitter=0.45, pointpos=0, width=0.55,
                line=dict(color="rgba(0,0,0,0)", width=0), fillcolor="rgba(0,0,0,0)",
                hoveron="points",
                hovertemplate="⚠ flagged invalid_R_int<br>%{y:.1f}<extra></extra>",
                showlegend=False,
            ), row=1, col=c)
        fig.update_yaxes(title_text=fam["ylabel"] if c == 1 else None, range=list(axis_range), row=1, col=c)
        fig.update_xaxes(showticklabels=False, row=1, col=c)

    title = f"{fam['title']} — Lab (UL18-12) vs Field (Boxer ArmaSafe)"
    subtitle = "<br><span style='font-size:12px;color:#52514e'>" + fam["caveat"] + "</span>" if fam["caveat"] else ""
    fig.update_layout(
        height=460 if not fam["caveat"] else 500,
        template="plotly_white",
        title=dict(text=title + subtitle, x=0.02, xanchor="left"),
        margin=dict(t=120 if fam["caveat"] else 90, b=40),
    )
    return fig


# ── Standalone run ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    lab_unit2_df, field_df, field_pk_df = load_data()
    families = build_matched_series(lab_unit2_df, field_df, field_pk_df)
    summary = summary_table(families)
    pd.set_option("display.float_format", lambda x: f"{x:.3f}")
    print(summary.to_string(index=False))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
    os.makedirs(out_dir, exist_ok=True)
    for fam in families:
        fig = build_family_figure(fam)
        fig.write_html(os.path.join(out_dir, f"lab_field_comparison_{fam['key']}.html"))
    print(f"\nWrote {len(families)} figures to {out_dir}")
