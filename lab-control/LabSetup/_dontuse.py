import panel as pn
from plotly.subplots import make_subplots
import plotly.graph_objs as go
import pandas as pd
import threading
from datetime import datetime
from Classes import bdps_class as cl
from Classes import new_sensor_reader_class as sensor_reader
import time
import os

pn.extension("plotly")

step_plan_df = pd.read_csv("detailed_battery_test_plan.csv")
LOG_ROOT = "/media/pi/LOGBATTEST"
os.makedirs(LOG_ROOT, exist_ok=True)

class BatteryTestDashboard:
    def __init__(self, bdps_ip="192.168.0.130", simulate=False):
        self.bdps = cl.bdps_control(bdps_ip)
        self.bdps.connect()
        self.sensor_reader = sensor_reader.SensorReader(simulate=False, port="/dev/ttyACM0")
        self.sensor_reader.start()
        self.sensor_reader.set_mode("idle")
        self.running_test = False
        self.current_cycle = None
        self.sensor_log_path = None

        self.test_selector = pn.widgets.Select(name="Select Test Cycle", options=sorted(step_plan_df["cycle"].unique().tolist()))
        self.start_button = pn.widgets.Button(name="Start Test", button_type="primary")
        self.stop_button = pn.widgets.Button(name="Stop Test", button_type="danger")
        self.full_charge_button = pn.widgets.Button(name="Full Charge", button_type="success")

        self.console_output = pn.widgets.TextAreaInput(value="", height=200, width=600)
        self.sensor_table = pn.pane.DataFrame(pd.DataFrame(columns=["timestamp", "voltage", "current", "temperature"]), height=300)
        self.sensor_status = pn.pane.Markdown("**Sensor Mode:** idle", height=30)
        self.sensor_plot = pn.pane.Plotly(height=800, width=1000)
        self.sensor_file_display = pn.pane.Markdown("**Log File:** None", height=30)

        self.start_button.on_click(self._start_test_thread)
        self.stop_button.on_click(self._stop_test)
        self.full_charge_button.on_click(self._start_full_charge_thread)

        self.sensor_panel = pn.Column(
            "# 📏 Sensor Data Monitor",
            self.sensor_status,
            self.sensor_file_display,
            self.sensor_plot,
            self.sensor_table
        )

        self.control_panel = pn.Column(
            "# 🔋 Battery Test Controller",
            self.test_selector,
            pn.Row(self.start_button, self.stop_button, self.full_charge_button),
            self.console_output
        )

    def _log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console_output.value += f"[{timestamp}] {msg}\n"

    def _refresh_sensor_data(self):
        self.sensor_status.object = f"**Sensor Mode:** {self.sensor_reader.mode}"
        df = pd.DataFrame(self.sensor_reader.get_latest(40))
        df_bdps = pd.DataFrame(self.sensor_reader.get_latest_bdps(40))
        self.sensor_table.object = df

        if not df.empty:
            fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                                subplot_titles=("Voltage (V)", "Current (A)", "Temperature (°C)", "BDPS Voltage & Current"))
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["voltage"], mode="lines+markers", name="Voltage"), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["current"], mode="lines+markers", name="Current"), row=2, col=1)
            fig.add_trace(go.Scatter(x=df["timestamp"], y=df["temperature"], mode="lines+markers", name="Temperature"), row=3, col=1)

            if not df_bdps.empty:
                fig.add_trace(go.Scatter(x=df_bdps["timestamp"], y=df_bdps["voltage"], mode="lines+markers", name="BDPS Voltage"), row=4, col=1)
                fig.add_trace(go.Scatter(x=df_bdps["timestamp"], y=df_bdps["current"], mode="lines+markers", name="BDPS Current"), row=4, col=1)

            fig.update_layout(height=800, width=1000, showlegend=True,
                              margin=dict(l=40, r=40, t=30, b=40),
                              xaxis4=dict(title="Timestamp"))
            self.sensor_plot.object = fig

            if self.sensor_log_path:
                df.to_csv(self.sensor_log_path, index=False)

    def _start_test_thread(self, event):
        if not self.running_test:
            self.running_test = True
            self.current_cycle = self.test_selector.value
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.sensor_log_path = os.path.join(LOG_ROOT, f"cycle_{self.current_cycle}_sensor_{timestamp}.csv")
            self.sensor_file_display.object = f"**Log File:** `{self.sensor_log_path}`"
            threading.Thread(target=self._run_selected_test, daemon=True).start()

    def _stop_test(self, event=None):
        self.running_test = False
        try:
            self.bdps.setCurrent(0.1)
        except:
            self._log("⚠️ Failed to reset current to 0")
        self._log("🛑 Test stopped manually.")

    def _start_full_charge_thread(self, event):
        if not self.running_test:
            self.running_test = True
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            print("In here")
            log_file = os.path.join(LOG_ROOT, f"full_charge_{timestamp}_sensor.csv")
            self.sensor_log_path = log_file
            self.sensor_file_display.object = f"**Log File:** `{log_file}`"
            self.sensor_reader.change_log_file(log_file)
            print("Changed sensor log file for full charge. to " + log_file)
            threading.Thread(target=self._run_full_charge, args=(log_file,), daemon=True).start()

    def _run_full_charge(self, log_file):
        self._log("⚡ Starting full battery charge...")
        try:
            self.sensor_reader.set_mode("charge")
            charged_Ah = self.bdps.charge_battery_cc_cv(
                battery_psu=self.bdps,
                target_voltage=14.7,
                max_current=5.4,
                duration_hours=4,
                log_path=log_file
            )
            self._log(f"✅ Full charge completed: {charged_Ah} Ah delivered.")
        except Exception as e:
            self._log(f"❌ Error during full charge: {e}")
        self.running_test = False

    def _run_selected_test(self):
        cycle = self.current_cycle
        steps = step_plan_df[step_plan_df["cycle"] == cycle]

        self._log(f"▶️ Starting cycle {cycle} with {len(steps)} steps")
        try:
            for i, row in steps.iterrows():
                if not self.running_test:
                    break
                step = row["step"]
                mode = row["mode"]
                self._log(f"➡️ {step.capitalize()}: {row['description']}")
                log_file = os.path.join(LOG_ROOT, f"cycle_{cycle}_{step}.csv")
                if mode == "discharge":
                    self.sensor_reader.set_mode("discharge")
                    discharged_Ah = self.bdps.discharge_fixed_time(
                        current=row["current_A"],
                        duration_minutes=row["duration_min"],
                        voltage_cutoff=row["voltage_V"],
                        log_path=log_file
                    )
                    self._log(f"✅ Discharged: {discharged_Ah} Ah")
                elif mode == "charge":
                    self.sensor_reader.set_mode("charge")
                    charged_Ah = self.bdps.charge_battery_cc_cv(
                        battery_psu=self.bdps,
                        target_voltage=row["voltage_V"],
                        max_current=row["current_A"],
                        duration_hours=row["duration_min"] / 60,
                        log_path=log_file
                    )
                    self._log(f"✅ Charged: {charged_Ah} Ah")
                elif mode == "soh_check":
                    self.sensor_reader.set_mode("discharge")
                    soh_capacity = self.bdps.discharge_fixed_time(
                        current=row["current_A"],
                        duration_minutes=600,
                        voltage_cutoff=10.8,
                        log_path=log_file
                    )
                    self._log(f"📉 SOH Measured Capacity: {soh_capacity} Ah")
        except Exception as e:
            self._log(f"❌ Error: {e}")
        self.running_test = False

    def panel(self):
        if not hasattr(self, "_sensor_callback"):
            self._sensor_callback = pn.state.add_periodic_callback(self._refresh_sensor_data, period=2000)
        return pn.Tabs(
            ("📊 Control Tests", self.control_panel),
            ("📏 Live Sensor Data", self.sensor_panel)
        )

dashboard = BatteryTestDashboard(simulate=False)
pn.serve(dashboard.panel, show=False)
