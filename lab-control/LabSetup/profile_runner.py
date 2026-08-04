import os
import time
import threading
from datetime import datetime
from io import BytesIO

import panel as pn
import pandas as pd
from plotly.subplots import make_subplots
import plotly.graph_objs as go

import numpy as np

import csv
import os as _os
from datetime import datetime


class ProfileRunner:
    """
    Handles the 'Custom Profile' functionality:
    - upload a CSV with Time (s), Voltage (V), Current (A)
    - validate and preview the profile
    - run it on the BDPS in current control mode and log the run
    """

    def __init__(
            self,
            bdps,
            sensor_reader,
            log_root,
            log_func,
            set_bdps_log_path=None,
            stop_event=None,
    ):
        """
        Parameters
        ----------
        bdps : bdps_control or None
            Instance of the BDPS controller. If None, running is disabled.
        sensor_reader : SensorReader or None
            Sensor reader instance; used only to set mode.
        log_root : str or Path
            Directory where BDPS logs will be written.
        log_func : callable
            Function that accepts a single string message for logging
            to the main dashboard console.
        set_bdps_log_path : callable or None
            Callback to inform the dashboard of the current BDPS log file.
        stop_event : threading.Event or None
            Shared stop flag from the main dashboard. When set, the profile stops.
        """
        self.bdps = bdps
        self.sensor_reader = sensor_reader
        self.log_root = str(log_root)
        self.log = log_func
        self.set_bdps_log_path = set_bdps_log_path
        self.stop_event = stop_event

        # Internal state
        self.profile_df = None
        self.running_profile = False

        # Widgets
        self.profile_file_input = pn.widgets.FileInput(
            name="Upload profile CSV", accept=".csv"
        )
        self.load_profile_button = pn.widgets.Button(
            name="Load profile", button_type="primary"
        )
        self.run_profile_button = pn.widgets.Button(
            name="Run profile on BDPS", button_type="success", disabled=True
        )
        self.profile_info = pn.pane.Markdown("No profile loaded.")
        self.profile_preview_plot = pn.pane.Plotly(height=400, width=800)

        # Callbacks
        self.load_profile_button.on_click(self._load_profile)
        self.run_profile_button.on_click(self._start_profile_thread)

        self.enable_70A_pretest = True   # run it automatically before real profile

        # Layout
        self._panel = pn.Column(
            "# 🧪 Custom BDPS Profile Runner",
            "Upload a CSV with columns `Time (s)`, `Voltage (V)`, `Current (A)`.",
            pn.Row(
                self.profile_file_input,
                self.load_profile_button,
                self.run_profile_button,
            ),
            self.profile_info,
            self.profile_preview_plot,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def panel(self):
        """Return the Panel layout for inclusion in Tabs."""
        return self._panel

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _load_profile(self, event=None):
        """Load uploaded CSV with profile and show a preview."""
        if not self.profile_file_input.value:
            self.profile_info.object = "No file uploaded."
            self.profile_preview_plot.object = None
            self.profile_df = None
            self.run_profile_button.disabled = True
            return

        try:
            bio = BytesIO(self.profile_file_input.value)
            df = pd.read_csv(bio)
        except Exception as e:
            self.profile_info.object = (
                f"Could not read uploaded CSV.\n\nError: {e}"
            )
            self.profile_preview_plot.object = None
            self.profile_df = None
            self.run_profile_button.disabled = True
            return

        # Standardise column names to Time_s, Voltage_V, Current_A
        cols_lower = {c.lower(): c for c in df.columns}
        required = ["time (s)", "voltage (v)", "current (a)"]
        missing = [c for c in required if c not in cols_lower]
        if missing:
            self.profile_info.object = (
                "CSV is missing required columns: "
                + ", ".join(missing)
                + "\n\nExpected columns: Time (s), Voltage (V), Current (A)."
            )
            self.profile_preview_plot.object = None
            self.profile_df = None
            self.run_profile_button.disabled = True
            return

        df_renamed = df.rename(
            columns={
                cols_lower["time (s)"]: "Time_s",
                cols_lower["voltage (v)"]: "Voltage_V",
                cols_lower["current (a)"]: "Current_A",
            }
        ).copy()

        df_renamed = df_renamed.sort_values("Time_s").drop_duplicates("Time_s")

        # Check BDPS current limit
        max_abs_current = df_renamed["Current_A"].abs().max()
        if self.bdps is not None:
            bdps_max_cur = getattr(self.bdps, "MAX_CUR", 90.0)
        else:
            bdps_max_cur = 70

        if max_abs_current > bdps_max_cur:
            print(f"Max abs current in profile: {max_abs_current:.2f} A")

            self.profile_info.object = (
                f"Profile current {max_abs_current:.2f} A exceeds BDPS limit "
                f"of {bdps_max_cur} A. Adjust profile first."
            )
            self.profile_preview_plot.object = None
            self.profile_df = None
            self.run_profile_button.disabled = True
            return

        self.profile_df = df_renamed.reset_index(drop=True)

        # Preview plot
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            subplot_titles=("Profile Voltage (V)", "Profile Current (A)"),
        )
        fig.add_trace(
            go.Scatter(
                x=self.profile_df["Time_s"],
                y=self.profile_df["Voltage_V"],
                mode="lines",
                name="Voltage",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=self.profile_df["Time_s"],
                y=self.profile_df["Current_A"],
                mode="lines",
                name="Current",
            ),
            row=2,
            col=1,
        )
        fig.update_layout(
            height=400,
            width=800,
            showlegend=False,
            margin=dict(l=40, r=40, t=30, b=40),
            xaxis2=dict(title="Time (s)"),
        )

        self.profile_preview_plot.object = fig
        self.profile_info.object = (
            f"Loaded profile with {len(self.profile_df)} points.\n\n"
            f"Current range: {self.profile_df['Current_A'].min():.2f} A "
            f"to {self.profile_df['Current_A'].max():.2f} A."
        )

        # Only allow running when a real BDPS is connected
        self.run_profile_button.disabled = self.bdps is None

    def _start_profile_thread(self, event=None):
        if self.bdps is None:
            self.log("BDPS not connected.")
            return
        if self.profile_df is None:
            self.log("No profile loaded.")
            return
        if self.running_profile:
            self.log("Profile already running.")
            return

        # clear stop flag
        if self.stop_event is not None:
            self.stop_event.clear()

        self.running_profile = True

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file_bdps = os.path.join(
            self.log_root,
            f"testday_run_{timestamp}_bdps.csv",
        )

        if self.set_bdps_log_path is not None:
            self.set_bdps_log_path(log_file_bdps)


        # start real profile
        thread = threading.Thread(
            target=self._run_profile,
            args=(log_file_bdps,),
            daemon=True
        )
        thread.start()


    def _run_profile(self, log_file, sample_rate_hz: float = 10.0):
        """
        Execute the loaded profile on the BDPS with high frequency logging.

        Uses the profile as a step wise current schedule and logs BDPS data
        in the same 5-column format expected by the dashboard:
        Timestamp, Elapsed_s, Voltage, Current, Mode
        """
        df = self.profile_df
        if df is None:
            self.log("No profile loaded in _run_profile.")
            self.running_profile = False
            return

        try:
            # Resolve column names for time and current
            time_col_candidates = ["Time_s", "Time (s)", "time", "Time"]
            curr_col_candidates = ["Current_A", "Current (A)", "current", "Current"]

            time_col = next((c for c in time_col_candidates if c in df.columns), None)
            curr_col = next((c for c in curr_col_candidates if c in df.columns), None)

            if time_col is None or curr_col is None:
                self.log(f"Profile columns not found. Have: {list(df.columns)}")
                self.running_profile = False
                return

            # Ensure numeric and sorted by time
            df = df.copy()
            df[time_col] = df[time_col].astype(float)
            df[curr_col] = df[curr_col].astype(float)
            df = df.sort_values(time_col).reset_index(drop=True)

            # Normalise time to start at zero
            t0 = float(df[time_col].iloc[0])
            df[time_col] = df[time_col] - t0

            times = df[time_col].to_numpy()
            currents = df[curr_col].to_numpy()
            # Characteristic time step (approximately 0.1 s in your file)
            if len(times) > 1:
                dt = float(np.median(np.diff(times)))
            else:
                dt = 0.0

            # Treat the last time stamp as the *start* of the final step
            total_duration = float(times[-1]) + dt


            # ------------------------------------------------------------------
            # Configure BDPS like the working discharge methods
            # ------------------------------------------------------------------
            # Power limits: sink only, like discharge_cc_until_voltage
            hw_pow = getattr(self.bdps, "MAX_POW", 1100.0)
            neg_pow = 1000
            self.log(f"Setting BDPS power limits: pos=0.0 W, neg={neg_pow:.0f} W")
            self.bdps.setPowerLimits(pos_power=0.0, neg_power=neg_pow)

            # Current control mode
            self.bdps.sendCommand("SOUR:FUNC CURR")

            # Use maximum absolute current from profile as sink limit
            max_abs = float(np.max(np.abs(currents)))
            if max_abs <= 0.0:
                self.log("Profile contains only zero current, nothing to run.")
                self.running_profile = False
                return

            # Matching your discharge functions: pos_current=0, neg_current=max_abs
            self.log(
                f"Setting BDPS current limits to pos=0.00 A and neg={max_abs:.2f} A "
                "(sink only, like discharge)."
            )
            self.bdps.setCurrentLimits(
                pos_current=0.0,
                neg_current=max_abs,
            )

            # Safety voltage ceiling, as in other methods
            self.bdps.setVoltage(10.2)
            self.bdps.sendCommand("SYST:SENS:REM OFF")
            time.sleep(1.0)

            # Initial setpoint from profile (can be negative or zero)
            current_index = 0
            current_setpoint = float(currents[current_index])

            # collapse very small noise currents
            if abs(current_setpoint) < 0.2:
                current_setpoint = 0.0

            self.log(f"Initial current setpoint from profile: {current_setpoint:.2f} A")
            self.bdps.setCurrent(current_setpoint)


            self.bdps.outputOn()
            if self.sensor_reader is not None:
                self.sensor_reader.set_mode("profile")

            sample_interval = 1.0 / float(sample_rate_hz)
            start_time = time.time()

            with open(log_file, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(
                    ["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode"]
                )

                while self.running_profile:
                    # Respect the global stop flag from the dashboard
                    if self.stop_event is not None and self.stop_event.is_set():
                        self.log("Stop requested by user, stopping profile run.")
                        break

                    loop_t0 = time.time()
                    elapsed = loop_t0 - start_time

                    # Stop when profile time is exceeded
                    if elapsed > total_duration:
                        break

                    # Advance to the correct profile step for this elapsed time
                    while (
                        current_index + 1 < len(times)
                        and elapsed >= times[current_index + 1]
                    ):
                        current_index += 1
                        new_setpoint = float(currents[current_index])

                       # Only treat very small currents as 0 A
                        if abs(new_setpoint) < 0.2:
                            new_setpoint = 0.0


                        if new_setpoint != current_setpoint:
                            print(
                                f"At t={elapsed:.2f}s, updating current setpoint to "
                                f"{new_setpoint:.2f} A"
                            )
                            
                            if new_setpoint == 0.0:
                                self.bdps.outputOff()
                            elif new_setpoint < 0.0: 
                                # Current control mode
                                self.bdps.sendCommand("SOUR:FUNC CURR")
                                self.bdps.setVoltage(10.2)  # limit voltage when sinking
                                self.bdps.setCurrentLimits(
                                    pos_current=0.0,
                                    neg_current=new_setpoint * -1.2,  # 20% headroom
                                )
                                self.bdps.outputOn()
                            elif new_setpoint > 0.0:
                                print("Switching to sourcing mode for positive current.")
                                # Configure source mode, similar structure to discharge functions
                                # self.sendCommand("SOUR:FUNC VOLT")
                                self.bdps.setVoltage(15.0)
                                # self.setCurrent(new_setpoint)  # pre-set current

                                # Only allow positive source current in normal operation
                                self.bdps.setCurrentLimits(pos_current=new_setpoint * 1.2, neg_current=0.0)
                                self.bdps.setCurrent(new_setpoint)  
                                # Only allow positive power (source), no sink
                                self.bdps.setPowerLimits(pos_power=800.0, neg_power=0.0)

                                self.bdps.sendCommand("SYST:SENS:REM OFF")
                                time.sleep(0.5)
                                self.bdps.outputOn()
                            
                            self.bdps.setCurrent(new_setpoint)
                            current_setpoint = new_setpoint

                    # Measure from BDPS
                    voltage = self.bdps.readVoltage()
                    current_val = self.bdps.readCurrent()

                    mode_str = "CC"

                    writer.writerow(
                        [
                            datetime.now().isoformat(),
                            round(elapsed, 3),
                            round(float(voltage), 3),
                            round(float(current_val), 3),
                            mode_str,
                        ]
                    )
                    file.flush()
                    _os.fsync(file.fileno())

                    # Sleep to enforce sampling rate
                    loop_dt = time.time() - loop_t0
                    sleep_time = sample_interval - loop_dt
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            self.log("Custom profile run finished.")
        except Exception as e:
            self.log(f"Error during profile run: {e}")
        finally:
            try:
                if self.bdps is not None:
                    try:
                        self.bdps.setCurrent(0.0)
                    except Exception:
                        pass
                    self.bdps.outputOff()
            finally:
                self.running_profile = False
