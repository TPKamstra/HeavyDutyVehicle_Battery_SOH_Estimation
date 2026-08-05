#!/usr/bin/env python3
"""
Boxer Battery — Motor-Start CAN Export Dashboard
7 CAN taps (can_tapper11/30/31/32/33/34/35), 826 engine starts total.

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

pn.extension("plotly")

DATASHEET_IR_MOHM = 1.6  # mΩ AC internal resistance per battery (new, datasheet) -- same pack as dataset_boxer

TAPPER_COLOURS = {
    "can_tapper11": "#1f77b4", "can_tapper30": "#EF553B", "can_tapper31": "#2ca02c",
    "can_tapper32": "#FF7F0E", "can_tapper33": "#AB63FA", "can_tapper34": "#00CC96",
    "can_tapper35": "#636EFA",
}

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


# ── Tab 2: Feature trends across all 826 starts, coloured by tapper ─────────

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
        title="Per-Tap Comparison — 7 CAN Taps",
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
## Boxer Motor-Start CAN Export — 7 Taps, {n_events} Starts

**Window per start:** 60s before .. 120s after (`tb60_ta120`) | **Commando markers:** Engine starting command, Glow plugs preheating, Engine running command
**Date range (all taps):** {t_min} -- {t_max}

Same pack as the original 41-start dataset (can_tapper11 IS that dataset — identical start-for-start).
This export adds 6 more taps (785 more starts) plus CAN commando event timing and a new
post-start voltage-stability feature (`post_start_V_std`), which is the field-side analogue of
`driving_aux_load_V_std` — the strongest SoC-robust indicator found in the lab campaign
(Section 5.1 of the paper). The 41-start dataset used in the paper's Results section 5.2 could not
test this, since it only ever captured crank-window features.

**Note on units:** `V_pre_total_1_10V` in features.csv is the *sum* of all four battery-group
voltages (~2x a single 12V-class group), not the vehicle's real CAN "Total Voltage" signal.
The new post-start feature instead uses the grid's per-group-average voltage, so it's on a
directly comparable scale to `V_pre_1_10V`/`V_min_1_10V` in features_packs.csv.

### Per-tap breakdown

| Tap | Starts | First start | Last start |
|---|---|---|---|
{tap_rows}
### Pack-level feature summary (all 826 starts)

| Metric | Min | Mean | Max |
|---|---|---|---|
| Voltage drop during crank (%) | {f["V_drop_total_pct"].min():.1f} | {f["V_drop_total_pct"].mean():.1f} | {f["V_drop_total_pct"].max():.1f} |
| Pack internal resistance (mΩ) | {f["R_int_mohm"].min():.1f} | {f["R_int_mohm"].mean():.1f} | {f["R_int_mohm"].max():.1f} |
| SoC at start (%) | {f["soc_pct_est_pack"].min():.0f} | {f["soc_pct_est_pack"].mean():.0f} | {f["soc_pct_est_pack"].max():.0f} |
| Crank duration, commando start→running (s) | {f["crank_duration_s"].min():.2f} | {f["crank_duration_s"].mean():.2f} | {f["crank_duration_s"].max():.2f} |
| Glow-plug lead time (s, +before start) | {f["glow_lead_s"].min():.2f} | {f["glow_lead_s"].mean():.2f} | {f["glow_lead_s"].max():.2f} |
| Post-start (15-115s) voltage std. dev. (V) | {f["post_start_V_std_V"].min():.3f} | {f["post_start_V_std_V"].mean():.3f} | {f["post_start_V_std_V"].max():.3f} |
    """
    return pn.pane.Markdown(md, width=1000, margin=(0, 20, 20, 20))


# ── Layout ────────────────────────────────────────────────────────────────────

tabs = pn.Tabs(
    ("Start Profile", pn.pane.Plotly(_build_start_fig(), config={"responsive": True})),
    ("Feature Trends", pn.pane.Plotly(_build_trends_fig(), config={"responsive": True})),
    ("Tap Comparison", pn.pane.Plotly(_build_tapper_fig(), config={"responsive": True})),
    ("SoC & Temp Effects", pn.pane.Plotly(_build_soc_temp_fig(), config={"responsive": True})),
    ("Post-Start Stability (new)", pn.pane.Plotly(_build_poststart_fig(), config={"responsive": True})),
)

template = pn.template.FastListTemplate(
    title="Boxer Battery — Motor-Start CAN Export (7 taps, 826 starts)",
    main=[_build_summary(), tabs],
    accent_base_color="#2C4F8C",
    header_background="#2C4F8C",
    header_color="#FFFFFF",
)

template.servable()

if __name__ == "__main__":
    pn.serve(template, show=True, port=5008)
