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

from lab_field_feature_comparison import (
    LAB_CSV, build_family_figure, build_matched_series, summary_table,
)

pn.extension("plotly", "tabulator")

DATASHEET_IR_MOHM = 1.6  # mΩ AC internal resistance per battery (new, datasheet) -- same pack as dataset_boxer

# TAPPER_COLOURS is assigned dynamically below, once the actual set of
# tappers present in the data is known (see _load() / TAPPERS).

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_boxer_can")


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

def _build_trends_fig():
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
    for (r, c, col, lbl) in specs:
        for tap in TAPPERS:
            sub = f[f["can_tapper_id"] == tap]
            fig.add_trace(go.Scatter(
                x=sub["start_time"], y=sub[col],
                mode="markers", name=tap, legendgroup=tap,
                showlegend=(r == 1 and c == 1),
                marker=dict(size=5, color=TAPPER_COLOURS.get(tap)),
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
        title=f"Engine Start Feature Trends — {len(f)} starts across {len(TAPPERS)} taps",
        margin=dict(t=80),
    )
    return fig


# ── Tab 3: Tapper (vehicle/channel) comparison ───────────────────────────────

def _build_tapper_fig():
    f = FEAT.copy()
    fig = make_subplots(
        rows=1, cols=3,
        horizontal_spacing=0.08,
        subplot_titles=["Voltage Drop (%)", "Internal Resistance (mΩ)", "Post-Start V_std (V)"],
    )
    for c, ykey in enumerate(["V_drop_total_pct", "R_int_mohm", "post_start_V_std_V"], start=1):
        for tap in TAPPERS:
            sub = f[f["can_tapper_id"] == tap][ykey]
            fig.add_trace(go.Box(
                y=sub, name=tap, marker_color=TAPPER_COLOURS.get(tap),
                boxpoints="outliers", legendgroup=tap, showlegend=(c == 1),
            ), row=1, col=c)
    fig.add_hline(
        y=DATASHEET_IR_MOHM, row=1, col=2,
        line=dict(color="black", dash="dot", width=1),
        annotation_text=f"Datasheet AC IR: {DATASHEET_IR_MOHM} mΩ",
        annotation_position="top right",
    )
    fig.update_layout(
        height=520, template="plotly_white",
        title=f"Per-Tap Comparison — {len(TAPPERS)} CAN Taps",
        legend=dict(orientation="h", y=-0.22, x=0.15),
        margin=dict(t=80, b=100),
    )
    return fig


# ── Tab 4: SoC & temperature effects ─────────────────────────────────────────

def _build_soc_temp_fig():
    f = FEAT.copy()
    fig = make_subplots(
        rows=1, cols=2,
        horizontal_spacing=0.14,
        subplot_titles=["Voltage Drop vs SoC at Start", "Internal Resistance vs SoC at Start"],
    )
    common_marker = dict(size=7, color=f["temp_avg_start_C"], colorscale="RdYlBu_r",
                          line=dict(width=0.5, color="white"))
    fig.add_trace(go.Scatter(
        x=f["soc_pct_est_pack"], y=f["V_drop_total_pct"], mode="markers", showlegend=False,
        marker=dict(**common_marker, showscale=True, colorbar=dict(title="Temp<br>(°C)", thickness=14, x=0.44)),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=f["soc_pct_est_pack"], y=f["R_int_mohm"], mode="markers", showlegend=False,
        marker=dict(**common_marker, showscale=True, colorbar=dict(title="Temp<br>(°C)", thickness=14, x=1.01)),
    ), row=1, col=2)
    for c in [1, 2]:
        fig.update_xaxes(title_text="SoC at start (%)", row=1, col=c)
    fig.update_yaxes(title_text="Voltage drop (%)", row=1, col=1)
    fig.update_yaxes(title_text="R_internal (mΩ)", row=1, col=2)
    fig.update_layout(
        height=460, template="plotly_white",
        title="SoC & Temperature Effect on Engine Start Performance (colour = battery temperature)",
        margin=dict(t=80),
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

KNOWN_INCIDENTS_MD = """
### Known data-quality incidents (found while reviewing this tab)

**`can_tapper31` — current-sensing dropout, 2026-06-11 19:38 to 2026-06-12 21:52
(start_index 388-404, 17 starts, ~26h window).** `I_load_A` collapses to single digits (-7 to 9 A)
instead of the normal ~400 A crank current seen immediately before (start 387: 500 A) and after
(start 405: 444 A) this window. `R_int_mohm` is computed as `ΔV / I_load`, so this tiny denominator
produces meaningless values (up to 3200 mΩ) — **all 11 of the whole dataset's `R_int_mohm > 50 mΩ`
outliers are from this one window on this one tap.** Voltage sample counts in the same window look
normal (2158-2160 samples/start, matching elsewhere), so this looks like a temporary fault specific
to the current channel rather than a general logging dropout. Root cause not confirmed from the
analysis side.

**`can_tapper33` — severe voltage-drop cluster, 2025-12-16 11:13-11:58
(start_index 20-23, 4 starts, ~45 min window).** Pack-level voltage drop 44.6-54.9% (vs. a
dataset-wide median of ~9%), during cold ambient temperature (4.3-6.5°C) with a *normal* crank
current (380-500 A) — unlike the tapper31 incident, this looks like a real electrical event (all
four pack groups sag together, not just one channel), consistent with a cold, weak battery under
load rather than a sensor artifact. One point within it (start_index 21, group `PG1_est`) sags far
more than its own siblings in the same start (83% vs. 44-46% for PG2/PG3_est/PG4) — `PG1_est` has
no direct sensor (estimated from the pack-level signal, see the per-group note on the Feature Trends
tab), so that specific extreme point should be treated with more suspicion than the other three.

The controls below re-derive outlier flags live from the current data (not a hardcoded list of the
starts above) — use them to check whether these incidents are still present after a re-export, or to
look for new ones.
"""


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
    normal, outliers = f[~mask], f[mask]

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
    fig.update_layout(
        height=420, template="plotly_white",
        title=f"{label} over time" + (f" — {tap}" if tap != "All taps" else " — all taps"),
        xaxis_title="Start time", yaxis_title=label,
        legend=dict(orientation="h", y=-0.2, x=0),
        margin=dict(t=60, b=60),
    )
    return fig


def _build_outlier_table(feature_key: str, tap: str, k: float) -> pn.widgets.Tabulator:
    f, mask = _outlier_frame(feature_key, tap, k)
    flagged = f[mask].sort_values("start_time").copy()
    cols = ["start_index", "start_time", "can_tapper_id", feature_key, "I_load_A", "delta_I_A",
            "V_pre_group_avg_V", "post_start_V_mean_V", "temp_avg_start_C", "soc_pct_est_pack"]
    cols = [c for c in cols if c in flagged.columns]
    flagged = flagged[cols].round(3)
    return pn.widgets.Tabulator(
        flagged, height=320, sizing_mode="stretch_width", page_size=15, disabled=True,
        titles={"start_index": "Start #", "can_tapper_id": "Tap", feature_key: label_for_col(feature_key)},
    )


def label_for_col(feature_key: str) -> str:
    return next(lbl for lbl, key in OUTLIER_FEATURES.items() if key == feature_key)


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


def _outlier_timeline_pane(feature_key, tap, k):
    return pn.pane.Plotly(_build_outlier_timeline(feature_key, tap, k), config={"responsive": True}, sizing_mode="stretch_width")


# ── Tab 7: Lab vs Field feature-family consistency check ────────────────────

def _load_lab():
    return pd.read_csv(LAB_CSV)


LAB_FEATURES = _load_lab()
LAB_FIELD_FAMILIES = build_matched_series(LAB_FEATURES, FEAT, FEAT_PK)
LAB_FIELD_SUMMARY = summary_table(LAB_FIELD_FAMILIES)


def _build_lab_field_intro():
    md = """
**Feature-family consistency check, not a value-for-value validation** — the lab battery
(Ultracell UL18-12, 12 V) and the field battery (EnerSys ArmaSafe Plus 12FV120, 24 V 2S2P) are
different products run through separate pipelines (BDPS pulse-test rig vs. vehicle CAN bus), and
the field side has no shared ground-truth SOH to validate against. This checks whether the same
feature families behave in a qualitatively similar way on both sides, per the paper's
"Lab-to-Field Validation Procedure" — not that the two sides should land on the same numbers.
`crank_duration_s`/`glow_lead_s` are field-only (the lab's crank pulses have a fixed, scripted
duration) and are intentionally excluded.

Each panel below is one condition (e.g. lab cold-crank, lab hot-crank, field), so lab and field are
never pooled into a single misleading box. Axes are capped to a robust (outlier-resistant) range —
a subtitle under each panel reports how many points were clipped from view and the most extreme
value among them; the table at the bottom always reports true, uncapped min/mean/max.
    """
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


# ── Summary pane ──────────────────────────────────────────────────────────────

def _build_summary():
    f = FEAT
    n_events = len(f)
    t_min, t_max = f["start_time"].dt.date.min(), f["start_time"].dt.date.max()
    tap_rows = ""
    for tap in TAPPERS:
        sub = f[f["can_tapper_id"] == tap]
        tap_rows += f"| {tap} | {len(sub)} | {sub['start_time'].dt.date.min()} | {sub['start_time'].dt.date.max()} |\n"

    md = f"""
## Boxer Motor-Start CAN Export — {len(TAPPERS)} Taps, {n_events} Starts

**Window per start:** 60s before .. 120s after (`tb60_ta120`) | **Commando markers:** Engine starting command, Glow plugs preheating, Engine running command
**Date range (all taps):** {t_min} -- {t_max}

Same pack as the original 41-start dataset used in the paper's Results section 5.2
(can_tapper11 IS that dataset — identical start-for-start). This export adds {len(TAPPERS) - 1}
more taps ({n_events - 41} more starts) plus CAN commando event timing and a new post-start
voltage-stability feature (`post_start_V_std`), which is the field-side analogue of
`driving_aux_load_V_std` — the strongest SoC-robust indicator found in the lab campaign
(Section 5.1 of the paper). The original 41-start dataset could not test this, since it only
ever captured crank-window features. Some taps (currently can_tapper14/15) arrived split across
multiple zip exports covering different date ranges (a single export got too large); these are
merged per-tap by build_boxer_can_features.py before this dashboard ever sees them.

**Note on units:** `V_pre_total_1_10V` in features.csv is the *sum* of all four battery-group
voltages (~2x a single 12V-class group), not the vehicle's real CAN "Total Voltage" signal.
The new post-start feature instead uses the grid's per-group-average voltage, so it's on a
directly comparable scale to `V_pre_1_10V`/`V_min_1_10V` in features_packs.csv.

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


# ── Layout ────────────────────────────────────────────────────────────────────

tabs = pn.Tabs(
    ("Start Profile", pn.pane.Plotly(_build_start_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("Feature Trends", pn.pane.Plotly(_build_trends_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("Tap Comparison", pn.pane.Plotly(_build_tapper_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("SoC & Temp Effects", pn.pane.Plotly(_build_soc_temp_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("Post-Start Stability (new)", pn.pane.Plotly(_build_poststart_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    ("Outlier Inspector (new)", pn.Column(
        pn.pane.Markdown(KNOWN_INCIDENTS_MD, sizing_mode="stretch_width", margin=(0, 20, 10, 20)),
        pn.Row(OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER, margin=(0, 20, 10, 20)),
        pn.bind(_outlier_timeline_pane, OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER),
        pn.pane.Markdown("#### Flagged starts — pack level", sizing_mode="stretch_width", margin=(10, 20, 0, 20)),
        pn.bind(_build_outlier_table, OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER),
        pn.pane.Markdown("#### Per-group breakdown for the same flagged starts", sizing_mode="stretch_width", margin=(10, 20, 0, 20)),
        pn.bind(_build_outlier_group_table, OUTLIER_FEATURE_SELECT, OUTLIER_TAP_SELECT, OUTLIER_K_SLIDER),
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
    pn.serve(template, show=True, port=5008)
