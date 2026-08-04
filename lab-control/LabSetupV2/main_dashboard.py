"""
Battery Test Dashboard V2 — persistent Panel server.
Modern FastListTemplate dark theme with sidebar controls.

Key architecture: backend singleton outlives any browser session.
create_dashboard() is called fresh per connection — fixes tab-reopen bug.
"""
import os
import sys
from collections import deque
from io import StringIO
from pathlib import Path

import pandas as pd
import panel as pn
import plotly.graph_objs as go
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent))
from backend import LOG_ROOT, get_backend
from profile_runner_v2 import build_profile_panel

pn.extension("plotly", sizing_mode="stretch_width")

SIMULATE = False

_backend = get_backend(simulate=SIMULATE)

# ---------------------------------------------------------------------------
# Custom CSS — injected once at server level
# ---------------------------------------------------------------------------
_CSS = """
:host(.solid) .bk-btn {
    border-radius: 6px !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px !important;
}
.console-area textarea {
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace !important;
    font-size: 11.5px !important;
    line-height: 1.5 !important;
    background: #0d1117 !important;
    color: #c9d1d9 !important;
    border: 1px solid #30363d !important;
    border-radius: 6px !important;
}
.status-pill {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 13px;
}
.metric-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 6px;
}
"""

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def _get_log_file_options() -> dict:
    """Recurse into LOG_ROOT since logs now live under a per-battery
    subfolder (LOG_ROOT/<battery_id>/...), not flat in LOG_ROOT itself."""
    if not os.path.isdir(LOG_ROOT):
        return {}
    files = []
    for root, _dirs, filenames in os.walk(LOG_ROOT):
        for f in filenames:
            if f.lower().endswith(".csv"):
                files.append(os.path.join(root, f))
    if not files:
        return {}
    files.sort(key=os.path.getmtime, reverse=True)
    options = {}
    for p in files:
        from datetime import datetime
        mtime = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
        battery_dir = os.path.relpath(os.path.dirname(p), LOG_ROOT)
        label_prefix = f"[{battery_dir}] " if battery_dir != "." else ""
        options[f"{mtime}  {label_prefix}{os.path.basename(p)}"] = p
    return options


def _tail_csv_df(file_path: str, max_lines: int = 500):
    """Read a CSV's real header (always the file's first line) plus up to
    the last max_lines data rows. Reading the header separately from the
    tail matters once a log exceeds max_lines: a naive deque(f, maxlen=...)
    over the whole file evicts the header line itself, and whatever data
    row ends up first gets mistaken for the header instead."""
    try:
        with open(file_path, "r") as f:
            header = f.readline()
            if not header or "," not in header:
                return None
            header = header.strip()
            tail_lines = deque(f, maxlen=max_lines)
    except OSError:
        return None

    data = [ln.strip() for ln in tail_lines if ln.strip() and ln.strip() != header]
    if not data:
        return None

    try:
        return pd.read_csv(StringIO("\n".join([header] + data)))
    except Exception:
        return None


def _build_sensor_fig(backend) -> go.Figure | None:
    file_path = backend.sensor_log_path
    file_path_bdps = backend.bdps_log_path

    if not file_path or not os.path.exists(file_path):
        return None

    df = _tail_csv_df(file_path)
    if df is None:
        return None
    df.columns = ["timestamp", "voltage", "current", "temperature", "humidity"]

    df_plot = df.iloc[::5]   # downsample for speed

    colours = {
        "voltage":     "#bd93f9",
        "current":     "#50fa7b",
        "temperature": "#ffb86c",
        "humidity":    "#8be9fd",
        "V_bdps":      "#ff79c6",
        "I_bdps":      "#ff5555",
        "Mode":        "#f1fa8c",
    }

    fig = make_subplots(
        rows=4, cols=2,
        shared_xaxes=True,
        subplot_titles=(
            "Sensor Voltage (V)", "BDPS Voltage (V)",
            "Sensor Current (A)", "BDPS Current (A)",
            "Temperature (°C)", "BDPS Mode",
            "Humidity (%)", "",
        ),
        vertical_spacing=0.06,
        horizontal_spacing=0.08,
    )

    for row, col_name in enumerate(["voltage", "current", "temperature", "humidity"], start=1):
        fig.add_trace(
            go.Scatter(
                x=df_plot["timestamp"], y=df_plot[col_name],
                mode="lines", name=col_name,
                line=dict(color=colours[col_name], width=1.5),
            ),
            row=row, col=1,
        )

    if file_path_bdps and os.path.exists(file_path_bdps):
        df_b = _tail_csv_df(file_path_bdps)
        if df_b is not None:
            try:
                fig.add_trace(
                    go.Scatter(x=df_b["Timestamp"], y=df_b["Voltage"],
                               mode="lines", name="V_bdps",
                               line=dict(color=colours["V_bdps"], width=1.5)),
                    row=1, col=2,
                )
                fig.add_trace(
                    go.Scatter(x=df_b["Timestamp"], y=df_b["Current"],
                               mode="lines", name="I_bdps",
                               line=dict(color=colours["I_bdps"], width=1.5)),
                    row=2, col=2,
                )
                mode_num = df_b["Mode"].map({"CC": 0, "CV": 1})
                fig.add_trace(
                    go.Scatter(x=df_b["Timestamp"], y=mode_num,
                               mode="lines", name="Mode",
                               line=dict(color=colours["Mode"], width=1.5, dash="dot")),
                    row=3, col=2,
                )
            except Exception:
                pass

    fig.update_layout(
        height=820,
        showlegend=False,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9", size=11),
        margin=dict(l=50, r=20, t=40, b=40),
        xaxis4=dict(title="Timestamp"),
    )
    for i in range(1, 9):
        fig.update_xaxes(gridcolor="#21262d", zerolinecolor="#30363d", row=(i + 1) // 2, col=(i % 2) + 1)
        fig.update_yaxes(gridcolor="#21262d", zerolinecolor="#30363d", row=(i + 1) // 2, col=(i % 2) + 1)

    return fig


def _build_history_fig(path: str):
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return None, f"Could not read file: {e}"

    cols_lower = {c.lower(): c for c in df.columns}
    is_sensor = all(k in cols_lower for k in ["timestamp", "voltage", "current", "temperature"])
    is_bdps = all(k in cols_lower for k in ["timestamp", "voltage", "current", "mode"])

    colours = ["#bd93f9", "#50fa7b", "#ffb86c", "#8be9fd", "#ff79c6"]

    def _style(fig, height=800):
        fig.update_layout(
            height=height,
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=11),
            showlegend=False,
            margin=dict(l=50, r=20, t=40, b=40),
        )
        fig.update_xaxes(gridcolor="#21262d", zerolinecolor="#30363d")
        fig.update_yaxes(gridcolor="#21262d", zerolinecolor="#30363d")
        return fig

    if is_sensor:
        tcol, vcol, ccol, tccol = [cols_lower[k] for k in ["timestamp", "voltage", "current", "temperature"]]
        hcol = cols_lower.get("humidity")
        rows = 4 if hcol else 3
        titles = ["Voltage (V)", "Current (A)", "Temperature (°C)"]
        if hcol:
            titles.append("Humidity (%)")
        fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, subplot_titles=titles, vertical_spacing=0.06)
        for row, (col, c) in enumerate(zip([vcol, ccol, tccol], colours), start=1):
            fig.add_trace(go.Scatter(x=df[tcol], y=df[col], mode="lines",
                                     line=dict(color=c, width=1.2)), row=row, col=1)
        if hcol:
            fig.add_trace(go.Scatter(x=df[tcol], y=df[hcol], mode="lines",
                                     line=dict(color=colours[3], width=1.2)), row=4, col=1)
        info = f"Sensor log — **{len(df):,}** rows  |  `{os.path.basename(path)}`"
        return _style(fig), info

    if is_bdps:
        tcol, vcol, ccol, mcol = [cols_lower[k] for k in ["timestamp", "voltage", "current", "mode"]]
        fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                            subplot_titles=["Voltage (V)", "Current (A)", "Mode (0=CC, 1=CV)"],
                            vertical_spacing=0.08)
        fig.add_trace(go.Scatter(x=df[tcol], y=df[vcol], mode="lines",
                                 line=dict(color=colours[0], width=1.2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df[tcol], y=df[ccol], mode="lines",
                                 line=dict(color=colours[1], width=1.2)), row=2, col=1)
        mode_series = df[mcol].map({"CC": 0, "CV": 1}) if df[mcol].dtype == object else df[mcol]
        fig.add_trace(go.Scatter(x=df[tcol], y=mode_series, mode="lines",
                                 line=dict(color=colours[4], width=1.2, dash="dot")), row=3, col=1)
        info = f"BDPS log — **{len(df):,}** rows  |  `{os.path.basename(path)}`"
        return _style(fig, height=600), info

    numeric_cols = df.select_dtypes("number").columns.tolist()
    if numeric_cols:
        fig = go.Figure()
        for col, c in zip(numeric_cols[:4], colours):
            fig.add_trace(go.Scatter(y=df[col], mode="lines", name=col,
                                     line=dict(color=c, width=1.2)))
        fig.update_layout(height=600, showlegend=True)
        return _style(fig), f"Generic plot — `{os.path.basename(path)}`"

    return None, "No numeric columns to plot."


# ---------------------------------------------------------------------------
# Dashboard factory — fresh per browser session
# ---------------------------------------------------------------------------

def create_dashboard():
    backend = _backend

    # --- Console (placed full-width in main area below tabs) ---
    console_output = pn.widgets.TextAreaInput(
        value=backend.get_log(),
        height=260,
        disabled=True,
        css_classes=["console-area"],
        sizing_mode="stretch_width",
    )

    # --- Status / metric panes ---
    status_pane = pn.pane.HTML(
        '<span class="status-pill" style="background:#1a3a1a;color:#50fa7b;">● Idle</span>',
        sizing_mode="stretch_width",
    )
    capacity_pane = pn.pane.Markdown(
        "_No SOH measurement yet_",
        sizing_mode="stretch_width",
    )
    mode_pane = pn.pane.Markdown("**Mode:** idle", sizing_mode="stretch_width")

    # --- Active file location panes (shown in Live Data tab) ---
    sensor_file_pane = pn.pane.Markdown(
        "**Sensor log:** —", sizing_mode="stretch_width"
    )
    bdps_file_pane = pn.pane.Markdown(
        "**BDPS log:** —", sizing_mode="stretch_width"
    )

    # --- Buttons ---
    def _btn(label, btype, icon="", width=None):
        kw = dict(button_type=btype, sizing_mode="stretch_width")
        if width:
            kw = dict(button_type=btype, width=width)
        return pn.widgets.Button(name=f"{icon} {label}".strip(), **kw)

    stop_button        = _btn("Stop Test",              "danger",   "⏹")
    full_charge_button = _btn("Full Charge",             "success",  "⚡")
    c5_button          = _btn("Discharge C/5",           "warning",  "▼")
    c2_button          = _btn("Discharge C/2",           "warning",  "▼")
    degr_button        = _btn("10 Degradation Cycles",   "primary",  "🔁")
    sweep_button       = _btn("SoC Sweep — 10 runs",     "primary",  "📊")
    plan_button        = _btn("Full Plan (20 blocks)",   "primary",  "🚀")

    stop_button.on_click(lambda e: backend.stop_test())
    full_charge_button.on_click(lambda e: backend.start_full_charge())
    c5_button.on_click(lambda e: backend.start_discharge_c5())
    c2_button.on_click(lambda e: backend.start_discharge_c2())
    degr_button.on_click(lambda e: backend.start_degradation_cycles())
    sweep_button.on_click(lambda e: backend.start_soc_sweep())
    plan_button.on_click(lambda e: backend.start_full_plan(blocks=20))

    # --- Live sensor plot ---
    sensor_plot = pn.pane.Plotly(sizing_mode="stretch_width", height=820)
    refresh_btn = pn.widgets.Button(name="↺ Refresh Plot", button_type="light", width=140)

    def _push_sensor():
        fig = _build_sensor_fig(backend)
        if fig:
            sensor_plot.object = fig

    refresh_btn.on_click(lambda e: _push_sensor())

    # --- History ---
    history_selector = pn.widgets.Select(
        name="Log file", options={}, sizing_mode="stretch_width"
    )
    refresh_hist_btn = pn.widgets.Button(name="↺ Refresh List", button_type="light", width=140)
    history_info = pn.pane.Markdown("", sizing_mode="stretch_width")
    history_plot = pn.pane.Plotly(sizing_mode="stretch_width", height=800)

    def _load_history(path):
        if not path or not isinstance(path, str) or not os.path.exists(path):
            history_info.object = "File not found."
            history_plot.object = None
            return
        fig, info = _build_history_fig(path)
        history_info.object = info
        history_plot.object = fig

    def _on_hist_refresh(e):
        opts = _get_log_file_options()
        history_selector.options = opts if opts else {"No CSV files found": None}

    refresh_hist_btn.on_click(_on_hist_refresh)
    history_selector.param.watch(lambda e: _load_history(e.new), "value")

    # Populate on open
    opts = _get_log_file_options()
    if opts:
        history_selector.options = opts
        first = next(iter(opts.values()))
        history_selector.value = first
        _load_history(first)

    # --- Profile tab ---
    profile_panel = build_profile_panel(backend)

    # --- Periodic refresh (registered unconditionally — fixes tab-reopen bug) ---
    _log_cache = [""]

    def _refresh():
        new_log = backend.get_log()
        if new_log != _log_cache[0]:
            console_output.value = new_log
            _log_cache[0] = new_log

        running = backend.running_test
        if running:
            status_pane.object = (
                '<span class="status-pill" style="background:#3a1a1a;color:#ff5555;">● Running</span>'
            )
        else:
            status_pane.object = (
                '<span class="status-pill" style="background:#1a3a1a;color:#50fa7b;">● Idle</span>'
            )

        cap = backend.last_capacity_ah
        if cap is not None:
            capacity_pane.object = f"Last capacity: **{cap:.3f} Ah**"

        mode_pane.object = f"**Sensor mode:** {backend.sensor_reader.mode}"

        slog = backend.sensor_log_path or "—"
        blog = backend.bdps_log_path or "—"
        sensor_file_pane.object = f"**Sensor log:** `{slog}`"
        bdps_file_pane.object   = f"**BDPS log:** `{blog}`"

        _push_sensor()

    pn.state.add_periodic_callback(_refresh, period=2000)

    # ---------------------------------------------------------------------------
    # Layout
    # ---------------------------------------------------------------------------

    sidebar_items = [
        pn.Card(
            status_pane,
            pn.layout.Divider(margin=(4, 0)),
            capacity_pane,
            mode_pane,
            title="System Status",
            collapsed=False,
            sizing_mode="stretch_width",
        ),
        pn.Card(
            stop_button,
            pn.layout.Divider(margin=(6, 0)),
            pn.pane.Markdown("**Charge / Discharge**", margin=(0, 0, 4, 0)),
            full_charge_button,
            c5_button,
            c2_button,
            pn.layout.Divider(margin=(6, 0)),
            pn.pane.Markdown("**Test Sequences**", margin=(0, 0, 4, 0)),
            degr_button,
            sweep_button,
            plan_button,
            title="Controls",
            collapsed=False,
            sizing_mode="stretch_width",
        ),
    ]

    live_tab = pn.Column(
        pn.Row(
            pn.Column(sensor_file_pane, bdps_file_pane, sizing_mode="stretch_width"),
            refresh_btn,
            sizing_mode="stretch_width",
        ),
        sensor_plot,
        sizing_mode="stretch_width",
    )

    history_tab = pn.Column(
        pn.Row(history_selector, refresh_hist_btn, sizing_mode="stretch_width"),
        history_info,
        history_plot,
        sizing_mode="stretch_width",
    )

    main_tabs = pn.Tabs(
        ("📡  Live Data",   live_tab),
        ("📁  History",     history_tab),
        ("📋  Profile",     profile_panel),
        sizing_mode="stretch_width",
    )

    console_card = pn.Card(
        console_output,
        title="Console Log",
        collapsed=False,
        sizing_mode="stretch_width",
    )

    template = pn.template.FastListTemplate(
        title="Battery Degradation Test System",
        theme="dark",
        accent="#2ea043",
        sidebar_width=300,
        raw_css=[_CSS],
    )
    template.sidebar.extend(sidebar_items)
    template.main.extend([main_tabs, console_card])
    return template


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pn.serve(
        create_dashboard,
        show=False,
        port=39517,
        websocket_origin=[
            "localhost:39517",
            "127.0.0.1:39517",
            "batterytestpi:39517",
            "100.115.72.118:39517",
        ],
    )
