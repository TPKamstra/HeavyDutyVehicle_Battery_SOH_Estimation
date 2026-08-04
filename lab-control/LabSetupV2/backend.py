"""
Singleton backend — holds BDPS, SensorReader, console log, and all test logic.
Instantiated once at server startup; all Panel browser sessions share this state.
This is the key architectural difference from LabSetup: the backend outlives any
single browser session, so closing/reopening the tab does not interrupt a running test.
"""
import csv
import hashlib
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Make the local Classes package importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))
from Classes.functions_class import bdps_control
from Classes.sensor_reader import SensorReader

LOG_ROOT = "/media/pi/LOGBATTEST"
# Realistic test-day profile (generator v2: 20 Hz, crank inrush, ocv_window,
# charge-neutral alternator segment, Event Type/Index columns).
PROFILE_FILE = Path(__file__).parent / "test_day_profile_new.csv"
LIVE_SENSOR_LOG = str(Path(__file__).parent / "live_sensor_output.csv")

# Identifies which physical battery produced a given run's logs. Update this
# before swapping the battery under test — it's stamped into every log
# filename and into a Battery_ID column on every BDPS log row, so runs from
# different physical batteries can never be silently pooled together in
# analysis. E.g. "old_ul18_12" tonight, "ul18_12_unit2" for the fresh battery.
BATTERY_ID = "ul18_12_unit2"

# SoC sweep parameters
SOC_SWEEP_POINTS = 10      # number of profile runs per sweep
SOC_STEP_CURRENT = 9.0    # A (C/2) discharge current for OCV step-downs

# Full plan: 20 blocks x 10 degradation cycles = 200 total degradation cycles.
# Per-block order: full charge -> SOH C/5 -> SoC sweep -> 10 degradation cycles.
FULL_PLAN_BLOCKS = 20
CYCLES_PER_BLOCK = 10
TOTAL_DEGRADATION_CYCLES = FULL_PLAN_BLOCKS * CYCLES_PER_BLOCK
FULL_PLAN_STAGE_ORDER = ["full_charge", "soh_c5", "soc_sweep", "degradation_cycles"]
CHECKPOINT_FILENAME = "_checkpoint.json"

# OCV-SoC reference table for Ultracell UL18-12 (12V VRLA) at 25°C.
# Each entry is (nominal_soc_pct, target_ocv_V).
# Values are resting terminal voltage after surface charge dissipates (~2 min).
# Derived from standard 12V VRLA electrochemistry; consistent with the
# datasheet's 1.80V/cell (10.80V) end-of-discharge and cycle-use charge
# voltage of 14.4V (2.40V/cell fully charged).
OCV_SOC_TABLE = [
    (100, 12.70),
    ( 90, 12.58),
    ( 80, 12.46),
    ( 70, 12.36),
    ( 60, 12.28),
    ( 50, 12.20),
    ( 40, 12.12),
    ( 30, 12.00),
    ( 20, 11.80),
    ( 10, 11.58),
]
OCV_REST_S  = 120   # seconds of rest before reading OCV (surface charge dissipation)
OCV_PULSE_S = 300   # seconds of CC discharge per pulse between OCV checks


class BatteryBackend:
    def __init__(self, bdps_ip: str = "192.168.2.130", simulate: bool = False):
        os.makedirs(LOG_ROOT, exist_ok=True)

        self.simulate = simulate
        self.battery_id = BATTERY_ID

        # Console log — persistent ring buffer across all browser sessions
        # (created before anything that might call self._log_raw)
        self._log_lines: deque = deque(maxlen=2000)
        self._log_lock = threading.Lock()
        self._log_raw(f"Active battery ID: {self.battery_id} (set BATTERY_ID in backend.py before swapping batteries).")

        # Hardware
        self.bdps = bdps_control(bdps_ip) if not simulate else None
        if self.bdps:
            self.bdps.connect()
        else:
            self._log_raw("Simulated BDPS active (no real hardware).")

        self.sensor_reader = SensorReader(
            simulate=simulate,
            port="/dev/ttyACM0",
            log_path=LIVE_SENSOR_LOG,
        )
        self.sensor_reader.start()
        self.sensor_reader.set_mode("idle")

        # Shared test state (read by all sessions via periodic callbacks)
        self.running_test: bool = False
        self.stop_event = threading.Event()
        self.test_thread: threading.Thread | None = None
        self.sensor_log_path: str = LIVE_SENSOR_LOG
        self.bdps_log_path: str | None = None
        self.last_capacity_ah: float | None = None

        # Test-day profile (auto-loaded from file)
        self.profile_path = PROFILE_FILE
        self.profile_df: pd.DataFrame | None = None
        self.profile_hash: str | None = None
        self._load_profile()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_raw(self, msg: str):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        with self._log_lock:
            self._log_lines.append(line)
        print(line)

    def log(self, msg: str):
        self._log_raw(msg)

    def get_log(self) -> str:
        with self._log_lock:
            return "\n".join(self._log_lines)

    # ------------------------------------------------------------------
    # Profile loading
    # ------------------------------------------------------------------

    def _load_profile(self):
        if not PROFILE_FILE.exists():
            self._log_raw(
                f"{PROFILE_FILE.name} not found at {PROFILE_FILE}. "
                "Place your profile CSV there and click 'Reload Profile'."
            )
            self.profile_df = None
            self.profile_hash = None
            return
        try:
            raw_bytes = PROFILE_FILE.read_bytes()
            self.profile_hash = hashlib.sha256(raw_bytes).hexdigest()

            df = pd.read_csv(PROFILE_FILE)
            cols_lower = {c.lower(): c for c in df.columns}

            # Only Time (s) and Current (A) are actual replay inputs.
            required = ["time (s)", "current (a)"]
            missing = [c for c in required if c not in cols_lower]
            if missing:
                self._log_raw(f"Profile CSV missing columns: {missing}. Expected at least: Time (s), Current (A).")
                self.profile_df = None
                return

            rename_map = {
                cols_lower["time (s)"]: "Time_s",
                cols_lower["current (a)"]: "Current_A",
            }
            # Voltage column is a lab-PC simulation preview only (never a
            # setpoint or measurement) — old generator called it "Voltage (V)",
            # new one calls it "V_expected_sim (V)". Kept only for the preview
            # plot; real voltage always comes from readVoltage().
            for v_col in ("v_expected_sim (v)", "voltage (v)"):
                if v_col in cols_lower:
                    rename_map[cols_lower[v_col]] = "Voltage_V"
                    break
            if "event type" in cols_lower:
                rename_map[cols_lower["event type"]] = "Event_Type"
            if "event index" in cols_lower:
                rename_map[cols_lower["event index"]] = "Event_Index"

            df = df.rename(columns=rename_map)
            if "Voltage_V" not in df.columns:
                df["Voltage_V"] = np.nan
            if "Event_Type" not in df.columns:
                df["Event_Type"] = ""
            if "Event_Index" not in df.columns:
                df["Event_Index"] = -1

            df = df.sort_values("Time_s").drop_duplicates("Time_s").reset_index(drop=True)

            if len(df) < 2:
                self._log_raw("Profile CSV has fewer than 2 data rows — not loaded.")
                self.profile_df = None
                return

            self.profile_df = df
            duration = df["Time_s"].iloc[-1] - df["Time_s"].iloc[0]
            i_min = df["Current_A"].min()
            i_max = df["Current_A"].max()
            dt_median = float(np.median(np.diff(df["Time_s"].to_numpy()))) if len(df) > 1 else 0.0
            self._log_raw(
                f"Test-day profile loaded from {PROFILE_FILE.name}: {len(df)} points, "
                f"duration {duration:.0f} s, dt={dt_median:.3f}s, "
                f"current range [{i_min:.2f}, {i_max:.2f}] A, "
                f"hash={self.profile_hash[:12]}."
            )
        except Exception as e:
            self._log_raw(f"Error loading profile: {e}")
            self.profile_df = None
            self.profile_hash = None

    # ------------------------------------------------------------------
    # Test control — public API (called from dashboard button handlers)
    # ------------------------------------------------------------------

    def stop_test(self):
        self.running_test = False
        self.stop_event.set()
        if self.bdps:
            try:
                self.bdps.setCurrent(0.0)
            except Exception:
                pass
            self.bdps.outputOff()
        self.log("Test stopped manually.")

    def start_full_charge(self):
        if not self._guard_start():
            return
        ts = _ts()
        self.bdps_log_path = self._path(f"full_charge_{ts}_bdps.csv")
        self._set_sensor_log(self._path(f"full_charge_{ts}_sensor.csv"))
        self._spawn(self._run_full_charge)

    def start_discharge_c5(self):
        if not self._guard_start():
            return
        ts = _ts()
        self.bdps_log_path = self._path(f"discharge_c5_{ts}_bdps.csv")
        self._set_sensor_log(self._path(f"discharge_c5_{ts}_sensor.csv"))
        self._spawn(self._run_discharge_c5)

    def start_discharge_c2(self):
        if not self._guard_start():
            return
        ts = _ts()
        self.bdps_log_path = self._path(f"discharge_c2_{ts}_bdps.csv")
        self._set_sensor_log(self._path(f"discharge_c2_{ts}_sensor.csv"))
        self._spawn(self._run_discharge_c2)

    def start_degradation_cycles(self):
        if not self._guard_start():
            return
        self._spawn(lambda: self._run_degradation_cycles_block(block_index=0, standalone=True))

    def start_single_profile_run(self):
        if not self._guard_start():
            return
        if self.profile_df is None:
            self.log("No profile loaded. Place test_day_profile.csv in LabSetupV2/ and reload.")
            self.running_test = False
            return
        ts = _ts()
        log_file = self._path(f"profile_single_{ts}_bdps.csv")
        self._set_sensor_log(self._path(f"profile_single_{ts}_sensor.csv"))
        self.bdps_log_path = log_file
        self._spawn(lambda: self._run_single_profile(log_file))

    def start_soc_sweep(self):
        if not self._guard_start():
            return
        if self.profile_df is None:
            self.log("No profile loaded. Place test_day_profile.csv in LabSetupV2/ and reload.")
            self.running_test = False
            return
        self.log(
            "SoC sweep: using OCV-based step-downs. "
            f"Targets from OCV_SOC_TABLE: {[v for _, v in OCV_SOC_TABLE]}V. "
            f"Rest per OCV read: {OCV_REST_S}s, pulse: {OCV_PULSE_S}s."
        )
        self._spawn(lambda: self._run_soc_sweep(block_index=0, standalone=True))

    def start_full_plan(self, blocks: int = FULL_PLAN_BLOCKS):
        if not self._guard_start():
            return
        if self.profile_df is None:
            self.log("No profile loaded. Cannot start full plan.")
            self.running_test = False
            return
        self._spawn(lambda: self._run_full_plan(blocks))

    # ------------------------------------------------------------------
    # Thread targets (private)
    # ------------------------------------------------------------------

    def _run_full_charge(self):
        self.log("Starting full battery charge (15.0 V / 5.4 A, max 4 h)...")
        try:
            self.sensor_reader.set_mode("charge")
            ah = self.bdps.charge_battery_cc_cv(
                target_voltage=15.0, max_current=5.4, duration_hours=4,
                log_path=self.bdps_log_path, stop_event=self.stop_event,
                battery_id=self.battery_id,
            )
            self.last_capacity_ah = ah
            self.log(f"Full charge complete: {ah:.3f} Ah delivered.")
        except Exception as e:
            self.log(f"Error during full charge: {e}")
        finally:
            self._finish()

    def _run_discharge_c5(self):
        self.log("Starting Discharge C/5 (3.6 A until 10.8 V)...")
        try:
            self.sensor_reader.set_mode("discharge")
            ah = self.bdps.discharge_cc_until_voltage(
                current=3.6, target_voltage=10.8,
                log_path=self.bdps_log_path, stop_event=self.stop_event,
                battery_id=self.battery_id,
            )
            self.last_capacity_ah = ah
            self.log(f"C/5 discharge complete. Measured capacity: {ah:.3f} Ah.")
        except Exception as e:
            self.log(f"Error during C/5 discharge: {e}")
        finally:
            self._finish()

    def _run_discharge_c2(self):
        self.log("Starting Discharge C/2 (9.0 A until 10.8 V)...")
        try:
            self.sensor_reader.set_mode("discharge")
            ah = self.bdps.discharge_cc_until_voltage(
                current=9.0, target_voltage=10.8,
                log_path=self.bdps_log_path, stop_event=self.stop_event,
                battery_id=self.battery_id,
            )
            self.last_capacity_ah = ah
            self.log(f"C/2 discharge complete. Measured capacity: {ah:.3f} Ah.")
        except Exception as e:
            self.log(f"Error during C/2 discharge: {e}")
        finally:
            self._finish()

    def _run_single_profile(self, log_file: str):
        self.log("Starting single test-day profile run at current SoC...")
        try:
            self.sensor_reader.set_mode("profile")
            self._run_profile_sync(log_file, sample_rate_hz=20.0)
            self.log("Single profile run complete.")
        except Exception as e:
            self.log(f"Error during single profile run: {e}")
        finally:
            self._finish()

    # ------------------------------------------------------------------
    # Block helpers (synchronous, called from test threads)
    # ------------------------------------------------------------------

    def _run_full_plan(self, blocks: int = FULL_PLAN_BLOCKS):
        try:
            resume = self._load_checkpoint()
            start_block = 1
            start_stage = FULL_PLAN_STAGE_ORDER[0]
            start_cycle = 1
            if resume:
                start_block = resume.get("block_index", 1)
                start_stage = resume.get("stage", FULL_PLAN_STAGE_ORDER[0])
                start_cycle = resume.get("cycle") or 1
                cycle_note = f", cycle {start_cycle}" if start_stage == "degradation_cycles" else ""
                self.log(
                    f"Resuming full plan from saved checkpoint: Block {start_block}, "
                    f"stage '{start_stage}'{cycle_note}. (Delete "
                    f"{self._checkpoint_path()} to force a restart from Block 1 instead.)"
                )
            else:
                self.log("No checkpoint found for this battery — starting full plan from Block 1.")

            self.log(
                f"Starting full plan: {blocks} blocks x {CYCLES_PER_BLOCK} degradation "
                f"cycles ({blocks * CYCLES_PER_BLOCK} total). Per block: full charge -> "
                f"SOH C/5 -> SoC sweep ({SOC_SWEEP_POINTS} runs) -> {CYCLES_PER_BLOCK} "
                f"degradation cycles. Battery ID: {self.battery_id}."
            )
            for b in range(start_block, blocks + 1):
                if self.stop_event.is_set():
                    self.log("Stop requested, aborting full plan.")
                    break

                stages = FULL_PLAN_STAGE_ORDER
                if b == start_block and start_stage in FULL_PLAN_STAGE_ORDER:
                    stages = FULL_PLAN_STAGE_ORDER[FULL_PLAN_STAGE_ORDER.index(start_stage):]

                cycle_lo = (b - 1) * CYCLES_PER_BLOCK + 1
                cycle_hi = b * CYCLES_PER_BLOCK

                if "full_charge" in stages:
                    self._save_checkpoint(b, "full_charge")
                    self.log(
                        f"=== Block {b}/{blocks} (cycles {cycle_lo}-{cycle_hi}/"
                        f"{TOTAL_DEGRADATION_CYCLES}): full charge ==="
                    )
                    if not self._run_full_charge_block(block_index=b):
                        if self.stop_event.is_set():
                            self.log(f"Stop requested during Block {b} full charge.")
                        else:
                            self._abort_full_plan(b, "full charge")
                        break
                    if self.stop_event.is_set():
                        break

                if "soh_c5" in stages:
                    self._save_checkpoint(b, "soh_c5")
                    self.log(f"=== Block {b}/{blocks}: SOH discharge C/5 ===")
                    if not self._run_discharge_c5_block(block_index=b):
                        if self.stop_event.is_set():
                            self.log(f"Stop requested during Block {b} SOH C/5 discharge.")
                        else:
                            self._abort_full_plan(b, "SOH C/5 discharge")
                        break
                    if self.stop_event.is_set():
                        break

                if "soc_sweep" in stages:
                    self._save_checkpoint(b, "soc_sweep")
                    self.log(f"=== Block {b}/{blocks}: SoC sweep ({SOC_SWEEP_POINTS} test-day runs) ===")
                    if not self._run_soc_sweep(block_index=b):
                        if self.stop_event.is_set():
                            self.log(f"Stop requested during Block {b} SoC sweep.")
                        else:
                            self._abort_full_plan(b, "SoC sweep")
                        break
                    if self.stop_event.is_set():
                        break

                if "degradation_cycles" in stages:
                    cyc_start = start_cycle if (b == start_block and start_stage == "degradation_cycles") else 1
                    self.log(f"=== Block {b}/{blocks} (cycles {cycle_lo}-{cycle_hi}/"
                             f"{TOTAL_DEGRADATION_CYCLES}): {CYCLES_PER_BLOCK} degradation cycles ===")
                    if not self._run_degradation_cycles_block(block_index=b, start_cycle=cyc_start):
                        if self.stop_event.is_set():
                            self.log(f"Stop requested during Block {b} degradation cycles.")
                        else:
                            self._abort_full_plan(b, "degradation cycles")
                        break

            else:
                self._clear_checkpoint()
                self.log(
                    f"Full plan finished — all {blocks} blocks complete "
                    f"({blocks * CYCLES_PER_BLOCK} degradation cycles total)."
                )
        except Exception as e:
            self._abort_full_plan(0, "full plan (unexpected exception)", detail=str(e))
        finally:
            self._finish()

    def _abort_full_plan(self, block_index: int, stage: str, detail: str = ""):
        """Log a highly visible, unambiguous halt message so a human can
        tell exactly where to resume testing. Also stops any lingering
        loops that check stop_event."""
        self.stop_event.set()
        self.log("!" * 70)
        self.log(
            f"FULL PLAN HALTED — Block {block_index}, stage: {stage}."
            + (f" Error: {detail}" if detail else "")
        )
        self.log(
            f"Battery ID: {self.battery_id}. To resume, restart the full plan and "
            f"manually skip ahead to Block {block_index}, stage '{stage}'."
        )
        self.log("!" * 70)

    def _run_full_charge_block(self, block_index: int) -> bool:
        ts = _ts()
        try:
            if self.stop_event.is_set():
                return False

            self._set_sensor_log(self._path(f"Block_{block_index:02d}_charge_full_sensor_{ts}.csv"))
            self.sensor_reader.set_mode("charge")
            self.bdps_log_path = self._path(f"Block_{block_index:02d}_charge_full_bdps_{ts}.csv")

            ah = self.bdps.charge_battery_cc_cv(
                target_voltage=14.4, max_current=5.4, duration_hours=4,
                log_path=self.bdps_log_path, stop_event=self.stop_event,
                battery_id=self.battery_id,
            )
            self.log(f"Block {block_index}: full charge complete — {ah:.3f} Ah delivered.")
            self._reset_sensor_log()
            return True
        except Exception as e:
            self.log(f"Error in full charge block {block_index}: {e}")
            return False

    def _run_degradation_cycles_block(self, block_index: int, standalone: bool = False,
                                       start_cycle: int = 1) -> bool:
        try:
            for cycle in range(start_cycle, CYCLES_PER_BLOCK + 1):
                if self.stop_event.is_set():
                    self.log("Stop requested, aborting degradation block.")
                    return False

                if not standalone:
                    self._save_checkpoint(block_index, "degradation_cycles", cycle=cycle)

                prefix = f"Block_{block_index:02d}_Degr_{cycle:02d}" if not standalone else f"Degr_{cycle:02d}"
                global_cycle = (block_index - 1) * CYCLES_PER_BLOCK + cycle
                cycle_label = (
                    f"cycle {global_cycle}/{TOTAL_DEGRADATION_CYCLES}" if not standalone
                    else f"cycle {cycle}/{CYCLES_PER_BLOCK}"
                )

                ts_discharge = _ts()
                self.log(f"Block {block_index}: degradation {cycle_label} — discharge.")

                self._set_sensor_log(self._path(f"{prefix}_discharge_sensor_{ts_discharge}.csv"))
                self.sensor_reader.set_mode("discharge")
                self.bdps_log_path = self._path(f"{prefix}_discharge_bdps_{ts_discharge}.csv")

                ah = self.bdps.discharge_cc_until_voltage(
                    current=9.0, target_voltage=10.8,
                    log_path=self.bdps_log_path, stop_event=self.stop_event,
                    battery_id=self.battery_id,
                )
                self.log(f"Block {block_index}: {cycle_label} discharge complete — {ah:.3f} Ah removed.")

                if self.stop_event.is_set():
                    return False

                ts_charge = _ts()
                self.log(f"Block {block_index}: degradation {cycle_label} — charge.")

                self._set_sensor_log(self._path(f"{prefix}_charge_sensor_{ts_charge}.csv"))
                self.sensor_reader.set_mode("charge")
                self.bdps_log_path = self._path(f"{prefix}_charge_bdps_{ts_charge}.csv")

                ah = self.bdps.charge_battery_cc_cv(
                    target_voltage=14.4, max_current=5.4, duration_hours=3,
                    log_path=self.bdps_log_path, stop_event=self.stop_event,
                    battery_id=self.battery_id,
                )
                self.log(f"Block {block_index}: {cycle_label} charge complete — {ah:.3f} Ah charged.")

            self._reset_sensor_log()
            return True
        except Exception as e:
            self.log(f"Error in degradation block {block_index}: {e}")
            return False
        finally:
            if standalone:
                self._finish()

    def _run_discharge_c5_block(self, block_index: int) -> bool:
        ts = _ts()
        try:
            if self.stop_event.is_set():
                return False

            self._set_sensor_log(self._path(f"Block_{block_index:02d}_SOH_C5_sensor_{ts}.csv"))
            self.sensor_reader.set_mode("discharge")
            self.bdps_log_path = self._path(f"Block_{block_index:02d}_SOH_C5_bdps_{ts}.csv")

            ah = self.bdps.discharge_cc_until_voltage(
                current=3.6, target_voltage=10.8,
                log_path=self.bdps_log_path, stop_event=self.stop_event,
                battery_id=self.battery_id,
            )
            self.last_capacity_ah = ah
            self.log(f"Block {block_index}: SOH C/5 complete — measured capacity {ah:.3f} Ah.")
            self._reset_sensor_log()
            return True
        except Exception as e:
            self.log(f"Error in SOH C/5 block {block_index}: {e}")
            return False

    def _run_soc_sweep(self, block_index: int, standalone: bool = False) -> bool:
        """
        Run the test-day profile at SOC_SWEEP_POINTS different SoC starting levels.

        Sequence:
          1. Charge to 100% CC/CV.
          2. Rest OCV_REST_S seconds; read and log initial OCV.
          3. Run profile at ~100% SoC, labelled by measured OCV.
          4. For each subsequent OCV target in OCV_SOC_TABLE:
               a. Step-down discharge in OCV_PULSE_S-second pulses at SOC_STEP_CURRENT A,
                  resting OCV_REST_S seconds between pulses to measure OCV.
               b. Stop pulse loop when OCV <= target or cutoff voltage reached.
               c. Run profile at the achieved OCV.
               d. If OCV was at or below cutoff, abort remaining steps.

        Log files are named by measured starting OCV rather than nominal SoC %,
        to match the field scenario where only OCV (CAN bus) is observable.
        """
        if self.profile_df is None:
            self.log("No profile loaded, cannot run SoC sweep.")
            return False

        b = f"Block_{block_index:02d}" if not standalone else "SoCsweep"

        def _ocv_label(ocv: float) -> str:
            """Format OCV as a safe filename fragment, e.g. 12.46 → OCV12p46V"""
            return f"OCV{ocv:.2f}V".replace(".", "p")

        try:
            # ── 1. Full charge ────────────────────────────────────────────────
            ts = _ts()
            self.log("SoC sweep: charging battery to 100%...")
            self._set_sensor_log(self._path(f"{b}_sweep_charge_sensor_{ts}.csv"))
            self.sensor_reader.set_mode("charge")
            self.bdps_log_path = self._path(f"{b}_sweep_charge_bdps_{ts}.csv")

            ah_charged = self.bdps.charge_battery_cc_cv(
                target_voltage=14.4, max_current=5.4, duration_hours=4,
                log_path=self.bdps_log_path, stop_event=self.stop_event,
                battery_id=self.battery_id,
            )
            self.log(f"SoC sweep: full charge complete — {ah_charged:.3f} Ah.")

            if self.stop_event.is_set():
                return False

            # ── 2. Initial OCV rest ───────────────────────────────────────────
            self.sensor_reader.set_mode("idle")
            self.log(f"SoC sweep: resting {OCV_REST_S}s for initial OCV measurement...")
            for _ in range(OCV_REST_S):
                if self.stop_event.is_set():
                    return False
                time.sleep(1)

            run_ocv = self.bdps.readVoltage()
            ref_soc, ref_ocv = OCV_SOC_TABLE[0]
            self.log(
                f"SoC sweep: initial OCV = {run_ocv:.3f}V "
                f"(ref {ref_ocv:.2f}V @ {ref_soc}% SoC from OCV table)."
            )

            # ── 3 + 4. Profile runs with OCV-based step-downs ─────────────────
            for i, (soc_nominal, ocv_target) in enumerate(OCV_SOC_TABLE):
                if self.stop_event.is_set():
                    return False

                lbl = _ocv_label(run_ocv)
                ts = _ts()
                self.log(
                    f"SoC sweep: [{i + 1}/{SOC_SWEEP_POINTS}] profile at "
                    f"OCV {run_ocv:.3f}V (nominal ~{soc_nominal}% SoC)."
                )

                self._set_sensor_log(
                    self._path(f"{b}_{lbl}_testday_sensor_{ts}.csv")
                )
                self.sensor_reader.set_mode("profile")
                self.bdps_log_path = self._path(f"{b}_{lbl}_testday_bdps_{ts}.csv")

                ok = self._run_profile_sync(self.bdps_log_path, sample_rate_hz=20.0)
                if not ok or self.stop_event.is_set():
                    return False

                self.log(f"SoC sweep: profile at OCV {run_ocv:.3f}V complete.")

                if i == SOC_SWEEP_POINTS - 1:
                    break  # no step-down after final run

                # ── step-down to next OCV target ──────────────────────────────
                _, next_ocv_target = OCV_SOC_TABLE[i + 1]
                next_soc_nominal = OCV_SOC_TABLE[i + 1][0]
                ts = _ts()
                self.log(
                    f"SoC sweep: stepping down from OCV {run_ocv:.3f}V "
                    f"→ target {next_ocv_target:.2f}V (~{next_soc_nominal}% SoC). "
                    f"Pulses: {OCV_PULSE_S}s discharge / {OCV_REST_S}s rest."
                )

                self._set_sensor_log(
                    self._path(f"{b}_{lbl}_stepdown_sensor_{ts}.csv")
                )
                self.sensor_reader.set_mode("discharge")
                step_log = self._path(f"{b}_{lbl}_stepdown_bdps_{ts}.csv")
                self.bdps_log_path = step_log

                removed, achieved_ocv = self.bdps.discharge_cc_to_ocv_target(
                    current=SOC_STEP_CURRENT,
                    target_ocv=next_ocv_target,
                    cutoff_voltage=10.8,
                    rest_s=OCV_REST_S,
                    pulse_s=OCV_PULSE_S,
                    log_path=step_log,
                    stop_event=self.stop_event,
                    battery_id=self.battery_id,
                    log_callback=self.log,
                )
                run_ocv = achieved_ocv if achieved_ocv is not None else next_ocv_target
                self.log(
                    f"SoC sweep: step-down complete — {removed:.3f} Ah removed, "
                    f"OCV = {run_ocv:.3f}V (target was {next_ocv_target:.2f}V)."
                )

                if run_ocv <= 10.8 + 0.3:
                    self.log(
                        f"SoC sweep: OCV at {run_ocv:.3f}V is at/near cutoff. "
                        "Battery empty — stopping sweep."
                    )
                    break

            self._reset_sensor_log()
            return True

        except Exception as e:
            self.log(f"Error in SoC sweep (block {block_index}): {e}")
            return False
        finally:
            if standalone:
                self._finish()

    # ------------------------------------------------------------------
    # Profile execution (runs synchronously in the calling test thread)
    # ------------------------------------------------------------------

    def _run_profile_sync(self, log_file: str, sample_rate_hz: float = 10.0) -> bool:
        """
        Execute self.profile_df on the BDPS at high frequency.
        Log format matches the BDPS logs used everywhere else.
        Returns True on clean completion, False if stopped or error.
        """
        df = self.profile_df
        if df is None:
            self.log("_run_profile_sync: no profile loaded.")
            return False

        try:
            df = df.copy()
            df["Time_s"] = df["Time_s"].astype(float)
            df["Current_A"] = df["Current_A"].astype(float)
            df = df.sort_values("Time_s").reset_index(drop=True)

            # Normalise time to start at 0
            df["Time_s"] = df["Time_s"] - df["Time_s"].iloc[0]
            times = df["Time_s"].to_numpy()
            currents = df["Current_A"].to_numpy()
            event_types = (
                df["Event_Type"].to_numpy() if "Event_Type" in df.columns
                else np.full(len(df), "", dtype=object)
            )
            event_indices = (
                df["Event_Index"].to_numpy() if "Event_Index" in df.columns
                else np.full(len(df), -1)
            )

            # Prefer the profile's own sample rate (it's uniform and
            # self-describing) over the caller-supplied sample_rate_hz, which
            # is only a fallback for degenerate single-row profiles.
            dt = float(np.median(np.diff(times))) if len(times) > 1 else 0.0
            sample_interval = dt if dt > 0 else (1.0 / sample_rate_hz)
            total_duration = float(times[-1]) + sample_interval

            # --- BDPS configuration ---
            # Allow both directions — profile contains charge phases (positive current)
            self.bdps.setPowerLimits(pos_power=1000.0, neg_power=1000.0)
            self.bdps.sendCommand("SOUR:FUNC CURR")

            max_neg = float(np.max(np.abs(currents[currents < 0]))) if np.any(currents < 0) else 0.0
            max_pos = float(np.max(currents[currents > 0])) if np.any(currents > 0) else 0.0
            if max(max_neg, max_pos) <= 0.0:
                self.log("Profile contains only zero current — nothing to run.")
                return False

            # Set once, covering the whole profile's range — avoids
            # re-tightening limits on every row of a fast decay (e.g. the
            # crank-pulse inrush spike), which only adds SCPI chatter without
            # changing what's safe to request.
            self.bdps.setCurrentLimits(pos_current=max_pos, neg_current=max_neg)
            self.bdps.sendCommand("SYST:SENS:REM OFF")
            time.sleep(1.0)

            def _polarity(v: float) -> str:
                if v == 0.0:
                    return "zero"
                return "neg" if v < 0.0 else "pos"

            current_index = 0
            current_setpoint = _snap_zero(float(currents[0]))
            current_polarity = _polarity(current_setpoint)

            if current_polarity == "zero":
                self.bdps.outputOff()
            else:
                self.bdps.setVoltage(15.0 if current_polarity == "pos" else 10.2)
                self.bdps.setCurrent(current_setpoint)
                self.bdps.outputOn()

            start_time = time.time()

            with open(log_file, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow([
                    "Timestamp", "Elapsed_s", "Voltage", "Current", "Mode",
                    "Event_Type", "Event_Index", "Profile_Hash", "Battery_ID",
                ])

                while True:
                    if self.stop_event is not None and self.stop_event.is_set():
                        self.log("Stop requested, stopping profile.")
                        break

                    loop_t0 = time.time()
                    elapsed = loop_t0 - start_time

                    if elapsed > total_duration:
                        break

                    # Advance profile step(s) — a while loop (not if) so a slow
                    # iteration can catch up across more than one CSV row.
                    while (
                        current_index + 1 < len(times)
                        and elapsed >= times[current_index + 1]
                    ):
                        current_index += 1
                        new_sp = _snap_zero(float(currents[current_index]))
                        new_polarity = _polarity(new_sp)

                        if new_polarity != current_polarity:
                            if new_polarity == "zero":
                                self.bdps.outputOff()
                            else:
                                self.bdps.setVoltage(15.0 if new_polarity == "pos" else 10.2)
                                self.bdps.setCurrent(new_sp)
                                self.bdps.outputOn()
                            current_polarity = new_polarity
                        elif new_sp != current_setpoint and new_polarity != "zero":
                            self.bdps.setCurrent(new_sp)

                        current_setpoint = new_sp

                    voltage = self.bdps.readVoltage()
                    current_val = self.bdps.readCurrent()

                    writer.writerow([
                        datetime.now().isoformat(),
                        round(elapsed, 3),
                        round(float(voltage), 3),
                        round(float(current_val), 3),
                        "CC",
                        event_types[current_index],
                        event_indices[current_index],
                        self.profile_hash or "",
                        self.battery_id,
                    ])
                    file.flush()  # OS buffers are sufficient; fsync would cap us at ~10 Hz on the Pi SD card

                    loop_dt = time.time() - loop_t0
                    sleep_t = sample_interval - loop_dt
                    if sleep_t > 0:
                        time.sleep(sleep_t)

            return not (self.stop_event is not None and self.stop_event.is_set())

        except Exception as e:
            self.log(f"Error during profile execution: {e}")
            return False
        finally:
            try:
                if self.bdps:
                    try:
                        self.bdps.setCurrent(0.0)
                    except Exception:
                        pass
                    self.bdps.outputOff()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _guard_start(self) -> bool:
        if self.running_test:
            self.log("Another test is already running.")
            return False
        if self.bdps is None and not self.simulate:
            self.log("BDPS not connected.")
            return False
        self.running_test = True
        self.stop_event.clear()
        return True

    def _finish(self):
        self.running_test = False

    def _spawn(self, target):
        self.test_thread = threading.Thread(target=target, daemon=True)
        self.test_thread.start()

    def _path(self, filename: str) -> str:
        """Build a path inside this battery's own subfolder under LOG_ROOT
        (created on first use), so files from different physical batteries
        can never end up mixed together in one directory."""
        battery_dir = os.path.join(LOG_ROOT, self.battery_id)
        os.makedirs(battery_dir, exist_ok=True)
        return os.path.join(battery_dir, filename)

    def _set_sensor_log(self, path: str):
        self.sensor_log_path = path
        self.sensor_reader.change_log_file(path)

    def _reset_sensor_log(self):
        self._set_sensor_log(LIVE_SENSOR_LOG)

    # ------------------------------------------------------------------
    # Full-plan checkpoint (per battery_id, lives alongside that battery's
    # own logs) -- lets "Full Plan" resume where a previous run left off
    # instead of always restarting at Block 1.
    # ------------------------------------------------------------------

    def _checkpoint_path(self) -> str:
        return self._path(CHECKPOINT_FILENAME)

    def _save_checkpoint(self, block_index: int, stage: str, cycle: int | None = None):
        try:
            with open(self._checkpoint_path(), "w") as f:
                json.dump({
                    "block_index": block_index,
                    "stage": stage,
                    "cycle": cycle,
                    "updated_at": datetime.now().isoformat(),
                }, f)
        except Exception as e:
            self.log(f"Warning: failed to save full-plan checkpoint: {e}")

    def _load_checkpoint(self) -> dict | None:
        path = self._checkpoint_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if data.get("stage") not in FULL_PLAN_STAGE_ORDER:
                return None
            return data
        except Exception as e:
            self.log(f"Warning: could not read full-plan checkpoint ({e}) -- starting from Block 1.")
            return None

    def _clear_checkpoint(self):
        path = self._checkpoint_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _snap_zero(val: float, threshold: float = 0.2) -> float:
    return 0.0 if abs(val) < threshold else val


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

_instance: BatteryBackend | None = None


def get_backend(simulate: bool = False) -> BatteryBackend:
    global _instance
    if _instance is None:
        _instance = BatteryBackend(simulate=simulate)
    return _instance
