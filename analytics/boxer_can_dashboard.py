#!/usr/bin/env python3
"""
Boxer Battery — Motor-Start CAN Export Dashboard
15 CAN taps, ~4800 engine starts total (tapper count grows as more taps are
uploaded -- tapper colours are assigned dynamically below, not hardcoded).

Reads the small, committed tables in dataset_boxer_can/ (produced by
build_boxer_can_features.py from the original motor_starts_can_tapper*.zip
export). No raw per-start grid/series data is loaded here -- that stays in
the original export; this dashboard only touches the already-derived,
per-start feature tables plus one saved example trace for the profile tab.
"""
import os

import numpy as np
import pandas as pd
import panel as pn
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import can_stats
from lab_field_feature_comparison import (
    FLAGGED_MARKER, LAB_CSV, build_family_figure, build_matched_series, robust_axis_range, summary_table,
)

pn.extension("plotly", "tabulator")

DATASHEET_IR_MOHM = 1.6  # mΩ AC internal resistance per battery (new, datasheet) -- same pack as dataset_boxer

# TAPPER_COLOURS is assigned dynamically below, once the actual set of
# tappers present in the data is known (see _load() / TAPPERS).

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_boxer_can")
NOTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_notes")


def _read_note(name: str) -> str:
    with open(os.path.join(NOTES_DIR, name), encoding="utf-8") as f:
        return f.read()


# `invalid_R_int` (+ R_int_current_sensor_dropout / R_int_negative_current
# sub-flags) come from build_boxer_can_features.py's add_r_int_validity_flags()
# -- a physically-motivated flag (I_load_A too low/missing/negative), never
# conflated with this dashboard's generic Tukey-fence outlier detection
# (Outlier Inspector tab). Reuses FLAGGED_MARKER from lab_field_feature_comparison.py
# so the same grey-✕ convention means the same thing across every tab/dashboard.
# The explanation itself lives in dashboard_notes/invalid_r_int_note.md (shown
# on the dashboard as a caption, same pattern as the other dashboard_notes/*.md
# files) rather than in a chart title or a "see CLAUDE.md" pointer -- CLAUDE.md
# is a repo file, not something visible from the running dashboard.
INVALID_R_INT_NOTE_MD = _read_note("invalid_r_int_note.md")

# Shared across every tab that plots R_int -- same checkbox object rendered
# in each tab, so toggling it in one place applies everywhere and stays in
# sync (a Panel widget can be placed in multiple locations in a layout).
HIDE_FLAGGED_TOGGLE = pn.widgets.Checkbox(name="Hide flagged (invalid_R_int) points", value=False)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load():
    f = pd.read_csv(os.path.join(BASE, "boxer_can_features_enriched.csv"), parse_dates=["start_time"])
    fp = pd.read_csv(os.path.join(BASE, "boxer_can_features_packs.csv"), parse_dates=["start_time"])
    ex = pd.read_csv(os.path.join(BASE, "example_start_trace.csv"))
    ex_ev = pd.read_csv(os.path.join(BASE, "example_start_events.csv"))

    f["R_int_mohm"] = f["R_internal_est_ohm_pack"] * 1000
    fp["R_int_mohm"] = fp["R_internal_est_ohm"] * 1000
    fp["V_pre_V"] = fp["V_pre_1_10V"] / 10
    fp["V_min_V"] = fp["V_min_1_10V"] / 10

    # Per-group-average scale (see build script docstring for the V_pre_total_1_10V
    # "sum of 4 groups" vs. real per-group voltage note) -- used for pre/post comparison.
    f["V_pre_group_avg_V"] = f["V_pre_total_1_10V"] / 40
    f["post_start_V_mean_V"] = f["post_start_V_mean_1_10V"] / 10
    f["post_start_V_std_V"] = f["post_start_V_std_1_10V"] / 10

    # Binned SoC / temperature factor columns for the ANOVA tab. Added here
    # (once, at load) rather than inside each figure builder so every tab sees
    # the same bin edges -- see can_stats.add_factor_columns for why the edges
    # are fixed rather than quantile-derived.
    f = can_stats.add_factor_columns(f)
    return f, fp, ex, ex_ev


FEAT, FEAT_PK, EX_TRACE, EX_EVENTS = _load()
TAPPERS = sorted(FEAT["can_tapper_id"].unique())

# Qualitative palette, cycled if there are ever more tappers than colours --
# avoids a hardcoded dict silently falling back to no colour for new taps.
_PALETTE = (
    "#1f77b4", "#EF553B", "#2ca02c", "#FF7F0E", "#AB63FA", "#00CC96", "#636EFA",
    "#FFA15A", "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#8C564B", "#17BECF",
    "#E377C2",
)
TAPPER_COLOURS = {tap: _PALETTE[i % len(_PALETTE)] for i, tap in enumerate(TAPPERS)}

# Raw per-group voltage traces for flagged-outlier starts only (see
# build_boxer_can_features.py's --extract-outlier-traces) -- None if that's
# never been run; the Outlier Inspector tab degrades gracefully in that case.
_OUTLIER_TRACES_PATH = os.path.join(BASE, "outlier_raw_traces.csv")
_OUTLIER_EVENTS_PATH = os.path.join(BASE, "outlier_raw_events.csv")
OUTLIER_RAW_TRACES = pd.read_csv(_OUTLIER_TRACES_PATH) if os.path.exists(_OUTLIER_TRACES_PATH) else None
OUTLIER_RAW_EVENTS = (
    pd.read_csv(_OUTLIER_EVENTS_PATH, parse_dates=["start_time", "event_time"])
    if os.path.exists(_OUTLIER_EVENTS_PATH) else None
)


# ── Tab 1: Representative start, with CAN "commando" event markers ──────────

def _build_start_fig():
    ex = EX_TRACE.sort_values("t_rel")
    fig = make_subplots(
        rows=1, cols=1,
        subplot_titles=["Example Engine Start — can_tapper11, start_index 0 (-60s .. +120s)"],
    )

    group_cols = {
        "PG1_est": "FM-Center Voltage PG1_est in 1/10V",
        "PG2": "FM-Center Voltage PG2 in 1/10V",
        "PG3_est": "FM-Center Voltage PG3_est in 1/10V",
        "PG4": "FM-Center Voltage PG4 in 1/10V",
    }
    colours = {"PG1_est": "#636EFA", "PG2": "#EF553B", "PG3_est": "#00CC96", "PG4": "#AB63FA"}
    for g, col in group_cols.items():
        if col in ex.columns:
            fig.add_trace(go.Scatter(
                x=ex["t_rel"], y=ex[col] / 10,
                mode="lines", name=g,
                line=dict(color=colours[g], width=1.5),
            ))

    # CAN commando event markers
    label_colours = {
        "Engine starting command": "#d62728",
        "Glow plugs preheating": "#ff7f0e",
        "Engine running command": "#2ca02c",
    }
    for _, row in EX_EVENTS.iterrows():
        fig.add_vline(
            x=row["t_rel"],
            line=dict(color=label_colours.get(row["event_label"], "grey"), dash="dash", width=1.5),
            annotation_text=row["event_label"],
            annotation_position="top",
        )

    fig.update_xaxes(title_text="Time relative to start command (s)")
    fig.update_yaxes(title_text="Per-group voltage (V)")
    fig.update_layout(
        height=520, template="plotly_white",
        legend=dict(orientation="h", y=-0.18, x=0),
        margin=dict(t=80, b=80),
    )
    return fig


# ── Tab 2: Feature trends across all starts, coloured by tapper ────────────

def _build_trends_fig(hide_flagged: bool = False):
    f = FEAT.sort_values("start_time")

    fig = make_subplots(
        rows=2, cols=2,
        vertical_spacing=0.16, horizontal_spacing=0.12,
        subplot_titles=[
            "Voltage Drop During Crank (%)",
            "Estimated Pack Internal Resistance (mΩ)",
            "Crank Duration (s) — commando starting→running",
            "Post-Start Voltage Std. Dev. (V) — new field feature",
        ],
    )
    specs = [
        (1, 1, "V_drop_total_pct", "V_drop (%)"),
        (1, 2, "R_int_mohm", "R_int (mΩ)"),
        (2, 1, "crank_duration_s", "Crank duration (s)"),
        (2, 2, "post_start_V_std_V", "Post-start V_std (V)"),
    ]
    is_r_int_col = {col: (col == "R_int_mohm") for (_, _, col, _) in specs}
    for (r, c, col, lbl) in specs:
        for tap in TAPPERS:
            sub = f[f["can_tapper_id"] == tap]
            if is_r_int_col[col] and "invalid_R_int" in sub.columns:
                clean, flagged = sub[~sub["invalid_R_int"]], sub[sub["invalid_R_int"]]
            else:
                clean, flagged = sub, sub.iloc[0:0]
            fig.add_trace(go.Scatter(
                x=clean["start_time"], y=clean[col],
                mode="markers", name=tap, legendgroup=tap,
                showlegend=(r == 1 and c == 1),
                marker=dict(size=5, color=TAPPER_COLOURS.get(tap)),
            ), row=r, col=c)
            if len(flagged) and not hide_flagged:
                fig.add_trace(go.Scatter(
                    x=flagged["start_time"], y=flagged[col], mode="markers", showlegend=False,
                    marker=FLAGGED_MARKER,
                    hovertemplate=f"{tap}<br>%{{x}}<br>%{{y:.1f}}<br>⚠ flagged invalid_R_int<extra></extra>",
                ), row=r, col=c)
        fig.update_yaxes(title_text=lbl, row=r, col=c)
        fig.update_xaxes(title_text="Start time", row=r, col=c, tickangle=25)

    fig.add_hline(
        y=DATASHEET_IR_MOHM, row=1, col=2,
        line=dict(color="black", dash="dot", width=1),
        annotation_text=f"Datasheet AC IR: {DATASHEET_IR_MOHM} mΩ",
        annotation_position="top right",
    )

    fig.update_layout(
        height=720, template="plotly_white",
        title=dict(text=f"Engine Start Feature Trends — {len(f)} starts across {len(TAPPERS)} taps"),
        margin=dict(t=100),
    )
    return fig


# ── Tab 3: Tapper (vehicle/channel) comparison ───────────────────────────────

def _build_tapper_fig(hide_flagged: bool = False):
    f = FEAT.copy()
    fig = make_subplots(
        rows=1, cols=3,
        horizontal_spacing=0.08,
        subplot_titles=["Voltage Drop (%)", "Internal Resistance (mΩ)", "Post-Start V_std (V)"],
    )
    for c, ykey in enumerate(["V_drop_total_pct", "R_int_mohm", "post_start_V_std_V"], start=1):
        is_r_int = (ykey == "R_int_mohm") and "invalid_R_int" in f.columns
        for tap in TAPPERS:
            tap_df = f[f["can_tapper_id"] == tap]
            if is_r_int:
                clean, flagged = tap_df.loc[~tap_df["invalid_R_int"], ykey], tap_df.loc[tap_df["invalid_R_int"], ykey]
            else:
                clean, flagged = tap_df[ykey], tap_df[ykey].iloc[0:0]
            fig.add_trace(go.Box(
                y=clean, name=tap, marker_color=TAPPER_COLOURS.get(tap),
                boxpoints="outliers", legendgroup=tap, showlegend=(c == 1),
            ), row=1, col=c)
            if len(flagged) and not hide_flagged:
                # Same category (tap name) so it aligns at the same x slot;
                # box/line hidden so only the flagged points themselves show.
                fig.add_trace(go.Box(
                    y=flagged, name=tap, marker=FLAGGED_MARKER,
                    boxpoints="all", jitter=0.5, pointpos=0,
                    line=dict(color="rgba(0,0,0,0)", width=0), fillcolor="rgba(0,0,0,0)",
                    showlegend=False,
                    hovertemplate=f"{tap}<br>%{{y:.1f}}<br>⚠ flagged invalid_R_int<extra></extra>",
                ), row=1, col=c)
    fig.add_hline(
        y=DATASHEET_IR_MOHM, row=1, col=2,
        line=dict(color="black", dash="dot", width=1),
        annotation_text=f"Datasheet AC IR: {DATASHEET_IR_MOHM} mΩ",
        annotation_position="top right",
    )
    fig.update_layout(
        height=520, template="plotly_white",
        title=dict(text=f"Per-Tap Comparison — {len(TAPPERS)} CAN Taps"),
        legend=dict(orientation="h", y=-0.22, x=0.15),
        margin=dict(t=100, b=100),
    )
    return fig


# ── Tab 4: SoC & temperature effects ─────────────────────────────────────────
# Redesigned 2026-08-26: the original version coloured each of 4799 overlapping
# points by a continuous temperature scale. That can't answer an INTERACTION
# question ("is voltage drop worst specifically at low SoC *and* low temp
# together?") -- overplotted, semi-transparent points of similar hue wash out
# into a blur, which is exactly the "I don't see all the colours" symptom.
# Replaced with a 2-D binned grid (SoC x temperature), colour = mean feature
# value per cell -- the standard way to show a two-variable interaction on a
# continuous outcome, and immune to overplotting since it aggregates instead
# of stacking points. See can_stats.joint_grid()'s docstring for the full
# reasoning, and why the temperature axis uses equal-COUNT terciles rather
# than fixed-width bands (verified: fixed 10C bands leave some cells at n=1).

# Sequential single-hue ramps, light -> dark, each for a DIFFERENT quantity --
# never the same ramp for count vs. a measurement, so a reader can't mistake
# "lots of starts here" for "high voltage drop here". SEQ_BLUE is also reused
# by the Feature vs SoC tab's density panel, so "blue = how many starts"
# means the same thing on both tabs.
SEQ_AMBER = [[0.0, "#FFF7ED"], [0.25, "#FCD9A0"], [0.5, "#F0A94E"], [0.75, "#D97F1E"], [1.0, "#8A4B0F"]]

# Computed ONCE from the full dataset and reused for every panel in this tab's
# figure (density + both feature grids) so all three share an identical grid
# -- see joint_grid()'s docstring for why recomputing terciles per-panel
# would silently misalign them.
_SOC_TEMP_Y_EDGES, _SOC_TEMP_Y_LABELS = can_stats.temperature_tercile_edges(FEAT)


def _heatmap_trace(grid: dict, colorscale, colorbar_title: str, colorbar_x: float) -> tuple:
    """One heatmap trace: cell fill = grid['value'] (or grid['count'] when no
    value grid exists, i.e. the density panel). Returns (trace, zmin, zmax) --
    zmin/zmax are needed again by `_add_cell_labels` so the label colour
    decision uses the identical normalisation the fill colour did.

    `colorbar_x` is explicit rather than left to Plotly's default: with three
    heatmap subplots in one row, un-positioned colorbars stack on top of each
    other at the figure's right edge (confirmed -- that's the bug this
    replaces). Each panel gets its own colorbar placed in the gap right after
    its own column.
    """
    z = grid["value"] if grid["value"] is not None else grid["count"]
    n = grid["count"].astype(int)
    finite = z[np.isfinite(z)]
    zmin, zmax = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
    trace = go.Heatmap(
        z=z, x=grid["x_labels"], y=grid["y_labels"],
        colorscale=colorscale, zmin=zmin, zmax=zmax,
        customdata=n,
        hovertemplate="SoC %{x} · %{y}<br>value: %{z:.2f}<br>n = %{customdata} starts<extra></extra>",
        colorbar=dict(title=colorbar_title, thickness=14, len=0.85, x=colorbar_x),
        xgap=2, ygap=2,
    )
    return trace, zmin, zmax


def _add_cell_labels(fig: go.Figure, grid: dict, value_fmt: str, row: int, col: int,
                     zmin: float, zmax: float) -> None:
    """Print each cell's value as a manual annotation (not go.Heatmap's built-in
    text/texttemplate) specifically so the font colour can be chosen per cell:
    white on the dark half of the ramp, dark ink on the light half -- Heatmap's
    own `textfont.color` only accepts one colour for the whole trace (checked
    against the installed plotly version), which is illegible at one end or
    the other of a light-to-dark ramp spanning this many cells. Only 18 cells
    per panel, so 54 annotations total is cheap."""
    z = grid["value"] if grid["value"] is not None else grid["count"]
    span = zmax - zmin
    for i, ylab in enumerate(grid["y_labels"]):
        for j, xlab in enumerate(grid["x_labels"]):
            v = z[i, j]
            if np.isnan(v):
                continue
            frac = (v - zmin) / span if span > 0 else 0.5
            color = "#FFFFFF" if frac > 0.55 else "#1A1A2E"
            fig.add_annotation(
                x=xlab, y=ylab, text=value_fmt.format(v), showarrow=False,
                font=dict(size=12, color=color), row=row, col=col,
            )


def _build_soc_temp_fig():
    y_edges, y_labels = _SOC_TEMP_Y_EDGES, _SOC_TEMP_Y_LABELS

    g_density = can_stats.joint_grid(FEAT, y_edges=y_edges, y_labels=y_labels)
    g_vdrop = can_stats.joint_grid(FEAT, feature="V_drop_total_pct", y_edges=y_edges, y_labels=y_labels)
    g_rint = can_stats.joint_grid(FEAT, feature="R_int_mohm", y_edges=y_edges, y_labels=y_labels)

    # Statistical backup for what the grid shows visually: does temperature
    # still move with the feature once SoC is held constant (and vice versa)?
    r_vdrop, r_vdrop_partial, p_vdrop, n_vdrop = can_stats.spearman_partial(
        FEAT["temp_avg_start_C"], FEAT["V_drop_total_pct"], FEAT["soc_pct_est_pack"])
    clean_rint = can_stats.clean_for(FEAT, "R_int_mohm")
    r_rint, r_rint_partial, p_rint, n_rint = can_stats.spearman_partial(
        clean_rint["temp_avg_start_C"], clean_rint["R_int_mohm"], clean_rint["soc_pct_est_pack"])

    fig = make_subplots(
        rows=1, cols=3, horizontal_spacing=0.09,
        subplot_titles=[
            f"Where the starts are (n={int(g_density['count'].sum())})",
            f"Voltage drop (%) — mean per cell<br><sub>temp ρ={r_vdrop:+.2f} "
            f"(ρ={r_vdrop_partial:+.2f} net of SoC), n={n_vdrop}</sub>",
            f"Internal resistance (mΩ) — mean per cell<br><sub>temp ρ={r_rint:+.2f} "
            f"(ρ={r_rint_partial:+.2f} net of SoC), n={n_rint}</sub>",
        ],
    )
    # x positions sit in the gaps between the three columns (horizontal_spacing
    # above carves out 0.09 of figure width there); rightmost sits just past
    # the third column's own right edge (x=1.02), same convention as every
    # other multi-colorbar figure in this file (e.g. the ANOVA tab's x=1.01).
    t_density, z0_d, z1_d = _heatmap_trace(g_density, SEQ_BLUE, "Starts", colorbar_x=0.305)
    t_vdrop, z0_v, z1_v = _heatmap_trace(g_vdrop, SEQ_AMBER, "V_drop<br>(%)", colorbar_x=0.665)
    t_rint, z0_r, z1_r = _heatmap_trace(g_rint, SEQ_AMBER, "R_int<br>(mΩ)", colorbar_x=1.02)
    fig.add_trace(t_density, row=1, col=1)
    fig.add_trace(t_vdrop, row=1, col=2)
    fig.add_trace(t_rint, row=1, col=3)
    _add_cell_labels(fig, g_density, "{:.0f}", row=1, col=1, zmin=z0_d, zmax=z1_d)
    _add_cell_labels(fig, g_vdrop, "{:.1f}", row=1, col=2, zmin=z0_v, zmax=z1_v)
    _add_cell_labels(fig, g_rint, "{:.1f}", row=1, col=3, zmin=z0_r, zmax=z1_r)

    for c in (1, 2, 3):
        fig.update_xaxes(title_text="SoC at start", row=1, col=c)
    fig.update_yaxes(title_text="Battery temperature", row=1, col=1)
    # Panels 2-3 share the identical y categories with panel 1 (same three
    # temperature bands) -- repeating the tick labels on every panel is
    # redundant, and collided with each panel's own colorbar sitting in the
    # gap right in front of it (confirmed visually). Small-multiples
    # convention: show the shared axis once, on the leftmost panel only.
    fig.update_yaxes(showticklabels=False, row=1, col=2)
    fig.update_yaxes(showticklabels=False, row=1, col=3)

    fig.update_layout(
        height=440, template="plotly_white",
        title=dict(text=("SoC × Temperature — mean voltage drop & internal resistance per cell"
                         "<br><sub>cell blank = fewer than 20 starts, not enough to trust a mean; "
                         "hover for exact n</sub>")),
        margin=dict(t=110, b=60),
    )
    return fig


# ── Tab 5: Post-start voltage stability — the new field feature ─────────────

def _build_poststart_fig():
    f = FEAT.dropna(subset=["post_start_V_std_V"]).copy()
    fig = make_subplots(
        rows=1, cols=2,
        horizontal_spacing=0.12,
        subplot_titles=["Post-Start V_std Distribution", "Post-Start V_std vs SoC at Start"],
    )
    fig.add_trace(go.Histogram(x=f["post_start_V_std_V"], nbinsx=40, marker_color="#2C4F8C"), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=f["soc_pct_est_pack"], y=f["post_start_V_std_V"], mode="markers", showlegend=False,
        marker=dict(size=6, color=[TAPPER_COLOURS.get(t) for t in f["can_tapper_id"]]),
    ), row=1, col=2)
    fig.update_xaxes(title_text="Post-start V_std (V)", row=1, col=1)
    fig.update_yaxes(title_text="Count", row=1, col=1)
    fig.update_xaxes(title_text="SoC at start (%)", row=1, col=2)
    fig.update_yaxes(title_text="Post-start V_std (V)", row=1, col=2)
    fig.update_layout(
        height=440, template="plotly_white",
        title="Post-Start (15-115s) Voltage Stability — field analogue of the lab's driving_aux_load_V_std feature",
        margin=dict(t=90),
    )
    return fig


# ── Tab 6: Outlier Inspector ──────────────────────────────────────────────────

OUTLIER_FEATURES = {
    "Voltage drop during crank (%)": "V_drop_total_pct",
    "Pack internal resistance (mΩ)": "R_int_mohm",
    "Post-start voltage std. dev. (V)": "post_start_V_std_V",
}

# Long-form prose notes live in dashboard_notes/*.md, not as inline string
# literals here -- keeps this file to code, and lets the notes be read/edited
# without touching Python.
KNOWN_INCIDENTS_MD = _read_note("outlier_known_incidents.md")


def _flag_outliers(s: pd.Series, k: float = 3.0) -> pd.Series:
    """Tukey far-out fence (k=3x IQR by default) -- same convention as the Lab vs
    Field tab's axis capping, so 'outlier' means the same thing across this dashboard."""
    q1, q3 = s.quantile([0.25, 0.75])
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series(False, index=s.index)
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return (s < lo) | (s > hi)


def _outlier_frame(feature_key: str, tap: str, k: float):
    f = FEAT if tap == "All taps" else FEAT[FEAT["can_tapper_id"] == tap]
    valid = f[feature_key].dropna()
    mask = _flag_outliers(valid, k=k).reindex(f.index, fill_value=False)
    return f, mask


def _build_outlier_timeline(feature_key: str, tap: str, k: float) -> go.Figure:
    f, mask = _outlier_frame(feature_key, tap, k)
    label = next(lbl for lbl, key in OUTLIER_FEATURES.items() if key == feature_key)

    # invalid_R_int is a separate, physically-motivated flag (I_load_A too
    # low/missing/negative) -- never conflated with the generic Tukey-fence
    # `mask` above, and only relevant when R_int itself is the selected
    # feature. A row flagged invalid_R_int is shown with FLAGGED_MARKER
    # regardless of whether it also happens to be a Tukey outlier, so the
    # three buckets (normal / Tukey outlier / invalid_R_int) are disjoint.
    is_r_int = (feature_key == "R_int_mohm") and "invalid_R_int" in f.columns
    invalid_mask = f["invalid_R_int"] if is_r_int else pd.Series(False, index=f.index)
    normal = f[~mask & ~invalid_mask]
    outliers = f[mask & ~invalid_mask]
    invalid = f[invalid_mask]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=normal["start_time"], y=normal[feature_key], mode="markers", name="Normal",
        marker=dict(size=6, color=[TAPPER_COLOURS.get(t, "#888") for t in normal["can_tapper_id"]], opacity=0.6),
        text=normal["can_tapper_id"], hovertemplate="%{text}<br>%{x}<br>%{y:.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=outliers["start_time"], y=outliers[feature_key], mode="markers", name=f"Outlier (n={len(outliers)})",
        marker=dict(size=12, color="#e34948", symbol="circle-open", line=dict(width=2.5)),
        text=outliers["can_tapper_id"], hovertemplate="%{text}<br>%{x}<br>%{y:.2f}<extra></extra>",
    ))
    if is_r_int:
        fig.add_trace(go.Scatter(
            x=invalid["start_time"], y=invalid[feature_key], mode="markers", name=f"Invalid R_int (n={len(invalid)})",
            marker=FLAGGED_MARKER,
            text=invalid["can_tapper_id"],
            hovertemplate="%{text}<br>%{x}<br>%{y:.2f}<br>⚠ flagged invalid_R_int<extra></extra>",
        ))
    fig.update_layout(
        height=420, template="plotly_white",
        title=f"{label} over time" + (f" — {tap}" if tap != "All taps" else " — all taps"),
        xaxis_title="Start time", yaxis_title=label,
        legend=dict(orientation="h", y=-0.2, x=0),
        margin=dict(t=60, b=60),
    )
    return fig


def label_for_col(feature_key: str) -> str:
    return next(lbl for lbl, key in OUTLIER_FEATURES.items() if key == feature_key)


# A single persistent, selectable Tabulator -- rebuilt-in-place (via
# .value =) rather than recreated by pn.bind, so its .selection survives
# across re-renders and can itself be bound to drive the raw-trace panel
# below. Selection resets to empty whenever the filtered set changes.
OUTLIER_TABLE = pn.widgets.Tabulator(
    pd.DataFrame(), height=320, sizing_mode="stretch_width", page_size=15,
    disabled=True, selectable=1,
)


def _refresh_outlier_table(feature_key: str, tap: str, k: float) -> None:
    f, mask = _outlier_frame(feature_key, tap, k)
    flagged = f[mask].sort_values("start_time").copy()
    cols = ["start_index", "start_time", "can_tapper_id", feature_key, "I_load_A", "delta_I_A",
            "V_pre_group_avg_V", "post_start_V_mean_V", "temp_avg_start_C", "soc_pct_est_pack"]
    if feature_key == "R_int_mohm":
        cols += ["invalid_R_int", "R_int_current_sensor_dropout", "R_int_negative_current"]
    cols = [c for c in cols if c in flagged.columns]
    flagged = flagged[cols].round(3).reset_index(drop=True)
    OUTLIER_TABLE.titles = {"start_index": "Start #", "can_tapper_id": "Tap", feature_key: label_for_col(feature_key)}
    OUTLIER_TABLE.value = flagged
    OUTLIER_TABLE.selection = []


# ── Raw trace for the selected outlier row ──────────────────────────────────

RAW_TRACE_GROUP_COLS = {
    "PG1_est": "FM-Center Voltage PG1_est in 1/10V",
    "PG2": "FM-Center Voltage PG2 in 1/10V",
    "PG3_est": "FM-Center Voltage PG3_est in 1/10V",
    "PG4": "FM-Center Voltage PG4 in 1/10V",
}
RAW_TRACE_GROUP_COLOURS = {"PG1_est": "#636EFA", "PG2": "#EF553B", "PG3_est": "#00CC96", "PG4": "#AB63FA"}
RAW_TRACE_EVENT_COLOURS = {
    "Engine starting command": "#d62728",
    "Glow plugs preheating": "#ff7f0e",
    "Engine running command": "#2ca02c",
}


def _build_raw_trace_fig(trace: pd.DataFrame, events: pd.DataFrame, tapper: str, start_idx: int) -> go.Figure:
    trace = trace.sort_values("t_rel")
    fig = go.Figure()
    for g, col in RAW_TRACE_GROUP_COLS.items():
        if col in trace.columns:
            fig.add_trace(go.Scatter(
                x=trace["t_rel"], y=trace[col] / 10, mode="lines", name=g,
                line=dict(color=RAW_TRACE_GROUP_COLOURS[g], width=1.5),
            ))
    for _, row in events.iterrows():
        fig.add_vline(
            x=row["t_rel"],
            line=dict(color=RAW_TRACE_EVENT_COLOURS.get(row["event_label"], "grey"), dash="dash", width=1.5),
            annotation_text=row["event_label"], annotation_position="top",
        )
    fig.update_xaxes(title_text="Time relative to start command (s)")
    fig.update_yaxes(title_text="Per-group voltage (V)")
    fig.update_layout(
        height=460, template="plotly_white",
        title=f"Raw voltage trace — {tapper}, start_index {start_idx} (-60s .. +120s)",
        legend=dict(orientation="h", y=-0.2, x=0),
        margin=dict(t=60, b=70),
    )
    return fig


def _build_selected_raw_trace(selection: list, table_value: pd.DataFrame):
    if OUTLIER_RAW_TRACES is None:
        return pn.pane.Markdown(
            "*Raw traces haven't been extracted yet. Run `python build_boxer_can_features.py "
            "--zips-dir <path> --extract-outlier-traces` once to enable this panel.*",
            sizing_mode="stretch_width", margin=(10, 20, 20, 20),
        )
    if not selection or table_value is None or table_value.empty:
        return pn.pane.Markdown(
            "*Select a row in the table above to see its raw per-group voltage trace.*",
            sizing_mode="stretch_width", margin=(10, 20, 20, 20),
        )
    row = table_value.iloc[selection[0]]
    tapper, start_idx = row["can_tapper_id"], int(row["start_index"])

    trace = OUTLIER_RAW_TRACES[
        (OUTLIER_RAW_TRACES["can_tapper_id"] == tapper) & (OUTLIER_RAW_TRACES["start_index"] == start_idx)
    ]
    if trace.empty:
        return pn.pane.Markdown(
            f"*Raw trace not available for start {start_idx} ({tapper}) — it wasn't flagged when traces "
            "were last extracted. Re-run `--extract-outlier-traces` after a re-export to refresh.*",
            sizing_mode="stretch_width", margin=(10, 20, 20, 20),
        )
    events = pd.DataFrame()
    if OUTLIER_RAW_EVENTS is not None:
        events = OUTLIER_RAW_EVENTS[
            (OUTLIER_RAW_EVENTS["can_tapper_id"] == tapper) & (OUTLIER_RAW_EVENTS["start_index"] == start_idx)
        ]
    fig = _build_raw_trace_fig(trace, events, tapper, start_idx)
    return pn.pane.Plotly(fig, config={"responsive": True}, sizing_mode="stretch_width")


def _build_outlier_group_table(feature_key: str, tap: str, k: float) -> pn.widgets.Tabulator:
    f, mask = _outlier_frame(feature_key, tap, k)
    flagged_keys = f[mask][["start_index", "can_tapper_id"]]
    fp = FEAT_PK.merge(flagged_keys, on=["start_index", "can_tapper_id"], how="inner")
    fp = fp.sort_values(["start_time", "group"])
    cols = ["start_index", "start_time", "can_tapper_id", "group", "V_pre_1_10V", "V_min_1_10V",
            "V_drop_pct", "R_int_mohm", "I_load_A"]
    cols = [c for c in cols if c in fp.columns]
    fp = fp[cols].round(3)
    return pn.widgets.Tabulator(fp, height=320, sizing_mode="stretch_width", page_size=20, disabled=True)


OUTLIER_FEATURE_SELECT = pn.widgets.Select(name="Feature", options=OUTLIER_FEATURES, value="V_drop_total_pct")
OUTLIER_TAP_SELECT = pn.widgets.Select(name="CAN tap", options=["All taps"] + TAPPERS, value="All taps")
OUTLIER_K_SLIDER = pn.widgets.FloatSlider(
    name="Outlier sensitivity (IQR × k — lower = stricter)", start=1.5, end=5.0, step=0.5, value=3.0,
)

# Side-effect binding (watch=True): keeps OUTLIER_TABLE.value in sync with the
# filter widgets without recreating the widget, so its .selection can drive
# the raw-trace panel below. Call once up front to populate it before the
# layout is ever rendered.
pn.bind(_refresh_outlier_table, OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER, watch=True)
_refresh_outlier_table(OUTLIER_FEATURE_SELECT.value, OUTLIER_TAP_SELECT.value, OUTLIER_K_SLIDER.value)


def _outlier_timeline_pane(feature_key, tap, k):
    return pn.pane.Plotly(_build_outlier_timeline(feature_key, tap, k), config={"responsive": True}, sizing_mode="stretch_width")


# ── Tab 7: Lab vs Field feature-family consistency check ────────────────────

def _load_lab():
    return pd.read_csv(LAB_CSV)


LAB_FEATURES = _load_lab()
LAB_FIELD_FAMILIES = build_matched_series(LAB_FEATURES, FEAT, FEAT_PK)
LAB_FIELD_SUMMARY = summary_table(LAB_FIELD_FAMILIES)


def _build_lab_field_intro():
    md = _read_note("lab_field_comparison_intro.md")
    return pn.pane.Markdown(md, sizing_mode="stretch_width", margin=(0, 20, 10, 20))


def _build_lab_field_table():
    rows = ""
    for _, r in LAB_FIELD_SUMMARY.iterrows():
        rows += f"| {r['feature_family']} | {r['series']} | {r['n']} | {r['min']:.2f} | {r['mean']:.2f} | {r['median']:.2f} | {r['max']:.2f} |\n"
    md = f"""
### Full (uncapped) summary statistics

| Feature family | Series | n | Min | Mean | Median | Max |
|---|---|---|---|---|---|---|
{rows}
    """
    return pn.pane.Markdown(md, sizing_mode="stretch_width", margin=(10, 20, 20, 20))


# ── Tab 8: Group Effects (ANOVA) ─────────────────────────────────────────────
# All statistics come from can_stats.py (pure pandas/scipy, no Panel/Plotly), so
# the same numbers can be recomputed in an export script without a server.

ANOVA_NOTE_MD = _read_note("anova_note.md")

ANOVA_FACTOR_SELECT = pn.widgets.Select(
    name="Grouping factor",
    options={disp: col for col, (disp, _) in can_stats.FACTORS.items()},
    value="can_tapper_id", width=260,
)
ANOVA_FEATURE_SELECT = pn.widgets.Select(
    name="Feature (for the plots below)",
    options={lbl: col for col, lbl in can_stats.FEATURE_LABELS.items()},
    value="R_int_mohm", width=320,
)

# Sequential single-hue ramp for the density/p-value heatmaps: darkness means
# magnitude and nothing else (never a rainbow -- a rainbow ramp implies
# category boundaries that don't exist in a continuous quantity).
SEQ_BLUE = [[0.0, "#F2F6FB"], [0.25, "#C3D5EA"], [0.5, "#8AAED2"], [0.75, "#4B7FB5"], [1.0, "#1F4E79"]]

# Ordered temperature bands: a cool/warm diverging pair with a neutral grey
# midpoint (never three arbitrary categorical hues -- these bands are ordered,
# and the colour has to carry that order). Blue/red is also the standard
# CVD-safe polarity pair.
TERCILE_COLOURS = ["#2C7BB6", "#8C8C8C", "#D7191C"]


def _fmt_p(p: float) -> str:
    """p-values here span 1.0 down to ~1e-200; %.4f would render most of them as
    '0.0000'. Scientific notation below 1e-4, plain decimals above."""
    if not np.isfinite(p):
        return "—"
    if p < 1e-4:
        return f"{p:.2e}"
    return f"{p:.4f}"


def _build_anova_table_md(factor: str) -> pn.pane.Markdown:
    """The ANOVA family for every feature, as a markdown table (rather than a
    Tabulator) so the per-cell formatting -- sci-notation p, bolded significant
    Welch p, the Levene warning glyph -- can be controlled directly."""
    tbl = can_stats.anova_table(FEAT, factor)
    disp, note = can_stats.FACTORS[factor]
    if tbl.empty:
        return pn.pane.Markdown(f"No usable groups for **{disp}**.", margin=(0, 20))

    rows = ""
    for _, r in tbl.iterrows():
        # Levene p < 0.05 => equal-variance assumption violated => the classic F
        # column is the untrustworthy one; flag it rather than silently
        # presenting both as equally valid.
        het = " ⚠" if np.isfinite(r["Levene p"]) and r["Levene p"] < 0.05 else ""
        welch_p = _fmt_p(r["Welch p"])
        if np.isfinite(r["Welch p"]) and r["Welch p"] < 0.05:
            welch_p = f"**{welch_p}**"
        rows += (
            f"| {r['Feature']} | {int(r['n'])} | {int(r['k groups'])} | "
            f"{r['F']:.2f} | {_fmt_p(r['p'])} | {r['Welch F']:.2f} | {welch_p} | "
            f"{_fmt_p(r['KW p'])} | {r['eta2']:.4f} | {r['omega2']:.4f} | "
            f"{r['Effect']} | {_fmt_p(r['Levene p'])}{het} |\n"
        )

    md = f"""
### One-way ANOVA — feature × **{disp}**

{note}

| Feature | n | k | Classic F | p | Welch F | Welch p | Kruskal–Wallis p | η² | ω² | Effect size | Levene p |
|---|---|---|---|---|---|---|---|---|---|---|---|
{rows}

⚠ = Levene p < 0.05, equal-variance assumption violated → read the **Welch** and
**Kruskal–Wallis** columns, not classic F. Sorted by η² (effect size) descending.
"""
    return pn.pane.Markdown(md, sizing_mode="stretch_width", margin=(0, 20, 10, 20))


def _build_group_means_fig(feature: str, factor: str) -> go.Figure:
    """Group means with 95% CI error bars, plus per-group n.

    Error bars (not bare bars) because the whole point of the tab is that the
    groups differ by less than they look like they do: a CI that overlaps the
    grand-mean line is a group that ANOVA has no business calling different.
    """
    gs = can_stats.group_summary(FEAT, feature, factor)
    label = can_stats.FEATURE_LABELS.get(feature, feature)
    disp = can_stats.FACTORS[factor][0]
    if gs.empty:
        return go.Figure(layout=dict(title=f"No group has enough starts for {label}", height=380))

    grand = can_stats.clean_for(FEAT, feature)[feature].mean()
    # Colour by tapper identity when the factor *is* the tapper, so a tap keeps
    # the same colour it has on every other tab; otherwise one neutral accent
    # (the levels of a binned factor are ordered, not identities).
    colours = ([TAPPER_COLOURS.get(lv, "#2C4F8C") for lv in gs["level"]]
               if factor == "can_tapper_id" else "#2C4F8C")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=gs["level"], y=gs["mean"], mode="markers",
        marker=dict(size=11, color=colours, line=dict(width=1.5, color="white")),
        error_y=dict(type="data", array=gs["ci95"], visible=True, thickness=2, width=6, color="#5A5A5A"),
        customdata=np.stack([gs["n"], gs["median"], gs["sd"]], axis=-1),
        hovertemplate=("%{x}<br>mean %{y:.3f} ± %{error_y.array:.3f} (95% CI)"
                       "<br>median %{customdata[1]:.3f}<br>sd %{customdata[2]:.3f}"
                       "<br>n = %{customdata[0]}<extra></extra>"),
        # Single series -- the title and y-axis already name it, so no legend box.
        showlegend=False,
    ))
    fig.add_hline(
        y=grand, line=dict(color="#B04A2F", dash="dash", width=1.5),
        annotation_text=f"Grand mean: {grand:.3f}", annotation_position="top left",
    )
    fig.update_layout(
        height=400, template="plotly_white",
        title=f"{label} — group means ± 95% CI by {disp}",
        xaxis_title=disp, yaxis_title=label, xaxis_tickangle=25,
        margin=dict(t=70, b=110),
    )
    return fig


def _build_posthoc_fig(feature: str, factor: str) -> go.Figure:
    """Games-Howell pairwise heatmap: cell = mean difference (A - B), and only
    pairs significant at p < 0.05 are drawn. Showing the *difference* rather
    than the p-value is deliberate -- at this n a p-value heatmap would be an
    almost-uniform block of "significant" and say nothing about magnitude."""
    gh = can_stats.games_howell(FEAT, feature, factor)
    label = can_stats.FEATURE_LABELS.get(feature, feature)
    disp = can_stats.FACTORS[factor][0]
    if gh.empty:
        return go.Figure(layout=dict(title=f"Not enough groups for a post-hoc test on {label}", height=380))

    levels, pmat, dmat = can_stats.games_howell_matrix(gh)
    masked = np.where(np.isfinite(pmat) & (pmat < 0.05), dmat, np.nan)
    n_sig = int((gh["p (Games-Howell)"] < 0.05).sum())

    # Diverging scale centred on zero: the sign of a mean difference is
    # meaningful (which group is higher), so a two-hue + neutral-midpoint
    # ramp is the correct encoding, symmetric about 0 so equal magnitudes
    # of either sign read as equally intense.
    lim = np.nanmax(np.abs(masked)) if np.isfinite(masked).any() else 1.0
    fig = go.Figure(go.Heatmap(
        z=masked, x=levels, y=levels,
        colorscale="RdBu_r", zmid=0, zmin=-lim, zmax=lim,
        customdata=pmat,
        hovertemplate="row %{y} − col %{x}<br>Δmean = %{z:.3f}<br>p = %{customdata:.2e}<extra></extra>",
        colorbar=dict(title=f"Δ mean<br>(row − col)", thickness=14),
        hoverongaps=False, xgap=1, ygap=1,
    ))
    fig.update_layout(
        height=520, template="plotly_white",
        title=(f"Games–Howell post-hoc — {label} by {disp}<br>"
               f"<sub>{n_sig} of {len(gh)} pairs significant at p &lt; 0.05; "
               f"non-significant pairs left blank</sub>"),
        xaxis=dict(tickangle=45, title=disp), yaxis=dict(title=disp, autorange="reversed"),
        margin=dict(t=90, b=110, l=140),
    )
    return fig


def _anova_table_pane(factor):
    return _build_anova_table_md(factor)


def _anova_means_pane(feature, factor):
    return pn.pane.Plotly(_build_group_means_fig(feature, factor),
                          config={"responsive": True}, sizing_mode="stretch_width")


def _anova_posthoc_pane(feature, factor):
    return pn.pane.Plotly(_build_posthoc_fig(feature, factor),
                          config={"responsive": True}, sizing_mode="stretch_width")


# ── Tab 9: Feature vs SoC — density, binned trend, temperature split ─────────

SOC_NOTE_MD = _read_note("soc_correlation_note.md")

SOC_FEATURE_SELECT = pn.widgets.Select(
    name="Feature",
    options={lbl: col for col, lbl in can_stats.FEATURE_LABELS.items()},
    value="V_drop_total_pct", width=320,
)
SOC_XAXIS_SELECT = pn.widgets.Select(
    name="X axis",
    options={"SoC at start (%)": "soc_pct_est_pack", "Battery temperature (°C)": "temp_avg_start_C"},
    value="soc_pct_est_pack", width=240,
)


def _build_soc_density_fig(feature: str, x_col: str) -> go.Figure:
    """Three panels answering "is there a relationship, and is it usable?":

      1. 2-D density  -- where the 4799 starts actually are (the raw scatter
                         can't show this; it saturates).
      2. Binned median + IQR -- the trend, robust to overplotting and outliers.
      3. Temperature terciles -- the same trend split three ways, to show
                         whether the relationship is really temperature.

    Deliberately *not* a raw scatter coloured by a continuous ramp -- that was
    the SoC & Temp Effects tab's original encoding, replaced 2026-08-26 for
    the same overplotting reason this tab avoids it (that tab now uses a
    binned SoC x temperature grid instead; this tab's temperature-tercile
    panel is the 1-D complement to that tab's 2-D one).
    """
    label = can_stats.FEATURE_LABELS.get(feature, feature)
    x_label = "SoC at start (%)" if x_col == "soc_pct_est_pack" else "Battery temperature (°C)"
    control = "temp_avg_start_C" if x_col == "soc_pct_est_pack" else "soc_pct_est_pack"
    control_label = "temperature" if x_col == "soc_pct_est_pack" else "SoC"

    d = can_stats.clean_for(FEAT, feature).dropna(subset=[x_col, control])
    rho, rho_p, p_partial, n = can_stats.spearman_partial(d[x_col], d[feature], d[control])
    strength = can_stats.correlation_strength(rho)

    # Same robust view-capping convention as every other tab: view-only, the
    # underlying data is never dropped.
    y_range, _, _ = robust_axis_range(d[feature].to_numpy())
    x_range, _, _ = robust_axis_range(d[x_col].to_numpy())
    bins = np.linspace(x_range[0], x_range[1], 13)

    fig = make_subplots(
        rows=1, cols=3, horizontal_spacing=0.075,
        subplot_titles=[
            f"Where the starts are (n={n})",
            "Median ± IQR per bin",
            f"Split by temperature tercile" if x_col == "soc_pct_est_pack" else "Split by SoC tercile",
        ],
    )

    # -- Panel 1: 2-D density
    fig.add_trace(go.Histogram2d(
        x=d[x_col], y=d[feature], colorscale=SEQ_BLUE,
        xbins=dict(start=x_range[0], end=x_range[1], size=(x_range[1] - x_range[0]) / 28),
        ybins=dict(start=y_range[0], end=y_range[1], size=(y_range[1] - y_range[0]) / 28),
        colorbar=dict(title="starts", thickness=12, x=0.285, len=0.9),
        hovertemplate=f"{x_label}: %{{x}}<br>{label}: %{{y}}<br>%{{z}} starts<extra></extra>",
    ), row=1, col=1)

    # -- Panel 2: binned median + IQR band
    bt = can_stats.binned_trend(FEAT, feature, x_col=x_col, bins=bins)
    if not bt.empty:
        fig.add_trace(go.Scatter(
            x=np.concatenate([bt["x_center"], bt["x_center"][::-1]]),
            y=np.concatenate([bt["q3"], bt["q1"][::-1]]),
            fill="toself", fillcolor="rgba(44,79,140,0.16)",
            line=dict(width=0), hoverinfo="skip", name="All starts: IQR (25–75%)",
        ), row=1, col=2)
        fig.add_trace(go.Scatter(
            x=bt["x_center"], y=bt["median"], mode="lines+markers",
            line=dict(color="#2C4F8C", width=2), marker=dict(size=8),
            customdata=np.stack([bt["n"], bt["q1"], bt["q3"]], axis=-1),
            hovertemplate=(f"{x_label}: %{{x}}<br>median {label}: %{{y:.3f}}"
                           "<br>IQR %{customdata[1]:.3f}–%{customdata[2]:.3f}"
                           "<br>n = %{customdata[0]}<extra></extra>"),
            # Named "All starts" so it can't be confused with the same-family
            # blue of the Cold tercile line in the panel next to it.
            name="All starts: median", showlegend=True,
        ), row=1, col=2)

    # -- Panel 3: same trend, split into three equal-count bands of the confounder
    band, band_labels, _ = can_stats.temperature_terciles(FEAT, col=control)
    fsplit = FEAT.assign(_band=band)
    for lbl_i, colour in zip(band_labels, TERCILE_COLOURS):
        sub = fsplit[fsplit["_band"] == lbl_i]
        bt_i = can_stats.binned_trend(sub, feature, x_col=x_col, bins=bins, min_count=8)
        if bt_i.empty:
            continue
        fig.add_trace(go.Scatter(
            x=bt_i["x_center"], y=bt_i["median"], mode="lines+markers",
            line=dict(color=colour, width=2), marker=dict(size=7),
            name=lbl_i, legendgroup=lbl_i,
            customdata=bt_i["n"],
            hovertemplate=(f"{lbl_i}<br>{x_label}: %{{x}}<br>median: %{{y:.3f}}"
                           "<br>n = %{customdata}<extra></extra>"),
        ), row=1, col=3)

    for c in (1, 2, 3):
        fig.update_xaxes(title_text=x_label, range=x_range, row=1, col=c)
        fig.update_yaxes(range=y_range, row=1, col=c)
    fig.update_yaxes(title_text=label, row=1, col=1)

    verdict = (f"Spearman ρ = {rho:+.3f} ({strength}, {100 * rho ** 2:.1f}% of variance explained) · "
               f"ρ = {rho_p:+.3f} net of {control_label} (p = {_fmt_p(p_partial)}) · n = {n}")
    fig.update_layout(
        height=470, template="plotly_white",
        title=dict(text=f"{label} vs {x_label}<br><sub>{verdict}</sub>"),
        legend=dict(orientation="h", y=-0.24, x=0.36),
        margin=dict(t=100, b=90),
    )
    return fig


def _build_correlation_table_md(x_col: str) -> pn.pane.Markdown:
    control = "temp_avg_start_C" if x_col == "soc_pct_est_pack" else "soc_pct_est_pack"
    x_label = "SoC at start" if x_col == "soc_pct_est_pack" else "battery temperature"
    control_label = "battery temperature" if x_col == "soc_pct_est_pack" else "SoC"
    tbl = can_stats.correlation_table(FEAT, x_col=x_col, control=control)
    if tbl.empty:
        return pn.pane.Markdown("No usable correlations.", margin=(0, 20))

    rows = ""
    for _, r in tbl.iterrows():
        # Circularity marker: SoC is an OCV-derived quantity upstream of this
        # repo, so correlating it against a pre-crank voltage feature is
        # definitional, not a finding. Flagged rather than hidden.
        circular = (" †" if (x_col == "soc_pct_est_pack" and r["column"] == "V_pre_group_avg_V") else "")
        rows += (
            f"| {r['Feature']}{circular} | {int(r['n'])} | {r['Spearman rho']:+.3f} | "
            f"{_fmt_p(r['p'])} | {r['Pearson r']:+.3f} | {r['rho | control']:+.3f} | "
            f"{_fmt_p(r['p | control'])} | {100 * r['Var. explained (rho^2)']:.1f}% | {r['Strength']} |\n"
        )

    md = f"""
### Correlation of every feature with {x_label}

| Feature | n | Spearman ρ | p | Pearson r | ρ \\| {control_label} | p (partial) | Variance explained | Strength |
|---|---|---|---|---|---|---|---|---|
{rows}

Sorted by \\|ρ\\| descending. **Variance explained (ρ²) is the column that answers
"is this relationship usable?"** — at n ≈ 4 800 essentially every row is
"significant", so p is not evidence of a meaningful effect.
{"† circular: `soc_pct_est_pack` is itself OCV-derived, so this row is definitional, not a physical finding." if x_col == "soc_pct_est_pack" else ""}
"""
    return pn.pane.Markdown(md, sizing_mode="stretch_width", margin=(10, 20, 20, 20))


def _soc_density_pane(feature, x_col):
    return pn.pane.Plotly(_build_soc_density_fig(feature, x_col),
                          config={"responsive": True}, sizing_mode="stretch_width")


def _soc_corr_table_pane(x_col):
    return _build_correlation_table_md(x_col)


# ── Summary pane ──────────────────────────────────────────────────────────────

def _build_summary():
    f = FEAT
    n_events = len(f)
    t_min, t_max = f["start_time"].dt.date.min(), f["start_time"].dt.date.max()
    tap_rows = ""
    for tap in TAPPERS:
        sub = f[f["can_tapper_id"] == tap]
        tap_rows += f"| {tap} | {len(sub)} | {sub['start_time'].dt.date.min()} | {sub['start_time'].dt.date.max()} |\n"

    dataset_notes = _read_note("dataset_summary_notes.md").format(
        n_more_taps=len(TAPPERS) - 1, n_more_starts=n_events - 41,
    )

    md = f"""
## Boxer Motor-Start CAN Export — {len(TAPPERS)} Taps, {n_events} Starts

**Window per start:** 60s before .. 120s after (`tb60_ta120`) | **Commando markers:** Engine starting command, Glow plugs preheating, Engine running command
**Date range (all taps):** {t_min} -- {t_max}

{dataset_notes}
### Per-tap breakdown

| Tap | Starts | First start | Last start |
|---|---|---|---|
{tap_rows}
### Pack-level feature summary (all {n_events} starts)

| Metric | Min | Mean | Max |
|---|---|---|---|
| Voltage drop during crank (%) | {f["V_drop_total_pct"].min():.1f} | {f["V_drop_total_pct"].mean():.1f} | {f["V_drop_total_pct"].max():.1f} |
| Pack internal resistance (mΩ) | {f["R_int_mohm"].min():.1f} | {f["R_int_mohm"].mean():.1f} | {f["R_int_mohm"].max():.1f} |
| SoC at start (%) | {f["soc_pct_est_pack"].min():.0f} | {f["soc_pct_est_pack"].mean():.0f} | {f["soc_pct_est_pack"].max():.0f} |
| Crank duration, commando start→running (s) | {f["crank_duration_s"].min():.2f} | {f["crank_duration_s"].mean():.2f} | {f["crank_duration_s"].max():.2f} |
| Glow-plug lead time (s, +before start) | {f["glow_lead_s"].min():.2f} | {f["glow_lead_s"].mean():.2f} | {f["glow_lead_s"].max():.2f} |
| Post-start (15-115s) voltage std. dev. (V) | {f["post_start_V_std_V"].min():.3f} | {f["post_start_V_std_V"].mean():.3f} | {f["post_start_V_std_V"].max():.3f} |
    """
    return pn.pane.Markdown(md, sizing_mode="stretch_width", margin=(0, 20, 20, 20))


# ── R_int flag controls, shared by the two tabs below ────────────────────────
# One markdown caption + one checkbox, each reused (same widget/pane pattern,
# not copies) across Feature Trends / Tap Comparison -- toggling "hide
# flagged" from either applies to both. SoC & Temp Effects no longer uses
# this: since 2026-08-26 it's an aggregated grid (can_stats.joint_grid), which
# has no individual points to show/hide -- it always excludes invalid_R_int
# rows from the R_int cell means (same rule as every stat in can_stats.py),
# unconditionally, via clean_for().

def _invalid_r_int_controls() -> pn.Column:
    return pn.Column(
        pn.pane.Markdown(INVALID_R_INT_NOTE_MD, sizing_mode="stretch_width", margin=(0, 20, 0, 20)),
        pn.Row(HIDE_FLAGGED_TOGGLE, margin=(4, 20, 10, 20)),
        sizing_mode="stretch_width",
    )


def _trends_pane(hide_flagged: bool):
    return pn.pane.Plotly(_build_trends_fig(hide_flagged), config={"responsive": True}, sizing_mode="stretch_width")


def _tapper_pane(hide_flagged: bool):
    return pn.pane.Plotly(_build_tapper_fig(hide_flagged), config={"responsive": True}, sizing_mode="stretch_width")




# ── Layout ────────────────────────────────────────────────────────────────────

tabs = pn.Tabs(
    ("Start Profile", pn.pane.Plotly(_build_start_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("Feature Trends", pn.Column(
        _invalid_r_int_controls(),
        pn.bind(_trends_pane, HIDE_FLAGGED_TOGGLE),
        sizing_mode="stretch_width",
    )),
    ("Tap Comparison", pn.Column(
        _invalid_r_int_controls(),
        pn.bind(_tapper_pane, HIDE_FLAGGED_TOGGLE),
        sizing_mode="stretch_width",
    )),
    ("SoC & Temp Effects", pn.pane.Plotly(
        _build_soc_temp_fig(), config={"responsive": True}, sizing_mode="stretch_width",
    )),
    ("Post-Start Stability (new)", pn.pane.Plotly(_build_poststart_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("Outlier Inspector (new)", pn.Column(
        pn.pane.Markdown(KNOWN_INCIDENTS_MD, sizing_mode="stretch_width", margin=(0, 20, 10, 20)),
        pn.Row(OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER, margin=(0, 20, 10, 20)),
        pn.bind(_outlier_timeline_pane, OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER),
        pn.pane.Markdown("#### Flagged starts — pack level (click a row to see its raw trace below)",
                          sizing_mode="stretch_width", margin=(10, 20, 0, 20)),
        OUTLIER_TABLE,
        pn.pane.Markdown("#### Raw voltage trace for the selected start", sizing_mode="stretch_width", margin=(10, 20, 0, 20)),
        pn.bind(_build_selected_raw_trace, OUTLIER_TABLE.param.selection, OUTLIER_TABLE.param.value),
        pn.pane.Markdown("#### Per-group breakdown for the same flagged starts", sizing_mode="stretch_width", margin=(10, 20, 0, 20)),
        pn.bind(_build_outlier_group_table, OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER),
        sizing_mode="stretch_width",
    )),
    ("Feature vs SoC (new)", pn.Column(
        pn.pane.Markdown(SOC_NOTE_MD, sizing_mode="stretch_width", margin=(0, 20, 10, 20)),
        pn.Row(SOC_FEATURE_SELECT, SOC_XAXIS_SELECT, margin=(0, 20, 10, 20)),
        pn.bind(_soc_density_pane, SOC_FEATURE_SELECT, SOC_XAXIS_SELECT),
        pn.bind(_soc_corr_table_pane, SOC_XAXIS_SELECT),
        sizing_mode="stretch_width",
    )),
    ("Group Effects / ANOVA (new)", pn.Column(
        pn.pane.Markdown(ANOVA_NOTE_MD, sizing_mode="stretch_width", margin=(0, 20, 10, 20)),
        pn.Row(ANOVA_FACTOR_SELECT, ANOVA_FEATURE_SELECT, margin=(0, 20, 10, 20)),
        pn.bind(_anova_table_pane, ANOVA_FACTOR_SELECT),
        pn.bind(_anova_means_pane, ANOVA_FEATURE_SELECT, ANOVA_FACTOR_SELECT),
        pn.bind(_anova_posthoc_pane, ANOVA_FEATURE_SELECT, ANOVA_FACTOR_SELECT),
        sizing_mode="stretch_width",
    )),
    ("Lab vs Field Comparison", pn.Column(
        _build_lab_field_intro(),
        *[pn.pane.Plotly(build_family_figure(fam), config={"responsive": True}, sizing_mode="stretch_width")
          for fam in LAB_FIELD_FAMILIES],
        _build_lab_field_table(),
        sizing_mode="stretch_width",
    )),
    sizing_mode="stretch_width",
)

template = pn.template.FastListTemplate(
    title=f"Boxer Battery — Motor-Start CAN Export ({len(TAPPERS)} taps, {len(FEAT)} starts)",
    main=[_build_summary(), tabs],
    accent_base_color="#2C4F8C",
    header_background="#2C4F8C",
    header_color="#FFFFFF",
    main_max_width="100%",
)

template.servable()

if __name__ == "__main__":
    pn.serve(template, show=True, port=5009)
