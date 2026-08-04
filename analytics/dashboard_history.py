import os
import glob

import pandas as pd
import panel as pn
from plotly.subplots import make_subplots
import plotly.graph_objects as go

pn.extension("plotly")


# Adjust path if needed
BASE_DIR = os.path.dirname(__file__)
LOG_DIR = os.path.join(BASE_DIR, "LOGBATTEST")


def list_log_files():
    """Return sorted CSV filenames starting with 'DegradationCycle'."""
    pattern = os.path.join(LOG_DIR, "*.csv")
    files = glob.glob(pattern)
    files = [os.path.basename(f) for f in files]
    return sorted(files)


# Widgets
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


# Plot panes (bigger for full HD)
bdps_plot_pane = pn.pane.Plotly(height=550, sizing_mode="stretch_width")
sensor_plot_pane = pn.pane.Plotly(height=550, sizing_mode="stretch_width")


def load_csv(filename: str) -> pd.DataFrame:
    if not filename:
        return pd.DataFrame()
    full_path = os.path.join(LOG_DIR, filename)
    if not os.path.exists(full_path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(full_path)
    except Exception as exc:
        print(f"Failed to read {full_path}: {exc}")
        return pd.DataFrame()
    return df


def detect_possible_xaxes(df: pd.DataFrame):
    """Return list of x axis candidates based on column names."""
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
    """Build Plotly figures given dataframe and selected x-axis."""
    if df.empty or x_col is None:
        return None, None

    # Attempt timestamp parsing if the user selected Timestamp
    if "time" in x_col.lower() or "timestamp" in x_col.lower():
        try:
            df[x_col] = pd.to_datetime(df[x_col])
        except Exception:
            pass

    cols_lower = {c.lower(): c for c in df.columns}

    v_col = cols_lower.get("voltage")
    i_col = cols_lower.get("current")
    t_col = cols_lower.get("temperature")
    h_col = cols_lower.get("humidity")

    # 1) Electrical figure
    fig_elec = make_subplots(specs=[[{"secondary_y": True}]])

    if v_col:
        fig_elec.add_trace(
            go.Scatter(
                x=df[x_col],
                y=df[v_col],
                name="Voltage [V]",
                mode="lines",
            ),
            secondary_y=False,
        )

    if i_col:
        fig_elec.add_trace(
            go.Scatter(
                x=df[x_col],
                y=df[i_col],
                name="Current [A]",
                mode="lines",
            ),
            secondary_y=True,
        )

    fig_elec.update_layout(
        title=f"Electrical behaviour ({x_col})",
        xaxis_title=x_col,
        legend=dict(orientation="h", y=1.1),
        margin=dict(l=40, r=40, t=70, b=40),
        height=550,
    )
    fig_elec.update_yaxes(title_text="Voltage [V]", secondary_y=False)
    fig_elec.update_yaxes(title_text="Current [A]", secondary_y=True)

    # 2) Environmental figure
    fig_env = make_subplots(specs=[[{"secondary_y": True}]])

    if t_col:
        fig_env.add_trace(
            go.Scatter(
                x=df[x_col],
                y=df[t_col],
                name="Temperature [°C]",
                mode="lines",
            ),
            secondary_y=False,
        )

    if h_col:
        fig_env.add_trace(
            go.Scatter(
                x=df[x_col],
                y=df[h_col],
                name="Humidity [%]",
                mode="lines",
            ),
            secondary_y=True,
        )

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
    """Set x axis dropdown options after loading a file."""
    axes = detect_possible_xaxes(df)
    xaxis_select.options = axes
    if axes:
        try:
            xaxis_select.value = axes[1]
        except IndexError:
            xaxis_select.value = axes[0]


@pn.depends(file_select, watch=True)
def update_after_file_selected(selected):
    df = load_csv(selected)
    update_axis_options(df)
    update_plots()


@pn.depends(xaxis_select, watch=True)
def update_plots(*events):
    """Update Plotly panes based on the selected x axis."""
    filename = file_select.value
    if not filename:
        return

    df = load_csv(filename)
    x_col = xaxis_select.value
    if not x_col:
        return

    fig_elec, fig_env = build_plots(df, x_col)
    bdps_plot_pane.object = fig_elec
    sensor_plot_pane.object = fig_env


def refresh_files(event):
    """Refresh file list, keeping only DegradationCycle*.csv."""
    options = list_log_files()
    file_select.options = options
    if file_select.value not in options:
        file_select.value = options[0] if options else None


refresh_button.on_click(refresh_files)


# Layout (bigger for full HD)
controls = pn.Row(
    file_select,
    refresh_button,
    xaxis_select,
    sizing_mode="stretch_width",
)

dashboard = pn.Column(
    pn.pane.Markdown("## Battery Log Visualisation Dashboard"),
    controls,
    bdps_plot_pane,
    sensor_plot_pane,
    sizing_mode="stretch_both",
)

dashboard.servable()


if __name__ == "__main__":
    pn.serve(dashboard, title="Battery Log Viewer", show=True)
