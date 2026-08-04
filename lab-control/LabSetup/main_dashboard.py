import panel as pn
from plotly.subplots import make_subplots
import plotly.graph_objs as go
import pandas as pd
import threading
from datetime import datetime
from Classes import functions_class as cl
from Classes import sensor_reader
from profile_runner import ProfileRunner
import os
import time
from collections import deque
from io import StringIO
from dataclasses import dataclass
from typing import List, Optional



pn.extension("plotly")

step_plan_df = pd.read_csv("detailed_battery_test_plan.csv")
LOG_ROOT = "/media/pi/LOGBATTEST"
os.makedirs(LOG_ROOT, exist_ok=True)

class BatteryTestDashboard:
    def __init__(self, bdps_ip="192.168.2.130", simulate=False):
        # BDPS
        self.bdps = cl.bdps_control(bdps_ip) if not simulate else None
        if self.bdps:
            self.bdps.connect()
        else:
            print("Simulated BDPS connected.")

        # Sensor reader
        self.sensor_reader = sensor_reader.SensorReader(
            simulate=simulate,
            port="/dev/ttyACM0",
            log_path="live_sensor_output.csv"
        )
        self.sensor_reader.start()
        self.sensor_reader.set_mode("idle")

        # State
        self.running_test = False
        self.sensor_log_path = self.sensor_reader.log_path
        self.bdps_log_path = None
        self.current_cycle = None

        # New global stop flag and thread handle
        self.stop_event = threading.Event()
        self.test_thread = None
        self.last_capacity_Ah = None

        # ---------- Control widgets ----------
        self.test_selector = pn.widgets.Select(
            name="Select Test Cycle",
            options=sorted(step_plan_df["cycle"].unique().tolist())
        )
        self.stop_button = pn.widgets.Button(
            name="Stop Test",
            button_type="danger"
        )
        self.full_charge_button = pn.widgets.Button(
            name="Full Charge",
            button_type="success"
        )
        self.discharge_c5_button = pn.widgets.Button(
            name="Discharge C/5",
            button_type="warning"
        )
        self.discharge_c2_button = pn.widgets.Button(
            name="Discharge C/2",
            button_type="warning"
        )

        self.degradation_cycles_button = pn.widgets.Button(
            name="Run 10 degradation cycles",
            button_type="danger",
            width=250,
        )
        self.run_full_plan_button = pn.widgets.Button(
            name="Run Full Plan (Degradation + SOH + Test Day)",
            button_type="primary",
            width=350,
        )


        self.console_output = pn.widgets.TextAreaInput(
            name="Console",
            value="",
            height=200,
            width=600
        )

        # ---------- Sensor widgets ----------
        self.sensor_status = pn.pane.Markdown("**Sensor Mode:** idle")
        self.sensor_plot = pn.pane.Plotly(height=800, width=1000)
        self.sensor_file_display = pn.pane.Markdown(
            f"**Log File:** `{self.sensor_log_path}`"
        )
        self.sensor_refresh_button = pn.widgets.Button(
            name="Refresh live plot",
            button_type="primary",
        )


        # ---------- History widgets ----------
        self.history_file_selector = pn.widgets.Select(
            name="Select Log File",
            options={}
        )
        self.refresh_history_button = pn.widgets.Button(
            name="Refresh file list",
            button_type="primary"
        )
        self.history_info = pn.pane.Markdown("No history file selected.")
        self.history_plot = pn.pane.Plotly(height=800, width=1000)

        # ---------- Event handlers ----------
        self.stop_button.on_click(self._stop_test)
        self.full_charge_button.on_click(self._start_full_charge_thread)
        self.discharge_c5_button.on_click(self._start_discharge_c5_thread)
        self.discharge_c2_button.on_click(self._start_discharge_c2_thread)
        self.degradation_cycles_button.on_click(self._start_degradation_cycles_thread)
        self.refresh_history_button.on_click(self._refresh_history_files)
        self.sensor_refresh_button.on_click(self._refresh_sensor_button_click)
        self.run_full_plan_button.on_click(self._start_full_plan_thread)



        self.history_file_selector.param.watch(
            self._update_history_plot, "value"
        )

        # Initialise history file list
        self._refresh_history_files(initial=True)

        # ---------- Panels ----------
        self.sensor_panel = pn.Column(
            "# 📏 Sensor Data Monitor",
            pn.Row(self.sensor_status, self.sensor_refresh_button),
            self.sensor_file_display,
            self.sensor_plot,
        )


        self.control_panel = pn.Column(
            "# 🔋 Battery Test Controller",
            self.test_selector,
            pn.Row(
                self.stop_button,
                self.full_charge_button,
                self.discharge_c5_button,
                self.discharge_c2_button,
                self.degradation_cycles_button,
                self.run_full_plan_button,
            ),
            self.console_output
        )

        self.history_panel = pn.Column(
            "# 📚 Test History",
            pn.Row(self.history_file_selector, self.refresh_history_button),
            self.history_info,
            self.history_plot,
        )

        # Custom profile tab, implemented in separate module
        self.profile_runner = ProfileRunner(
            self.bdps,
            self.sensor_reader,
            LOG_ROOT,
            self._log,
            set_bdps_log_path=self._set_bdps_log_path,
            stop_event=self.stop_event,   # new
        )


    def _set_bdps_log_path(self, path: str):
        """Callback so ProfileRunner can update the live BDPS log path."""
        self.bdps_log_path = path

    # ------------- Utility logging -------------
    def _log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_output.value += f"[{timestamp}] {msg}\n"

    # ------------- Live sensor refresh -------------
    def _refresh_sensor_button_click(self, event=None):
        """
        Manual refresh from the button in the Live Sensor Data tab.
        Keeps the sensor file label in sync and forces one update.
        """
        # Make sure the label shows the actual file the sensor reader is writing to
        self.sensor_log_path = self.sensor_reader.log_path
        self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"

        # Trigger a fresh read of sensor and BDPS data
        self._refresh_sensor_data()

    def _start_full_plan_thread(self, event=None):
        """
        Runs: 10 degradation cycles -> Discharge C/5 -> Real-world test day profile.
        Repeats this block for N blocks (default 20 to cover 200 cycles worth of degradation blocks).
        """
        if self.running_test:
            self._log("Another test is already running; cannot start full plan.")
            return

        if self.bdps is None:
            self._log("BDPS is not connected; cannot start full plan.")
            return

        # Require that a test-day profile is loaded in ProfileRunner
        if self.profile_runner.profile_df is None:
            self._log("No test-day profile loaded. Load it in the Custom Profile tab first.")
            return

        self.running_test = True
        self.stop_event.clear()

        self.test_thread = threading.Thread(
            target=self._run_full_plan,
            daemon=True,
        )
        self.test_thread.start()

    def _run_full_plan(self, blocks: int = 20):
        """
        blocks=20 corresponds to 20x(10 degradation cycles) = 200 degradation cycles.
        Each block also includes one SOH discharge C/5 and one custom test day.
        """
        try:
            self._log(f"Starting full plan: {blocks} blocks.")
            for b in range(1, blocks + 1):
                if self.stop_event.is_set():
                    self._log("Stop requested, aborting full plan.")
                    break

                self._log(f"=== Block {b}/{blocks}: 10 degradation cycles ===")
                ok = self._run_degradation_cycles_block(block_index=b)
                if not ok or self.stop_event.is_set():
                    break

                self._log(f"=== Block {b}/{blocks}: SOH Discharge C/5 ===")
                ok = self._run_discharge_c5_block(block_index=b)
                if not ok or self.stop_event.is_set():
                    break

                self._log(f"=== Block {b}/{blocks}: Real-world test day profile ===")
                ok = self._run_test_day_profile_block(block_index=b)
                if not ok or self.stop_event.is_set():
                    break

            self._log("Full plan finished.")
        except Exception as e:
            self._log(f"Unexpected error in full plan: {e}")
        finally:
            self.running_test = False

    # -----------------------------
    # BLOCK HELPERS
    # These run synchronously and return True/False.
    # -----------------------------
    def _run_degradation_cycles_block(self, block_index: int) -> bool:
        """
        Calls your existing degradation routine, but with unique timestamps per block,
        so logs do not overwrite each other.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        try:
            for cycle in range(1, 11):
                if self.stop_event.is_set():
                    self._log("Stop requested, aborting degradation block.")
                    return False

                self._log(f"Block {block_index}: Degradation cycle {cycle}/10 discharge.")

                # Sensor file
                if self.sensor_reader is not None:
                    discharge_sensor_log = os.path.join(
                        LOG_ROOT,
                        f"Block_{block_index:02d}_Degr_{cycle:02d}_discharge_sensor_{timestamp}.csv",
                    )
                    self.sensor_log_path = discharge_sensor_log
                    self.sensor_reader.change_log_file(discharge_sensor_log)
                    self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"
                    self.sensor_reader.set_mode("discharge")

                discharge_log = os.path.join(
                    LOG_ROOT,
                    f"Block_{block_index:02d}_Degr_{cycle:02d}_discharge_bdps_{timestamp}.csv",
                )
                self.bdps_log_path = discharge_log

                discharged_Ah = self.bdps.discharge_cc_until_voltage(
                    current=9.0,
                    target_voltage=10.8,
                    log_path=discharge_log,
                    stop_event=self.stop_event,
                )
                self._log(
                    f"Block {block_index}: cycle {cycle} discharge complete, removed ≈ {discharged_Ah:.2f} Ah."
                )

                if self.stop_event.is_set():
                    self._log("Stop requested after discharge, aborting degradation block.")
                    return False

                self._log(f"Block {block_index}: Degradation cycle {cycle}/10 charge.")

                if self.sensor_reader is not None:
                    charge_sensor_log = os.path.join(
                        LOG_ROOT,
                        f"Block_{block_index:02d}_Degr_{cycle:02d}_charge_sensor_{timestamp}.csv",
                    )
                    self.sensor_log_path = charge_sensor_log
                    self.sensor_reader.change_log_file(charge_sensor_log)
                    self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"
                    self.sensor_reader.set_mode("charge")

                charge_log = os.path.join(
                    LOG_ROOT,
                    f"Block_{block_index:02d}_Degr_{cycle:02d}_charge_bdps_{timestamp}.csv",
                )
                self.bdps_log_path = charge_log

                charged_Ah = self.bdps.charge_battery_cc_cv(
                    target_voltage=14.4,
                    max_current=5.4,
                    duration_hours=3,
                    log_path=charge_log,
                    stop_event=self.stop_event,
                )
                self._log(
                    f"Block {block_index}: cycle {cycle} charge complete, charged ≈ {charged_Ah:.2f} Ah."
                )

            # After block: return sensor logging to default live file if you want
            if self.sensor_reader is not None:
                self.sensor_reader.change_log_file("live_sensor_output.csv")

            return True

        except Exception as e:
            self._log(f"Error in degradation block {block_index}: {e}")
            return False

    def _run_discharge_c5_block(self, block_index: int) -> bool:
        """
        Runs your C/5 discharge synchronously, with unique log names.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        try:
            if self.stop_event.is_set():
                return False

            if self.sensor_reader is not None:
                self.sensor_reader.set_mode("discharge")
                self.sensor_log_path = os.path.join(
                    LOG_ROOT,
                    f"Block_{block_index:02d}_SOH_C5_sensor_{timestamp}.csv"
                )
                self.sensor_reader.change_log_file(self.sensor_log_path)
                self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"

            log_file_bdps = os.path.join(
                LOG_ROOT,
                f"Block_{block_index:02d}_SOH_C5_bdps_{timestamp}.csv"
            )
            self.bdps_log_path = log_file_bdps

            discharged_Ah = self.bdps.discharge_cc_until_voltage(
                current=3.6,
                target_voltage=10.8,
                log_path=log_file_bdps,
                stop_event=self.stop_event,
            )
            self.last_capacity_Ah = discharged_Ah
            self._log(
                f"Block {block_index}: SOH C/5 complete, capacity ≈ {discharged_Ah:.2f} Ah."
            )

            if self.sensor_reader is not None:
                self.sensor_reader.change_log_file("live_sensor_output.csv")

            return True

        except Exception as e:
            self._log(f"Error in SOH C/5 block {block_index}: {e}")
            return False

    def _run_test_day_profile_block(self, block_index: int) -> bool:
        """
        Runs the already loaded ProfileRunner profile, but synchronously.
        This avoids starting a second thread and lets the full plan wait until completion.
        """
        if self.profile_runner.profile_df is None:
            self._log("No profile loaded in ProfileRunner, cannot run test day.")
            return False

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file_bdps = os.path.join(
            LOG_ROOT,
            f"Block_{block_index:02d}_TestDay_bdps_{timestamp}.csv"
        )
        self.bdps_log_path = log_file_bdps

        # Let ProfileRunner update BDPS log path
        if self.profile_runner.set_bdps_log_path is not None:
            self.profile_runner.set_bdps_log_path(log_file_bdps)

        # Run profile in this same thread (blocking) so sequencing is correct.
        try:
            if self.stop_event is not None:
                self.stop_event.clear()

            self.profile_runner.running_profile = True

            # Ensure sensor mode is set
            if self.sensor_reader is not None:
                self.sensor_reader.set_mode("profile")

            # Call the internal runner directly
            self.profile_runner._run_profile(log_file_bdps, sample_rate_hz=10.0)

            if self.stop_event.is_set():
                self._log(f"Block {block_index}: test day stopped by user.")
                return False

            self._log(f"Block {block_index}: test day profile completed.")
            return True

        except Exception as e:
            self._log(f"Error in test day block {block_index}: {e}")
            return False
        finally:
            self.profile_runner.running_profile = False



    def _refresh_sensor_data(self):
        self.sensor_status.object = f"**Sensor Mode:** {self.sensor_reader.mode}"
        file_path = self.sensor_reader.log_path
        file_path_bdps = self.bdps_log_path
        # self._log(f"BDPS live file: {file_path_bdps}")
        if not os.path.exists(file_path):
            self._log(f"⚠️ Sensor log file not found: {file_path}")
            return

        # Read the last 500 lines to be sure we get the header + valid data
        with open(file_path, "r") as f:
            last_lines = deque(f, maxlen=500)

        # Ensure there is a header
        if not last_lines or "," not in last_lines[0]:
            self._log("⚠️ Sensor log missing or malformed header.")
            return

        header = last_lines[0].strip()
        data = [
            line.strip()
            for line in last_lines
            if line.strip() and line.strip() != header
        ]

        if not data:
            self._log("⚠️ No usable sensor data found.")
            return

        csv_data = "\n".join([header] + data)
        df = pd.read_csv(StringIO(csv_data))
        df.columns = ["timestamp", "voltage", "current", "temperature", "humidity"]

        if "timestamp" not in df.columns:
            self._log("⚠️ 'timestamp' column missing in parsed data.")
            return

        df_plot = df.iloc[::10]  # Downsample

        fig = make_subplots(
            rows=4,
            cols=2,
            shared_xaxes=True,
            subplot_titles=(
                "Sensor Voltage (V)", "BDPS Voltage (V)",
                "Sensor Current (A)", "BDPS Current (A)",
                "Sensor Temperature (°C)", "BDPS Mode (0=CC, 1=CV)",
                "Sensor Humidity (%)", ""
            )
        )

        # BDPS side if available
        if file_path_bdps is not None and os.path.exists(file_path_bdps):
            with open(file_path_bdps, "r") as f:
                last_lines_bdps = deque(f, maxlen=500)

            if not last_lines_bdps or "," not in last_lines_bdps[0]:
                self._log("⚠️ BDPS log missing or malformed header.")
            else:
                header_b = last_lines_bdps[0].strip()
                data_b = [
                    line.strip()
                    for line in last_lines_bdps
                    if line.strip() and line.strip() != header_b
                ]
                if data_b:
                    csv_data_bdps = "\n".join([header_b] + data_b)
                    df_bdps = pd.read_csv(StringIO(csv_data_bdps))
                    df_bdps.columns = [
                        "Timestamp", "Elapsed_s",
                        "Voltage", "Current", "Mode"
                    ]
                    fig.add_trace(
                        go.Scatter(
                            x=df_bdps["Timestamp"],
                            y=df_bdps["Voltage"],
                            mode="lines",
                            name="Voltage"
                        ),
                        row=1, col=2
                    )
                    fig.add_trace(
                        go.Scatter(
                            x=df_bdps["Timestamp"],
                            y=df_bdps["Current"],
                            mode="lines",
                            name="Current"
                        ),
                        row=2, col=2
                    )
                    mode_map = {"CC": 0, "CV": 1}
                    df_bdps["ModeNum"] = df_bdps["Mode"].map(mode_map)
                    fig.add_trace(
                        go.Scatter(
                            x=df_bdps["Timestamp"],
                            y=df_bdps["ModeNum"],
                            mode="lines",
                            name="BDPS Mode",
                            line=dict(dash="dot")
                        ),
                        row=3, col=2
                    )
                else:
                    self._log("⚠️ No usable BDPS data found.")
        else:
            # Only print once in console
            pass

        # Sensor side
        fig.add_trace(
            go.Scatter(
                x=df_plot["timestamp"],
                y=df_plot["voltage"],
                mode="lines",
                name="Voltage"
            ),
            row=1, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=df_plot["timestamp"],
                y=df_plot["current"],
                mode="lines",
                name="Current"
            ),
            row=2, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=df_plot["timestamp"],
                y=df_plot["temperature"],
                mode="lines",
                name="Temperature"
            ),
            row=3, col=1
        )
        fig.add_trace(
            go.Scatter(
                x=df_plot["timestamp"],
                y=df_plot["humidity"],
                mode="lines",
                name="Humidity"
            ),
            row=4, col=1
        )

        fig.update_layout(
            height=800,
            width=1000,
            showlegend=False,
            margin=dict(l=40, r=40, t=30, b=40),
            xaxis4=dict(title="Timestamp")
        )

        self.sensor_plot.object = fig

    # ------------- History helpers -------------
    def _get_log_file_options(self):
        """Scan LOG_ROOT and return dict label -> full path sorted by newest first."""
        if not os.path.isdir(LOG_ROOT):
            return {}

        files = [
            os.path.join(LOG_ROOT, f)
            for f in os.listdir(LOG_ROOT)
            if f.lower().endswith(".csv")
        ]
        if not files:
            return {}

        files.sort(key=os.path.getmtime, reverse=True)

        options = {}
        for p in files:
            mtime = datetime.fromtimestamp(
                os.path.getmtime(p)
            ).strftime("%Y-%m-%d %H:%M")
            label = f"{mtime} | {os.path.basename(p)}"
            options[label] = p
        return options

    def _refresh_history_files(self, event=None, initial=False):
        opts = self._get_log_file_options()
        if not opts:
            self.history_file_selector.options = {"No CSV files found": None}
            self.history_file_selector.value = None
            self.history_info.object = "No CSV history files found in `/media/pi/LOGBATTEST`."
            self.history_plot.object = None
            return

        self.history_file_selector.options = opts
        # On first call, automatically select newest and plot it
        if initial or self.history_file_selector.value is None:
            first_label = next(iter(opts.keys()))
            self.history_file_selector.value = opts[first_label]
            # Manual trigger of plot
            self._update_history_plot(None)

    def _update_history_plot(self, event):
        path = self.history_file_selector.value
        if path is None or not isinstance(path, str):
            return
        if not os.path.exists(path):
            self.history_info.object = f"Selected file does not exist anymore: `{path}`"
            self.history_plot.object = None
            return

        try:
            df = pd.read_csv(path)
        except Exception as e:
            self.history_info.object = f"Could not read CSV: `{path}`\n\nError: {e}"
            self.history_plot.object = None
            return

        # Standardise column names for detection
        cols_lower = {c.lower(): c for c in df.columns}

        is_sensor = (
            "timestamp" in cols_lower
            and "voltage" in cols_lower
            and "current" in cols_lower
            and "temperature" in cols_lower
        )
        is_bdps = (
            "timestamp" in cols_lower
            and "voltage" in cols_lower
            and "current" in cols_lower
            and "mode" in cols_lower
        )

        if is_sensor:
            tcol = cols_lower["timestamp"]
            vcol = cols_lower["voltage"]
            ccol = cols_lower["current"]
            tccol = cols_lower["temperature"]
            hcol = cols_lower.get("humidity")

            fig = make_subplots(
                rows=4,
                cols=1,
                shared_xaxes=True,
                subplot_titles=(
                    "Sensor Voltage (V)",
                    "Sensor Current (A)",
                    "Sensor Temperature (°C)",
                    "Sensor Humidity (%)" if hcol else ""
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=df[tcol],
                    y=df[vcol],
                    mode="lines",
                    name="Voltage"
                ),
                row=1, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df[tcol],
                    y=df[ccol],
                    mode="lines",
                    name="Current"
                ),
                row=2, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df[tcol],
                    y=df[tccol],
                    mode="lines",
                    name="Temperature"
                ),
                row=3, col=1
            )
            if hcol:
                fig.add_trace(
                    go.Scatter(
                        x=df[tcol],
                        y=df[hcol],
                        mode="lines",
                        name="Humidity"
                    ),
                    row=4, col=1
                )

            fig.update_layout(
                height=800,
                width=1000,
                showlegend=False,
                margin=dict(l=40, r=40, t=30, b=40),
                xaxis4=dict(title="Timestamp")
            )
            self.history_plot.object = fig
            self.history_info.object = (
                f"**Sensor log:** `{os.path.basename(path)}`  \n"
                f"Rows: {len(df)}"
            )
            return

        if is_bdps:
            tcol = cols_lower["timestamp"]
            vcol = cols_lower["voltage"]
            ccol = cols_lower["current"]
            mcol = cols_lower["mode"]

            fig = make_subplots(
                rows=3,
                cols=1,
                shared_xaxes=True,
                subplot_titles=(
                    "BDPS Voltage (V)",
                    "BDPS Current (A)",
                    "BDPS Mode (0=CC, 1=CV)"
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=df[tcol],
                    y=df[vcol],
                    mode="lines",
                    name="Voltage"
                ),
                row=1, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df[tcol],
                    y=df[ccol],
                    mode="lines",
                    name="Current"
                ),
                row=2, col=1
            )

            # Convert textual mode to numeric if needed
            mode_series = df[mcol]
            if mode_series.dtype == object:
                mode_map = {"CC": 0, "CV": 1}
                mode_series = mode_series.map(mode_map)
            fig.add_trace(
                go.Scatter(
                    x=df[tcol],
                    y=mode_series,
                    mode="lines",
                    name="Mode",
                    line=dict(dash="dot")
                ),
                row=3, col=1
            )

            fig.update_layout(
                height=800,
                width=1000,
                showlegend=False,
                margin=dict(l=40, r=40, t=30, b=40),
                xaxis3=dict(title="Timestamp")
            )
            self.history_plot.object = fig
            self.history_info.object = (
                f"**BDPS log:** `{os.path.basename(path)}`  \n"
                f"Rows: {len(df)}"
            )
            return

        # Fallback: generic plot of first two numeric columns
        numeric_cols = df.select_dtypes("number").columns.tolist()
        if len(numeric_cols) >= 1:
            fig = go.Figure()
            for col in numeric_cols[:4]:
                fig.add_trace(
                    go.Scatter(
                        y=df[col],
                        mode="lines",
                        name=col
                    )
                )
            fig.update_layout(
                height=800,
                width=1000,
                xaxis_title="Index",
                yaxis_title="Value"
            )
            self.history_plot.object = fig
            self.history_info.object = (
                f"Generic plot for `{os.path.basename(path)}` "
                f"(could not recognise sensor or BDPS format)."
            )
        else:
            self.history_plot.object = None
            self.history_info.object = (
                f"File `{os.path.basename(path)}` has no numeric columns to plot."
            )

    # ------------- Test control -------------
    def _start_test_thread(self, event):
        if not self.running_test:
            self.running_test = True
            self.stop_event.clear()
            self.current_cycle = self.test_selector.value
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.sensor_log_path = os.path.join(
                LOG_ROOT,
                f"cycle_{self.current_cycle}_sensor_{timestamp}.csv"
            )
            self.sensor_reader.change_log_file(self.sensor_log_path)
            self.sensor_file_display.object = (
                f"**Log File:** `{self.sensor_log_path}`"
            )
            self.test_thread = threading.Thread(
                target=self._run_selected_test,
                daemon=True
            )
            self.test_thread.start()

    def _stop_test(self, event=None):
        # Signal running loops to stop on next iteration
        self.running_test = False
        if self.stop_event is not None:
            self.stop_event.set()

        # Also stop any running profile
        if hasattr(self, "profile_runner"):
            self.profile_runner.running_profile = False

        self._log("🛑 Test stopped manually.")

        # Disable BDPS output immediately
        if self.bdps:
            self.bdps.outputOff()


    def _start_degradation_cycles_thread(self, event=None):
        """Start 10 charge/discharge cycles in a background thread."""
        if self.running_test:
            self._log("Another test is already running; cannot start degradation cycles.")
            return

        if self.bdps is None:
            self._log("BDPS is not connected; cannot start degradation cycles.")
            return

        self.running_test = True
        self.stop_event.clear()
        self._log("Starting 10 degradation cycles (discharge + charge, end on charge).")

        self.test_thread = threading.Thread(
            target=self._run_degradation_cycles,
            daemon=True,
        )
        self.test_thread.start()

    def _run_degradation_cycles(self):
        """
        Perform 10 degradation cycles.

        Each cycle:
        1) Discharge CC until 10.8 V.
        2) Charge with CC/CV until taper or max time.

        Each test writes its own CSV file:
        DegradationCycle_<cycle>_discharge_YYYY-MM-DD_HH-MM-SS.csv
        DegradationCycle_<cycle>_charge_YYYY-MM-DD_HH-MM-SS.csv
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        try:
            for cycle in range(1, 11):
                if self.stop_event.is_set():
                    self._log("Stop requested, aborting degradation cycles.")
                    break

                self._log(f"Cycle {cycle}/10: starting discharge.")

                # Discharge step, set dedicated sensor file
                if self.sensor_reader is not None:
                    discharge_sensor_log = os.path.join(
                        LOG_ROOT,
                        f"DegradationCycle_{cycle:02d}_discharge_sensor_{timestamp}.csv",
                    )
                    self.sensor_log_path = discharge_sensor_log
                    self.sensor_reader.change_log_file(discharge_sensor_log)
                    self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"
                    self.sensor_reader.set_mode("discharge")

                    discharge_log = os.path.join(
                        LOG_ROOT,
                        f"DegradationCycle_{cycle:02d}_discharge_{timestamp}.csv",
                    )

                    # Point the live BDPS plot to the current discharge file
                    self.bdps_log_path = discharge_log

                    try:
                        discharged_Ah = self.bdps.discharge_cc_until_voltage(
                            current=13.5,         # C/1.5 for 18 Ah battery TODO CHANGED!
                            target_voltage=10.8, # cutoff
                            log_path=discharge_log,
                            stop_event=self.stop_event,
                        )
                        self._log(
                            f"Cycle {cycle}: discharge complete, "
                            f"removed capacity ≈ {discharged_Ah:.2f} Ah."
                        )
                    except Exception as e:
                        self._log(f"Cycle {cycle}: error during discharge: {e}")
                        break

                if self.stop_event.is_set():
                    self._log("Stop requested after discharge, aborting degradation cycles.")
                    break

                # Charge step
                self._log(f"Cycle {cycle}/10: starting charge.")

                if self.sensor_reader is not None:
                    charge_sensor_log = os.path.join(
                        LOG_ROOT,
                        f"DegradationCycle_{cycle:02d}_charge_sensor_{timestamp}.csv",
                    )
                    self.sensor_log_path = charge_sensor_log
                    self.sensor_reader.change_log_file(charge_sensor_log)
                    self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"
                    self.sensor_reader.set_mode("charge")

                charge_log = os.path.join(
                    LOG_ROOT,
                    f"DegradationCycle_{cycle:02d}_charge_{timestamp}.csv",
                )

                self.bdps_log_path = charge_log

                try:
                    charged_Ah = self.bdps.charge_battery_cc_cv(
                        target_voltage=14.4,
                        max_current=5.4,
                        duration_hours=1.5,
                        log_path=charge_log,
                        stop_event=self.stop_event,
                    )
                    self._log(
                        f"Cycle {cycle}: charge complete, "
                        f"charged capacity ≈ {charged_Ah:.2f} Ah."
                    )
                    charge_sensor_log = "live_sensor_output.csv"
                    self.sensor_log_path = charge_sensor_log
                    self.sensor_reader.change_log_file(charge_sensor_log)
                except Exception as e:
                    self._log(f"Cycle {cycle}: error during charge: {e}")
                    break

            self._log("Finished degradation routine, battery should end in charged state.")
        except Exception as e:
            self._log(f"Unexpected error during degradation cycles: {e}")
        finally:
            self.running_test = False

    def _start_full_charge_thread(self, event):
        if not self.running_test:
            self.running_test = True
            self.stop_event.clear()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            log_file = os.path.join(
                LOG_ROOT,
                f"full_charge_{timestamp}_sensor.csv"
            )
            self.sensor_log_path = log_file
            self.sensor_reader.change_log_file(log_file)
            self.sensor_file_display.object = f"**Log File:** `{log_file}`"
            log_file_bdps = os.path.join(
                LOG_ROOT,
                f"full_charge_{timestamp}_bdps.csv"
            )
            self.bdps_log_path = log_file_bdps
            self.test_thread = threading.Thread(
                target=self._run_full_charge,
                args=(log_file_bdps,),
                daemon=True
            )
            self.test_thread.start()

    def _run_full_charge(self, log_file):
        self._log("⚡ Starting full battery charge...")
        try:
            self.sensor_reader.set_mode("charge")
            charged_Ah = self.bdps.charge_battery_cc_cv(
                battery_psu=self.bdps,
                target_voltage=15.0,
                max_current=5.4,
                duration_hours=4,
                log_path=log_file,
                stop_event=self.stop_event,
            )
            self.last_capacity_Ah = charged_Ah
            self._log(f"✅ Full charge completed: {charged_Ah} Ah delivered.")
        except Exception as e:
            self._log(f"❌ Error during full charge: {e}")
        self.running_test = False

    def _run_selected_test(self):
        cycle = self.current_cycle
        steps = step_plan_df[step_plan_df["cycle"] == cycle]
        self._log(f"▶️ Starting cycle {cycle} with {len(steps)} steps")

        try:
            for _, row in steps.iterrows():
                if not self.running_test or self.stop_event.is_set():
                    self._log("Test stopped before completing all steps.")
                    break

                step = row["step"]
                mode = row["mode"]
                self._log(f"➡️ {step.capitalize()}: {row['description']}")
                log_file = os.path.join(
                    LOG_ROOT,
                    f"cycle_{cycle}_{step}.csv"
                )

                # BDPS live view should track this file
                self.bdps_log_path = log_file

                if mode == "discharge":
                    self.sensor_reader.set_mode("discharge")
                    discharged_Ah = self.bdps.discharge_fixed_time(
                        current=row["current_A"],
                        duration_minutes=row["duration_min"],
                        voltage_cutoff=row["voltage_V"],
                        log_path=log_file,
                        stop_event=self.stop_event,
                    )
                    self._log(f"✅ Discharged: {discharged_Ah} Ah")

                elif mode == "charge":
                    self.sensor_reader.set_mode("charge")
                    charged_Ah = self.bdps.charge_battery_cc_cv(
                        battery_psu=self.bdps,
                        target_voltage=row["voltage_V"],
                        max_current=row["current_A"],
                        duration_hours=row["duration_min"] / 60,
                        log_path=log_file,
                        stop_event=self.stop_event,
                    )
                    self._log(f"✅ Charged: {charged_Ah} Ah")

                elif mode == "soh_check":
                    self.sensor_reader.set_mode("discharge")
                    soh_capacity = self.bdps.discharge_fixed_time(
                        current=row["current_A"],
                        duration_minutes=600,
                        voltage_cutoff=10.8,
                        log_path=log_file,
                        stop_event=self.stop_event,
                    )
                    self._log(
                        f"📉 SOH Measured Capacity: {soh_capacity} Ah"
                    )
        except Exception as e:
            self._log(f"❌ Error: {e}")

        self.running_test = False

    def _start_discharge_c5_thread(self, event):
        if not self.running_test:
            self.running_test = True
            self.stop_event.clear()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            log_file_bdps = os.path.join(
                LOG_ROOT,
                f"discharge_c5_{timestamp}_bdps.csv"
            )
            self.bdps_log_path = log_file_bdps
            self.sensor_log_path = os.path.join(
                LOG_ROOT,
                f"discharge_c5_{timestamp}_sensor.csv"
            )
            self.sensor_reader.change_log_file(self.sensor_log_path)
            self.sensor_file_display.object = (
                f"**Log File:** `{self.sensor_log_path}`"
            )
            self.test_thread = threading.Thread(
                target=self._run_discharge_c5,
                args=(log_file_bdps,),
                daemon=True
            )
            self.test_thread.start()

    def _start_discharge_c2_thread(self, event):
        if not self.running_test:
            self.running_test = True
            self.stop_event.clear()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            log_file_bdps = os.path.join(
                LOG_ROOT,
                f"discharge_c2_{timestamp}_bdps.csv"
            )
            self.bdps_log_path = log_file_bdps

            self.sensor_log_path = os.path.join(
                LOG_ROOT,
                f"discharge_c2_{timestamp}_sensor.csv"
            )
            self.sensor_reader.change_log_file(self.sensor_log_path)
            self.sensor_file_display.object = (
                f"**Log File:** `{self.sensor_log_path}`"
            )

            self.test_thread = threading.Thread(
                target=self._run_discharge_c2,
                args=(log_file_bdps,),
                daemon=True
            )
            self.test_thread.start()

    def _run_discharge_c5(self, log_file):
        self._log(
            "⚡ Starting Discharge C/5 test (3.6A until 10.8 V)..."
        )
        try:
            self.sensor_reader.set_mode("discharge")
            discharged_Ah = self.bdps.discharge_cc_until_voltage(
                current=3.6,
                target_voltage=10.8,
                log_path=log_file,
                stop_event=self.stop_event,
            )
            self.last_capacity_Ah = discharged_Ah
            self._log(
                f"📉 Discharge complete. Measured Capacity (SOH): "
                f"{discharged_Ah:.2f} Ah"
            )
        except Exception as e:
            self._log(f"❌ Error during C/5 discharge test: {e}")
        self.running_test = False

    def _run_discharge_c2(self, log_file):
        self._log(
            "⚡ Starting Discharge C/2 test (9A until 10.8 V)..."
        )
        try:
            self.sensor_reader.set_mode("discharge")
            discharged_Ah = self.bdps.discharge_cc_until_voltage(
                current=9.0,
                target_voltage=10.8,
                log_path=log_file,
                stop_event=self.stop_event,
            )
            self.last_capacity_Ah = discharged_Ah
            self._log(
                f"📉 Discharge complete. Measured Capacity (SOH): "
                f"{discharged_Ah:.2f} Ah"
            )
        except Exception as e:
            self._log(f"❌ Error during C/2 discharge test: {e}")
        self.running_test = False

    def panel(self):
        if not hasattr(self, "_sensor_callback"):
            self._sensor_callback = pn.state.add_periodic_callback(
                self._refresh_sensor_data,
                period=2000
            )
        return pn.Tabs(
            ("📊 Control Tests", self.control_panel),
            ("📏 Live Sensor Data", self.sensor_panel),
            ("📚 History", self.history_panel),
            ("🧪 Custom Profile", self.profile_runner.panel()),
        )

# Launch the dashboard
dashboard = BatteryTestDashboard(simulate=False)
pn.serve(dashboard.panel, show=False, port=39517, websocket_origin=[
    "localhost:39517",
    "batterytestpi:39517",
    "127.0.0.1:39517",
])
