#!/usr/bin/env python3
"""
Boxer Battery — Engine Start Analysis Dashboard
EnerSys ArmaSafe Plus 12FV120, pack config: 2S2P (24 V nominal, 252 Ah)
"""
import os
import numpy as np
import pandas as pd
import panel as pn
import plotly.graph_objects as go
from plotly.subplots import make_subplots

pn.extension("plotly")

# ── Constants ─────────────────────────────────────────────────────────────────
DATASHEET_IR_MOHM = 1.6    # mΩ AC internal resistance per battery (new, datasheet)
PACK_NOM_V        = 24.0   # V nominal (2 batteries in series)
BATT_NOM_V        = 12.0   # V per battery
BATT_CAP_AH       = 126.0  # Ah per battery (C20 rated)
PACK_N_BATT       = 4      # 2 series × 2 parallel

GROUP_COLOURS = {
    "PG1_est": "#636EFA",
    "PG2":     "#EF553B",
    "PG3_est": "#00CC96",
    "PG4":     "#AB63FA",
}
GROUP_NOTE = {
    "PG1_est": "estimated (no direct sensor)",
    "PG2":     "measured (centre-tap sensor)",
    "PG3_est": "estimated (no direct sensor)",
    "PG4":     "measured (centre-tap sensor)",
}

BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "dataset_boxer", "start_data_battery", "my_start_csv",
)


# ── Data loading ──────────────────────────────────────────────────────────────

def _load():
    v  = pd.read_csv(os.path.join(BASE, "voltage_series.csv"))
    t  = pd.read_csv(os.path.join(BASE, "temperature_series.csv"))
    f  = pd.read_csv(os.path.join(BASE, "features.csv"),       parse_dates=["start_time"])
    fp = pd.read_csv(os.path.join(BASE, "features_packs.csv"), parse_dates=["start_time"])

    f["R_int_mohm"]  = f["R_internal_est_ohm_pack"] * 1000
    fp["R_int_mohm"] = fp["R_internal_est_ohm"]      * 1000
    fp["V_pre_V"]    = fp["V_pre_1_10V"] / 10
    fp["V_min_V"]    = fp["V_min_1_10V"] / 10
    return v, t, f, fp


V_SER, T_SER, FEAT, FEAT_PK = _load()


# ── Tab 1: Representative Engine Start ────────────────────────────────────────

def _build_start_fig():
    vc = V_SER.columns.tolist()
    tc = T_SER.columns.tolist()

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.10,
        subplot_titles=["Pack Voltage During Engine Crank", "Battery Temperature"],
        row_heights=[0.65, 0.35],
    )

    # Pack terminal voltage (24 V system)
    fig.add_trace(go.Scatter(
        x=V_SER[vc[0]], y=V_SER[vc[1]] / 10,
        mode="lines+markers", name="Total pack (24 V system)",
        line=dict(color="#1f77b4", width=2.5),
        marker=dict(size=5),
    ), row=1, col=1)

    # Per-string centre taps (12 V each)
    fig.add_trace(go.Scatter(
        x=V_SER[vc[2]], y=V_SER[vc[3]] / 10,
        mode="lines+markers", name="String PG2 (measured)",
        line=dict(color="#EF553B", width=1.5, dash="dot"),
        marker=dict(size=4),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=V_SER[vc[4]], y=V_SER[vc[5]] / 10,
        mode="lines+markers", name="String PG4 (measured)",
        line=dict(color="#00CC96", width=1.5, dash="dot"),
        marker=dict(size=4),
    ), row=1, col=1)

    # Annotations
    v_pre = V_SER[vc[1]].iloc[0] / 10
    v_min = V_SER[vc[1]].min()    / 10
    v_rec = V_SER[vc[1]].iloc[-1] / 10
    drop_pct = (v_pre - v_min) / v_pre * 100

    fig.add_hline(
        y=v_min, row=1, col=1,
        line=dict(color="red", dash="dash", width=1),
        annotation_text=f"V_min = {v_min:.1f} V  ({drop_pct:.1f}% drop)",
        annotation_position="top left",
    )
    fig.add_hline(
        y=PACK_NOM_V, row=1, col=1,
        line=dict(color="grey", dash="dot", width=1),
        annotation_text=f"Nominal {PACK_NOM_V:.0f} V",
        annotation_position="bottom right",
    )

    # Temperature
    fig.add_trace(go.Scatter(
        x=T_SER[tc[0]], y=T_SER[tc[1]] / 10,
        mode="lines+markers", name="DM Battery Temp",
        line=dict(color="#FF7F0E", width=2),
        marker=dict(size=4),
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=T_SER[tc[2]], y=T_SER[tc[3]] / 10,
        mode="lines+markers", name="Pool Temp",
        line=dict(color="#9467BD", width=2, dash="dot"),
        marker=dict(size=4),
    ), row=2, col=1)

    fig.update_xaxes(title_text="Time from crank initiation (s)", row=2, col=1)
    fig.update_yaxes(title_text="Voltage (V)", row=1, col=1)
    fig.update_yaxes(title_text="Temperature (°C)", row=2, col=1)
    fig.update_layout(
        height=580, template="plotly_white",
        title="Representative Engine Start — ArmaSafe 12FV120 Pack (2S2P, 24 V)",
        legend=dict(orientation="h", y=-0.18, x=0),
        margin=dict(t=80, b=80),
    )
    return fig


# ── Tab 2: Feature Trends over 41 Starts ─────────────────────────────────────

def _linreg_trace(x_dates, y, colour):
    x_num = (x_dates - x_dates.min()).dt.total_seconds().values
    mask  = ~np.isnan(y.astype(float))
    if mask.sum() < 3:
        return None
    p = np.polyfit(x_num[mask], y[mask], 1)
    return go.Scatter(
        x=x_dates, y=np.polyval(p, x_num),
        mode="lines", showlegend=False,
        line=dict(color=colour, dash="dash", width=1.5),
    )


def _build_trends_fig():
    f = FEAT.sort_values("start_time").copy()
    n = len(f)
    date_rng = f"({f['start_time'].dt.date.min()} – {f['start_time'].dt.date.max()})"

    fig = make_subplots(
        rows=2, cols=2,
        shared_xaxes=False,
        vertical_spacing=0.16, horizontal_spacing=0.12,
        subplot_titles=[
            "Voltage Drop During Crank (%)",
            "Estimated Pack Internal Resistance (mΩ)",
            "Estimated SoC at Start (%)",
            "Temperature at Start (°C)",
        ],
    )

    mk = dict(size=7, line=dict(width=1, color="white"))

    specs = [
        (1, 1, "V_drop_total_pct", "#1f77b4", "V_drop (%)"),
        (1, 2, "R_int_mohm",       "#EF553B", "R_int (mΩ)"),
        (2, 1, "soc_pct_est_pack", "#2ca02c", "SoC (%)"),
        (2, 2, "temp_avg_start_C", "#FF7F0E", "Temp (°C)"),
    ]
    for (r, c, col, clr, lbl) in specs:
        fig.add_trace(go.Scatter(
            x=f["start_time"], y=f[col],
            mode="markers", name=lbl, showlegend=False,
            marker=dict(**mk, color=clr),
        ), row=r, col=c)
        tr = _linreg_trace(f["start_time"], f[col].values, clr)
        if tr is not None:
            fig.add_trace(tr, row=r, col=c)
        fig.update_yaxes(title_text=lbl, row=r, col=c)
        fig.update_xaxes(title_text="Start time", row=r, col=c, tickangle=25)

    # Datasheet IR reference line
    fig.add_hline(
        y=DATASHEET_IR_MOHM, row=1, col=2,
        line=dict(color="black", dash="dot", width=1),
        annotation_text=f"Datasheet AC IR: {DATASHEET_IR_MOHM} mΩ",
        annotation_position="top right",
    )

    fig.update_layout(
        height=660, template="plotly_white",
        title=f"Engine Start Feature Trends — {n} events {date_rng}",
        margin=dict(t=80),
    )
    return fig


# ── Tab 3: Pack Group Comparison ─────────────────────────────────────────────

def _build_group_fig():
    fp     = FEAT_PK.copy()
    groups = ["PG1_est", "PG2", "PG3_est", "PG4"]

    fig = make_subplots(
        rows=1, cols=3,
        horizontal_spacing=0.10,
        subplot_titles=[
            "Voltage Drop per Group (%)",
            "Internal Resistance per Group (mΩ)",
            "Minimum Voltage per Group (V)",
        ],
    )

    for first, (r, c, ykey, ytitle) in enumerate([
        (1, 1, "V_drop_pct",  "V_drop (%)"),
        (1, 2, "R_int_mohm",  "R_int (mΩ)"),
        (1, 3, "V_min_V",     "V_min (V)"),
    ]):
        for g in groups:
            gdf = fp[fp["group"] == g]
            fig.add_trace(go.Box(
                y=gdf[ykey], name=g,
                marker_color=GROUP_COLOURS[g],
                boxpoints="all", jitter=0.35, pointpos=-1.6,
                legendgroup=g,
                showlegend=(c == 1),
            ), row=r, col=c)
        fig.update_yaxes(title_text=ytitle, row=r, col=c)

    # Datasheet reference on R_int panel
    fig.add_hline(
        y=DATASHEET_IR_MOHM, row=1, col=2,
        line=dict(color="black", dash="dot", width=1),
        annotation_text=f"Datasheet AC IR: {DATASHEET_IR_MOHM} mΩ",
        annotation_position="top right",
    )

    fig.update_layout(
        height=500, template="plotly_white",
        title="Pack Group Comparison — All Engine Starts  (PG2 & PG4 measured; PG1 & PG3 estimated)",
        legend=dict(orientation="h", y=-0.22, x=0.2),
        margin=dict(t=80, b=100),
    )
    return fig


# ── Tab 4: SoC & Temperature Effects ─────────────────────────────────────────

def _build_soc_temp_fig():
    f = FEAT.copy()

    fig = make_subplots(
        rows=1, cols=2,
        horizontal_spacing=0.14,
        subplot_titles=[
            "Voltage Drop vs SoC at Start",
            "Internal Resistance vs SoC at Start",
        ],
    )

    common_marker = dict(
        size=9,
        color=f["temp_avg_start_C"],
        colorscale="RdYlBu_r",
        line=dict(width=1, color="white"),
    )

    fig.add_trace(go.Scatter(
        x=f["soc_pct_est_pack"], y=f["V_drop_total_pct"],
        mode="markers", showlegend=False,
        marker=dict(**common_marker, showscale=True,
                    colorbar=dict(title="Temp<br>(°C)", thickness=14, x=0.44)),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=f["soc_pct_est_pack"], y=f["R_int_mohm"],
        mode="markers", showlegend=False,
        marker=dict(**common_marker, showscale=True,
                    colorbar=dict(title="Temp<br>(°C)", thickness=14, x=1.01)),
    ), row=1, col=2)

    for c in [1, 2]:
        fig.update_xaxes(title_text="SoC at start (%)", row=1, col=c)
    fig.update_yaxes(title_text="Voltage drop (%)", row=1, col=1)
    fig.update_yaxes(title_text="R_internal (mΩ)", row=1, col=2)

    fig.update_layout(
        height=460, template="plotly_white",
        title="SoC & Temperature Effect on Engine Start Performance  (colour = battery temperature)",
        margin=dict(t=80),
    )
    return fig


# ── Summary pane ──────────────────────────────────────────────────────────────

def _build_summary():
    f  = FEAT
    fp = FEAT_PK

    # Per-group R_int summary
    gr_rows = ""
    for g in ["PG1_est", "PG2", "PG3_est", "PG4"]:
        sub = fp[fp["group"] == g]["R_int_mohm"]
        gr_rows += (
            f"| {g} ({GROUP_NOTE[g]}) "
            f"| {sub.min():.1f} | {sub.mean():.1f} | {sub.max():.1f} |\n"
        )

    n_events  = len(f)
    t_min     = f["start_time"].dt.date.min()
    t_max     = f["start_time"].dt.date.max()
    v_pre_avg = fp["V_pre_V"].mean()
    v_min_avg = fp["V_min_V"].mean()

    md = f"""
## EnerSys ArmaSafe Plus 12FV120 — Boxer Engine Start Dataset

**Pack configuration:** 2 batteries in series × 2 strings in parallel (2S2P)
**Pack voltage:** {PACK_NOM_V:.0f} V nominal | **Pack capacity:** {BATT_CAP_AH * 2:.0f} Ah (C20)
**Individual battery:** {BATT_NOM_V:.0f} V / {BATT_CAP_AH:.0f} Ah | Datasheet AC internal resistance: {DATASHEET_IR_MOHM} mΩ
**Dataset:** {n_events} engine starts recorded between {t_min} and {t_max}

---

### Pack-level start features

| Metric | Min | Mean | Max |
|---|---|---|---|
| Voltage drop during crank (%) | {f["V_drop_total_pct"].min():.1f} | {f["V_drop_total_pct"].mean():.1f} | {f["V_drop_total_pct"].max():.1f} |
| Pack internal resistance (mΩ) | {f["R_int_mohm"].min():.1f} | {f["R_int_mohm"].mean():.1f} | {f["R_int_mohm"].max():.1f} |
| SoC at start (%) | {f["soc_pct_est_pack"].min():.0f} | {f["soc_pct_est_pack"].mean():.0f} | {f["soc_pct_est_pack"].max():.0f} |
| Temperature at start (°C) | {f["temp_avg_start_C"].min():.1f} | {f["temp_avg_start_C"].mean():.1f} | {f["temp_avg_start_C"].max():.1f} |
| Recovery time (s) | {f["t_recovery_s_max"].min():.2f} | {f["t_recovery_s_max"].mean():.2f} | {f["t_recovery_s_max"].max():.2f} |
| Mean battery voltage pre-start (V) | {fp["V_pre_V"].min():.2f} | {fp["V_pre_V"].mean():.2f} | {fp["V_pre_V"].max():.2f} |
| Mean battery voltage at V_min (V) | {fp["V_min_V"].min():.2f} | {fp["V_min_V"].mean():.2f} | {fp["V_min_V"].max():.2f} |

### Internal resistance per battery group (mΩ)

| Group | Min | Mean | Max |
|---|---|---|---|
{gr_rows}
*Note: PG1 & PG3 resistances are estimated from the pack-level measurement; PG2 & PG4 have direct centre-tap voltage sensors.*
    """
    return pn.pane.Markdown(md, sizing_mode="stretch_width", margin=(0, 20, 20, 20))


# ── Layout ────────────────────────────────────────────────────────────────────

tabs = pn.Tabs(
    ("Start Profile",       pn.pane.Plotly(_build_start_fig(),    config={"responsive": True}, sizing_mode="stretch_width")),
    ("Feature Trends",      pn.pane.Plotly(_build_trends_fig(),   config={"responsive": True}, sizing_mode="stretch_width")),
    ("Group Comparison",    pn.pane.Plotly(_build_group_fig(),    config={"responsive": True}, sizing_mode="stretch_width")),
    ("SoC & Temp Effects",  pn.pane.Plotly(_build_soc_temp_fig(), config={"responsive": True}, sizing_mode="stretch_width")),
    sizing_mode="stretch_width",
)

template = pn.template.FastListTemplate(
    title="Boxer Battery — Engine Start Analysis",
    main=[_build_summary(), tabs],
    accent_base_color="#2C4F8C",
    header_background="#2C4F8C",
    header_color="#FFFFFF",
    main_max_width="100%",
)

template.servable()

if __name__ == "__main__":
    pn.serve(template, show=True, port=5007)
