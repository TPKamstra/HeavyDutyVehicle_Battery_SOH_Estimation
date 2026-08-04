"""
Profile preview panel for the Custom Profile tab.
The profile is auto-loaded from test_day_profile.csv by the backend at startup.
This module only handles the UI: preview plot, reload button, single-run button.
"""
import panel as pn
from plotly.subplots import make_subplots
import plotly.graph_objs as go


def build_profile_panel(backend) -> pn.Column:
    """Return the Panel layout for the Test-Day Profile tab."""

    profile_info = pn.pane.Markdown(_profile_summary(backend))
    preview_pane = pn.pane.Plotly(height=400, width=900, object=_make_plot(backend))

    reload_button = pn.widgets.Button(
        name="Reload profile from file", button_type="primary"
    )
    run_once_button = pn.widgets.Button(
        name="Run profile once (at current SoC)",
        button_type="success",
        disabled=backend.profile_df is None,
    )

    def on_reload(event):
        backend._load_profile()
        profile_info.object = _profile_summary(backend)
        preview_pane.object = _make_plot(backend)
        run_once_button.disabled = backend.profile_df is None

    def on_run_once(event):
        backend.start_single_profile_run()

    reload_button.on_click(on_reload)
    run_once_button.on_click(on_run_once)

    return pn.Column(
        "# Test-Day Profile",
        pn.pane.Markdown(
            f"The profile is loaded automatically from `{backend.profile_path.name}` "
            "at server startup. Replace that file (same name) and click **Reload** to "
            "update it without restarting the server.\n\n"
            "Columns required: `Time (s)`, `Current (A)`. Optional: a voltage column "
            "(`Voltage (V)` or `V_expected_sim (V)`, cosmetic preview only — never a "
            "setpoint or measurement) and `Event Type` / `Event Index` (logged alongside "
            "measured V/I on every run).  \n"
            "Negative current = discharge (sink); positive = charge (source)."
        ),
        pn.Row(reload_button, run_once_button),
        profile_info,
        preview_pane,
    )


def _profile_summary(backend) -> str:
    df = backend.profile_df
    if df is None:
        return f"**Status:** No profile loaded. Place `{backend.profile_path.name}` in `LabSetupV2/`."
    duration = df["Time_s"].iloc[-1] - df["Time_s"].iloc[0]
    i_min = df["Current_A"].min()
    i_max = df["Current_A"].max()
    hash_str = (backend.profile_hash or "")[:12]
    events = ""
    if "Event_Type" in df.columns and (df["Event_Type"] != "").any():
        counts = df.drop_duplicates("Event_Index")["Event_Type"].value_counts()
        events = "  \nEvents: " + ", ".join(f"{k} ×{v}" for k, v in counts.items())
    return (
        f"**Status:** Loaded from `{backend.profile_path.name}` — {len(df)} points, "
        f"duration **{duration:.0f} s** ({duration / 60:.1f} min)  \n"
        f"Current range: **{i_min:.2f} A** to **{i_max:.2f} A**  \n"
        f"Hash: `{hash_str}`{events}"
    )


def _make_plot(backend):
    df = backend.profile_df
    if df is None:
        return None

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        subplot_titles=("Profile Voltage (V)", "Profile Current (A)"),
    )
    fig.add_trace(
        go.Scatter(x=df["Time_s"], y=df["Voltage_V"], mode="lines", name="Voltage"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df["Time_s"], y=df["Current_A"], mode="lines", name="Current"),
        row=2, col=1,
    )
    fig.update_layout(
        height=400, width=900,
        showlegend=False,
        margin=dict(l=40, r=40, t=30, b=40),
        xaxis2=dict(title="Time (s)"),
    )
    return fig
