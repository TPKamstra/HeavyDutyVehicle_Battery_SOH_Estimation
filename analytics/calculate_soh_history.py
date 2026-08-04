import os
import glob
import re

import numpy as np
import pandas as pd
import panel as pn
from plotly.subplots import make_subplots
import plotly.graph_objects as go

pn.extension("plotly", "tabulator")

# Adjust path if needed
BASE_DIR = os.path.dirname(__file__)
LOG_DIR = os.path.join(BASE_DIR, "LOGBATTEST_Complete")

# -----------------------
# Filter: only after December 2025
# -----------------------
# Interpretation: strictly after 2025-12-31 23:59:59, so from 2026-01-01 00:00:00 onward.
CUTOFF_TS = pd.Timestamp("2025-12-01 00:00:00")

# -----------------------
# Filename pattern (C/2 + C/5)
# -----------------------
# discharge_c5_YYYY-MM-DD_HH-MM-SS_bdps.csv
# discharge_c2_YYYY-MM-DD_HH-MM-SS_bdps.csv
C_PATTERN = re.compile(
    r"^discharge_c(?P<rate>(2|5))_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})_bdps\.csv$",
    flags=re.IGNORECASE,
)

# -----------------------
# Content gates
# -----------------------
MIN_BYTES = 200
MIN_ROWS = 10
REQUIRED_COLS = {"current"}


def list_log_files():
    """Return sorted CSV filenames in LOG_DIR."""
    pattern = os.path.join(LOG_DIR, "*.csv")
    files = glob.glob(pattern)
    files = [os.path.basename(f) for f in files]
    return sorted(files)


def _cols_lower_set(df: pd.DataFrame) -> set:
    return {c.lower() for c in df.columns}


def has_time_axis(df: pd.DataFrame) -> bool:
    cols = _cols_lower_set(df)
    return ("elapsed_s" in cols) or ("timestamp" in cols) or ("time" in cols)


def file_has_content(full_path: str) -> bool:
    try:
        if os.path.getsize(full_path) < MIN_BYTES:
            return False
    except OSError:
        return False

    try:
        df = pd.read_csv(full_path)
    except Exception:
        return False

    if df is None or df.empty or len(df) < MIN_ROWS:
        return False

    cols_lower = _cols_lower_set(df)
    if not REQUIRED_COLS.issubset(cols_lower):
        return False

    if not has_time_axis(df):
        return False

    return True


def parse_filename_datetime_and_rate(filename: str):
    """
    Extract datetime and rate from:
      discharge_c{2|5}_YYYY-MM-DD_HH-MM-SS_bdps.csv
    Returns: (pd.Timestamp, "C/2" or "C/5")
    """
    m = C_PATTERN.match(filename)
    if not m:
        return pd.NaT, None

    date_part = m.group("date")
    time_part = m.group("time").replace("-", ":")
    rate_num = m.group("rate")
    ts = pd.to_datetime(f"{date_part} {time_part}", errors="coerce")
    return ts, f"C/{rate_num}"


def list_c_bdps_files():
    """
    Return sorted filenames matching discharge_c2/c5_*_bdps.csv,
    passing content gates, and with filename datetime >= CUTOFF_TS.
    """
    files = []
    for fn in list_log_files():
        if not C_PATTERN.match(fn):
            continue

        ts, _ = parse_filename_datetime_and_rate(fn)
        if pd.isna(ts) or ts < CUTOFF_TS:
            continue

        full_path = os.path.join(LOG_DIR, fn)
        if file_has_content(full_path):
            files.append(fn)

    return sorted(files)


# -----------------------
# Widgets
# -----------------------
file_select = pn.widgets.Select(
    name="Select log file",
    options=list_log_files(),
    width=350,
)

refresh_button = pn.widgets.Button(
    name="Refresh list",
    button_type="primary",
    width=150,
)

xaxis_select = pn.widgets.Select(
    name="X axis",
    options=[],
    width=200,
)

compute_button = pn.widgets.Button(
    name="Compute SOH (C/2 + C/5, after Dec 2025)",
    button_type="success",
    width=380,
)

rate_select = pn.widgets.Select(
    name="Use for SOH",
    options=["C/5 only", "C/2 only", "Both (compute SOH per-rate)"],
    value="C/5 only",
    width=260,
)

soh_baseline_select = pn.widgets.Select(
    name="SOH baseline",
    options=["First file", "Nominal 18 Ah"],
    value="First file",
    width=200,
)

nominal_capacity_ah = pn.widgets.FloatInput(
    name="Nominal capacity [Ah]",
    value=18.0,
    step=0.1,
    width=180,
)

# Plot panes
bdps_plot_pane = pn.pane.Plotly(height=550, sizing_mode="stretch_width")
sensor_plot_pane = pn.pane.Plotly(height=550, sizing_mode="stretch_width")
soh_plot_pane = pn.pane.Plotly(height=420, sizing_mode="stretch_width")

stats_table = pn.widgets.Tabulator(
    pd.DataFrame(),
    pagination="remote",
    page_size=25,
    height=260,
    sizing_mode="stretch_width",
)


# Helpers
def _safe_stem(fn: str) -> str:
    """
    Turn 'discharge_c5_2025-12-16_14-05-57_bdps.csv'
    into a safe image filename stem.
    """
    if not fn:
        return "plot"
    stem = os.path.splitext(os.path.basename(fn))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")
    return stem or "plot"


def set_download_filename_for_panes(filename: str):
    stem = _safe_stem(filename)

    # one config dict reused for both figures
    cfg = {
        "displaylogo": False,
        "responsive": True,
        "toImageButtonOptions": {
            "format": "png",
            "filename": stem,     # this becomes "<filename>.png"
            "height": 1200,       # optional, higher resolution
            "width": 2000,
            "scale": 2,           # optional, sharper text/lines
        },
    }

    bdps_plot_pane.config = cfg
    sensor_plot_pane.config = cfg
    soh_plot_pane.config = {**cfg, "toImageButtonOptions": {**cfg["toImageButtonOptions"], "filename": f"{stem}_soh"}}



# -----------------------
# IO + viewer
# -----------------------
def load_csv(filename: str) -> pd.DataFrame:
    if not filename:
        return pd.DataFrame()

    full_path = os.path.join(LOG_DIR, filename)
    if not os.path.exists(full_path):
        return pd.DataFrame()

    # Keep viewer robust
    if not file_has_content(full_path):
        return pd.DataFrame()

    try:
        df = pd.read_csv(full_path)
    except Exception as exc:
        print(f"Failed to read {full_path}: {exc}")
        return pd.DataFrame()

    return df


def detect_possible_xaxes(df: pd.DataFrame):
    cols_lower = {c.lower(): c for c in df.columns}
    candidates = []
    if "elapsed_s" in cols_lower:
        candidates.append(cols_lower["elapsed_s"])
    if "timestamp" in cols_lower:
        candidates.append(cols_lower["timestamp"])
    if "time" in cols_lower:
        candidates.append(cols_lower["time"])
    return candidates


def build_plots(df: pd.DataFrame, x_col: str):
    if df.empty or x_col is None:
        return None, None

    if "time" in x_col.lower() or "timestamp" in x_col.lower():
        try:
            df = df.copy()
            df[x_col] = pd.to_datetime(df[x_col], errors="coerce")
        except Exception:
            pass

    cols_lower = {c.lower(): c for c in df.columns}
    v_col = cols_lower.get("voltage")
    i_col = cols_lower.get("current")
    t_col = cols_lower.get("temperature")
    h_col = cols_lower.get("humidity")

    fig_elec = make_subplots(specs=[[{"secondary_y": True}]])
    if v_col:
        fig_elec.add_trace(go.Scatter(x=df[x_col], y=df[v_col], name="Voltage [V]", mode="lines"), secondary_y=False)
    if i_col:
        fig_elec.add_trace(go.Scatter(x=df[x_col], y=df[i_col], name="Current [A]", mode="lines"), secondary_y=True)

    fig_elec.update_layout(
        title=f"Electrical behaviour ({x_col})",
        xaxis_title=x_col,
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=40, r=40, t=70, b=40),
        height=550,
    )
    fig_elec.update_yaxes(title_text="Voltage [V]", secondary_y=False)
    fig_elec.update_yaxes(title_text="Current [A]", secondary_y=True)

    fig_env = make_subplots(specs=[[{"secondary_y": True}]])
    if t_col:
        fig_env.add_trace(go.Scatter(x=df[x_col], y=df[t_col], name="Temperature [°C]", mode="lines"), secondary_y=False)
    if h_col:
        fig_env.add_trace(go.Scatter(x=df[x_col], y=df[h_col], name="Humidity [%]", mode="lines"), secondary_y=True)

    fig_env.update_layout(
        title=f"Environmental behaviour ({x_col})",
        xaxis_title=x_col,
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=40, r=40, t=70, b=40),
        height=550,
    )
    fig_env.update_yaxes(title_text="Temperature [°C]", secondary_y=False)
    fig_env.update_yaxes(title_text="Humidity [%]", secondary_y=True)

    return fig_elec, fig_env


def update_axis_options(df: pd.DataFrame):
    axes = detect_possible_xaxes(df)
    xaxis_select.options = axes
    if axes:
        xaxis_select.value = axes[0]


@pn.depends(file_select, watch=True)
def update_after_file_selected(selected):
    df = load_csv(selected)
    update_axis_options(df)
    update_plots()


@pn.depends(xaxis_select, watch=True)
def update_plots(*events):
    filename = file_select.value
    if not filename:
        return

    df = load_csv(filename)
    x_col = xaxis_select.value
    if df.empty or not x_col:
        bdps_plot_pane.object = None
        sensor_plot_pane.object = None
        return

    # set camera-download filename based on selected file
    set_download_filename_for_panes(filename)

    fig_elec, fig_env = build_plots(df, x_col)
    bdps_plot_pane.object = fig_elec
    sensor_plot_pane.object = fig_env


def refresh_files(event):
    options = list_log_files()
    file_select.options = options
    if file_select.value not in options:
        file_select.value = options[0] if options else None


refresh_button.on_click(refresh_files)


# -----------------------
# SOH computation
# -----------------------
def _get_col(df: pd.DataFrame, name: str):
    cols_lower = {c.lower(): c for c in df.columns}
    return cols_lower.get(name.lower())


def _compute_dt_seconds(df: pd.DataFrame) -> np.ndarray:
    if df.empty:
        return np.array([])

    elapsed = _get_col(df, "elapsed_s")
    if elapsed:
        x = pd.to_numeric(df[elapsed], errors="coerce").to_numpy(dtype=float)
        dt = np.diff(x, prepend=x[0])
        dt[0] = np.nan
        return dt

    ts = _get_col(df, "timestamp") or _get_col(df, "time")
    if ts:
        t = pd.to_datetime(df[ts], errors="coerce")
        return t.diff().dt.total_seconds().to_numpy(dtype=float)

    return np.array([])


def _infer_discharge_mask(i: np.ndarray, threshold_a: float = 0.2) -> np.ndarray:
    i = i.astype(float)
    i_valid = i[np.isfinite(i)]
    if i_valid.size == 0:
        return np.zeros_like(i, dtype=bool)

    neg_frac = np.mean(i_valid < -threshold_a)
    pos_frac = np.mean(i_valid > threshold_a)

    if neg_frac >= pos_frac:
        return i < -threshold_a
    return i > threshold_a


def compute_discharge_capacity_ah(df: pd.DataFrame) -> dict:
    i_col = _get_col(df, "current")
    if not i_col:
        return {"capacity_ah": np.nan, "discharge_seconds": np.nan, "i_mean_discharge": np.nan}

    i = pd.to_numeric(df[i_col], errors="coerce").to_numpy(dtype=float)
    dt = _compute_dt_seconds(df)

    if dt.size == 0 or i.size != dt.size:
        return {"capacity_ah": np.nan, "discharge_seconds": np.nan, "i_mean_discharge": np.nan}

    dt = np.where(np.isfinite(dt) & (dt >= 0) & (dt <= 10.0), dt, np.nan)

    discharge_mask = _infer_discharge_mask(i, threshold_a=0.2) & np.isfinite(i) & np.isfinite(dt)
    if not np.any(discharge_mask):
        return {"capacity_ah": np.nan, "discharge_seconds": np.nan, "i_mean_discharge": np.nan}

    ah_signed = np.nansum(i[discharge_mask] * dt[discharge_mask]) / 3600.0
    capacity_ah = float(abs(ah_signed))

    discharge_seconds = float(np.nansum(dt[discharge_mask]))
    i_mean = float(np.nanmean(i[discharge_mask]))

    return {"capacity_ah": capacity_ah, "discharge_seconds": discharge_seconds, "i_mean_discharge": i_mean}


def compute_stats_for_c_files() -> pd.DataFrame:
    files = list_c_bdps_files()
    rows = []

    for fn in files:
        df = load_csv(fn)
        if df.empty:
            continue

        cap = compute_discharge_capacity_ah(df)

        t_col = _get_col(df, "temperature")
        temp_mean = float(pd.to_numeric(df[t_col], errors="coerce").mean()) if t_col else np.nan

        v_col = _get_col(df, "voltage")
        v_min = float(pd.to_numeric(df[v_col], errors="coerce").min()) if v_col else np.nan

        ts, rate = parse_filename_datetime_and_rate(fn)

        rows.append(
            {
                "file": fn,
                "start_datetime": ts,
                "rate": rate,
                "capacity_ah": cap["capacity_ah"],
                "discharge_h": cap["discharge_seconds"] / 3600.0 if np.isfinite(cap["discharge_seconds"]) else np.nan,
                "i_mean_discharge": cap["i_mean_discharge"],
                "temp_mean_c": temp_mean,
                "v_min": v_min,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out = out.sort_values(["start_datetime", "file"], kind="stable").reset_index(drop=True)

    use = rate_select.value
    if use == "C/5 only":
        mask_use = out["rate"] == "C/5"
    elif use == "C/2 only":
        mask_use = out["rate"] == "C/2"
    else:
        mask_use = out["rate"].isin(["C/2", "C/5"])

    out["use_for_soh"] = mask_use

    if soh_baseline_select.value == "Nominal 18 Ah":
        baseline_nom = float(nominal_capacity_ah.value)
        out["soh"] = np.where(out["use_for_soh"], out["capacity_ah"] / baseline_nom, np.nan)
        return out

    if use == "Both (compute SOH per-rate)":
        out["soh"] = np.nan
        for r in ["C/2", "C/5"]:
            m = out["use_for_soh"] & (out["rate"] == r) & out["capacity_ah"].notna()
            if not m.any():
                continue
            baseline = float(out.loc[m, "capacity_ah"].iloc[0])
            out.loc[m, "soh"] = out.loc[m, "capacity_ah"] / baseline
    else:
        m = out["use_for_soh"] & out["capacity_ah"].notna()
        baseline = float(out.loc[m, "capacity_ah"].iloc[0]) if m.any() else float("nan")
        out["soh"] = np.where(out["use_for_soh"], out["capacity_ah"] / baseline, np.nan)
        # -----------------------
    # Cycle axis (each datapoint = +10 cycles)
    # -----------------------
    # Per-rate cycle counter (recommended, avoids mixing C/2 and C/5 timelines)
    out["soh_point_idx"] = np.nan
    for r in ["C/2", "C/5"]:
        m = out["use_for_soh"] & (out["rate"] == r) & out["soh"].notna()
        if not m.any():
            continue
        out.loc[m, "soh_point_idx"] = np.arange(1, int(m.sum()) + 1)

    out["cycles"] = out["soh_point_idx"] * 10
    return out


def build_soh_plot(stats_df: pd.DataFrame):
    fig = go.Figure()
    if stats_df.empty:
        return fig.update_layout(title="SOH trend (no matching C/2 or C/5 files found after Dec 2025)")

    df = stats_df[stats_df["use_for_soh"] & stats_df["soh"].notna()].copy()
    if df.empty:
        return fig.update_layout(title="SOH trend (no usable rows for selected rate)")

    for rate, g in df.groupby("rate"):
        g = g.sort_values("start_datetime", kind="stable")
        fig.add_trace(
            go.Scatter(
                x=g["cycles"],
                y=g["soh"],
                mode="lines+markers",
                name=f"SOH {rate}",
                hovertext=g["file"],
            )
        )

    fig.update_layout(
        title="SOH trend versus cycle count (each point = +10 cycles)",
        xaxis_title="Cycles",
        yaxis_title="SOH (ratio)",
        margin=dict(l=40, r=40, t=60, b=40),
        height=420,
        legend=dict(orientation="h", y=1.12),
    )
    return fig

def on_compute_clicked(event):
    df_stats = compute_stats_for_c_files()
    stats_table.value = df_stats
    soh_plot_pane.object = build_soh_plot(df_stats)


compute_button.on_click(on_compute_clicked)


# -----------------------
# Layout
# -----------------------
controls = pn.Row(
    file_select,
    refresh_button,
    xaxis_select,
    sizing_mode="stretch_width",
)

stats_controls = pn.Row(
    compute_button,
    rate_select,
    soh_baseline_select,
    nominal_capacity_ah,
    sizing_mode="stretch_width",
)

viewer_tab = pn.Column(
    pn.pane.Markdown("## Battery Log Visualisation Dashboard"),
    controls,
    bdps_plot_pane,
    sensor_plot_pane,
    sizing_mode="stretch_both",
)

stats_tab = pn.Column(
    pn.pane.Markdown("## SOH from C/2 and C/5 discharge files"),
    pn.pane.Markdown(
        "SOH uses only filenames matching `discharge_c2/c5_YYYY-MM-DD_HH-MM-SS_bdps.csv`, "
        "and only those with datetime **from 2026-01-01 onward**."
    ),
    stats_controls,
    stats_table,
    soh_plot_pane,
    sizing_mode="stretch_both",
)

dashboard = pn.Tabs(
    ("Viewer", viewer_tab),
    ("SOH Stats", stats_tab),
    sizing_mode="stretch_both",
)

dashboard.servable()

if __name__ == "__main__":
    pn.serve(dashboard, title="Battery Log Viewer", show=True)