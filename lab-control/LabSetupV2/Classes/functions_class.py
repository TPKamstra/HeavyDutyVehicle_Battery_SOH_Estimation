"""
BDPS control class — TCP/SCPI interface for the SM500-CP-90 bidirectional power supply.
Extended from LabSetup version with discharge_cc_fixed_ah for Coulomb-counting step-downs.
"""
import socket
import time
import csv
from datetime import datetime
import os


class bdps_control:
    def __init__(self, ip):
        self.SUPPLY_IP = ip
        self.SUPPLY_PORT = 8462
        self.BUFFER_SIZE = 128
        self.TIMEOUT_SECONDS = 2
        self.MAX_VOLT = 15
        self.MAX_CUR = 90
        self.MAX_POW = 1501
        self.supplySocket = None

    def connect(self):
        self.supplySocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.supplySocket.connect((self.SUPPLY_IP, self.SUPPLY_PORT))
        self.supplySocket.settimeout(self.TIMEOUT_SECONDS)
        print(f"Connected to BDPS at {self.SUPPLY_IP}")

    def sendAndReceiveCommand(self, msg):
        if not msg.strip().endswith("?"):
            raise ValueError("sendAndReceiveCommand must be used with query commands ending in '?'")
        msg += "\n"
        self.supplySocket.sendall(msg.encode("UTF-8"))
        chunks = []
        self.supplySocket.settimeout(2)
        while True:
            try:
                chunk = self.supplySocket.recv(self.BUFFER_SIZE)
                if not chunk:
                    break
                chunks.append(chunk.decode("UTF-8"))
                if "\n" in chunk.decode("UTF-8"):
                    break
            except socket.timeout:
                break
        return "".join(chunks).strip()

    def sendCommand(self, msg):
        self.supplySocket.sendall((msg + "\n").encode("UTF-8"))

    def setVoltage(self, volt):
        if 0 < volt <= self.MAX_VOLT:
            self.sendCommand(f"SOUR:VOLT {volt}")
        else:
            raise ValueError(f"Voltage {volt} out of range.")

    def setCurrent(self, cur):
        if -self.MAX_CUR <= cur <= self.MAX_CUR:
            self.sendCommand(f"SOUR:CURR {cur}")
        else:
            raise ValueError(f"Current {cur} out of range.")

    def setCurrentLimits(self, pos_current, neg_current):
        if 0 <= pos_current <= self.MAX_CUR:
            self.sendCommand(f"SOUR:CURR:POS {pos_current}")
        else:
            raise ValueError("Positive current out of range")
        if 0 <= neg_current <= self.MAX_CUR:
            self.sendCommand(f"SOUR:CURR:NEG {-abs(neg_current)}")
        else:
            raise ValueError("Negative current out of range")

    def setPowerLimits(self, pos_power, neg_power):
        if 0 <= pos_power <= self.MAX_POW:
            self.sendCommand(f"SOUR:POW: {pos_power}")
        else:
            raise ValueError("Positive power out of range")
        if 0 <= neg_power <= self.MAX_POW:
            self.sendCommand(f"SOUR:POW:NEG {-neg_power}")
        else:
            raise ValueError("Negative power out of range")

    def readVoltage(self):
        return float(self.sendAndReceiveCommand("MEAS:VOLT?"))

    def readCurrent(self):
        return float(self.sendAndReceiveCommand("MEAS:CURR?"))

    def outputOn(self):
        self.sendCommand("OUTP ON")

    def outputOff(self):
        self.sendCommand("OUTP OFF")

    def close(self):
        self.supplySocket.close()

    def discharge_cc_until_voltage(self, current, target_voltage, log_path="log.csv",
                                   min_runtime_s=5, timeout_s=6 * 3600, stop_event=None,
                                   battery_id=""):
        """Discharge at constant current until terminal voltage <= target_voltage."""
        self.setPowerLimits(0.0, 800.0)
        self.sendCommand("SOUR:FUNC CURR")
        self.setCurrentLimits(0.0, abs(current))
        time.sleep(0.5)

        self.setCurrent(-abs(current))
        self.setVoltage(target_voltage)
        self.sendCommand("SYST:SENS:REM OFF")
        time.sleep(0.5)

        self.outputOn()
        print("Output state:", self.sendAndReceiveCommand("OUTP?"))

        capacity_ah = 0.0
        start_time = time.time()
        ever_discharged = False

        try:
            with open(log_path, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode", "Battery_ID"])

                while True:
                    if stop_event is not None and stop_event.is_set():
                        print("Stop requested, stopping discharge.")
                        break

                    now = datetime.now().isoformat()
                    elapsed = time.time() - start_time
                    voltage = self.readVoltage()
                    current_val = self.readCurrent()

                    try:
                        writer.writerow([now, round(elapsed, 2), voltage, current_val, "CC", battery_id])
                        file.flush()
                        os.fsync(file.fileno())
                        print(f"BDPS Discharge: [{now}] | V: {voltage:.2f} V | I: {current_val:.2f} A")
                    except Exception as e:
                        print(f"Error writing log: {e}")

                    if abs(current_val) > 0.5:
                        ever_discharged = True

                    if voltage <= target_voltage:
                        time.sleep(0.1)
                        if self.readVoltage() <= target_voltage:
                            print("Target voltage reached, stopping discharge.")
                            break

                    if elapsed > timeout_s:
                        print("Timeout reached, stopping discharge.")
                        break

                    if elapsed > min_runtime_s and ever_discharged and abs(current_val) < 0.05:
                        print("Current collapsed, stopping discharge.")
                        break

                    capacity_ah += abs(current_val) / 3600.0
                    time.sleep(1)
        finally:
            # Guarantee the output is switched off even if an exception
            # (e.g. a socket error from readVoltage/readCurrent) propagates
            # out of the loop above.
            self.outputOff()

        if not ever_discharged:
            print("Warning: BDPS never sank current. Check power/current settings.")
        return round(capacity_ah, 3)

    def discharge_cc_fixed_ah(self, current, target_ah, cutoff_voltage=10.8,
                               log_path="discharge_log.csv", stop_event=None,
                               timeout_s=4 * 3600, battery_id=""):
        """
        Discharge at constant current until target_ah Ah has been removed (Coulomb counting).
        Also stops if voltage drops to cutoff_voltage or timeout is reached.
        Used to step down SoC between test-day profile runs.
        """
        self.setPowerLimits(0.0, 800.0)
        self.sendCommand("SOUR:FUNC CURR")
        self.setCurrentLimits(0.0, abs(current))
        time.sleep(0.5)

        self.setCurrent(-abs(current))
        self.setVoltage(cutoff_voltage)
        self.sendCommand("SYST:SENS:REM OFF")
        time.sleep(0.5)

        self.outputOn()
        print("Output state:", self.sendAndReceiveCommand("OUTP?"))

        capacity_ah = 0.0
        start_time = time.time()

        try:
            with open(log_path, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode", "Battery_ID"])

                while True:
                    if stop_event is not None and stop_event.is_set():
                        print("Stop requested, stopping step-down discharge.")
                        break

                    now = datetime.now().isoformat()
                    elapsed = time.time() - start_time
                    voltage = self.readVoltage()
                    current_val = self.readCurrent()

                    try:
                        writer.writerow([now, round(elapsed, 2), voltage, current_val, "CC", battery_id])
                        file.flush()
                        os.fsync(file.fileno())
                        print(
                            f"BDPS Step-down: [{now}] | V: {voltage:.2f} V | "
                            f"I: {current_val:.2f} A | Ah: {capacity_ah:.3f}/{target_ah:.3f}"
                        )
                    except Exception as e:
                        print(f"Error writing log: {e}")

                    capacity_ah += abs(current_val) / 3600.0

                    if capacity_ah >= target_ah:
                        print(f"Target capacity {target_ah:.3f} Ah reached ({capacity_ah:.3f} Ah removed).")
                        break

                    if voltage <= cutoff_voltage:
                        print(f"Cutoff voltage {cutoff_voltage} V reached at {capacity_ah:.3f} Ah.")
                        break

                    if elapsed > timeout_s:
                        print("Timeout reached.")
                        break

                    time.sleep(1)
        finally:
            self.outputOff()

        return round(capacity_ah, 3)

    def discharge_cc_to_ocv_target(self, current, target_ocv, cutoff_voltage=10.8,
                                    rest_s=120, pulse_s=300,
                                    log_path="stepdown_log.csv", stop_event=None,
                                    log_callback=print, battery_id=""):
        """
        Discharge at CC in pulses until the resting OCV drops to target_ocv.

        Sequence per iteration:
          1. Rest rest_s seconds with output off, read OCV.
          2. If OCV <= target_ocv: done (no unnecessary discharge).
          3. Else: discharge for pulse_s seconds at CC.
          4. Repeat.

        Note: terminal voltage DURING discharge will be lower than OCV due to
        the internal resistance voltage drop (V_terminal = OCV - I * R_int).
        On a degraded cell with high R_int this gap can be >200 mV at 3.6 A.
        Only the resting OCV (step 1) is compared to target_ocv.

        Returns (total_ah_removed, final_ocv).
        """
        self.setPowerLimits(0.0, 800.0)
        self.sendCommand("SOUR:FUNC CURR")
        self.setCurrentLimits(0.0, abs(current))
        time.sleep(0.5)
        self.setCurrent(-abs(current))
        self.setVoltage(cutoff_voltage)
        self.sendCommand("SYST:SENS:REM OFF")
        time.sleep(0.5)

        total_ah = 0.0
        final_ocv = None
        start_time = time.time()

        try:
            with open(log_path, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode", "Battery_ID"])

                while True:
                    if stop_event and stop_event.is_set():
                        break

                    # ── rest and measure OCV first ───────────────────────────
                    log_callback(f"Step-down: resting {rest_s}s for OCV measurement...")
                    for _ in range(rest_s):
                        if stop_event and stop_event.is_set():
                            break
                        time.sleep(1)

                    if stop_event and stop_event.is_set():
                        break

                    final_ocv = self.readVoltage()
                    now = datetime.now().isoformat()
                    elapsed = time.time() - start_time
                    try:
                        writer.writerow([now, round(elapsed, 2), final_ocv, 0.0, "OCV", battery_id])
                        file.flush()
                        os.fsync(file.fileno())
                    except Exception as e:
                        print(f"Error writing OCV entry: {e}")

                    log_callback(
                        f"Step-down: OCV = {final_ocv:.3f}V  "
                        f"(target <= {target_ocv:.3f}V, removed {total_ah:.3f} Ah so far)"
                    )

                    if final_ocv <= target_ocv:
                        log_callback(f"Step-down: target OCV reached — stopping discharge.")
                        break

                    # ── discharge pulse ──────────────────────────────────────
                    log_callback(
                        f"Step-down: OCV {final_ocv:.3f}V still above target {target_ocv:.3f}V "
                        f"— discharging {pulse_s}s at {current:.1f}A. "
                        f"(Terminal voltage will read lower than OCV during this pulse — that is normal.)"
                    )
                    self.outputOn()
                    pulse_end = time.time() + pulse_s
                    cutoff_hit = False

                    while time.time() < pulse_end:
                        if stop_event and stop_event.is_set():
                            break
                        now = datetime.now().isoformat()
                        elapsed = time.time() - start_time
                        voltage = self.readVoltage()
                        current_val = self.readCurrent()
                        total_ah += abs(current_val) / 3600.0

                        try:
                            writer.writerow([now, round(elapsed, 2), voltage, current_val, "CC", battery_id])
                            file.flush()
                            os.fsync(file.fileno())
                        except Exception as e:
                            print(f"Error writing step-down log: {e}")

                        print(
                            f"Step-down: V={voltage:.3f}V  I={current_val:.2f}A  "
                            f"Ah={total_ah:.3f}  target OCV={target_ocv:.3f}V"
                        )

                        if voltage <= cutoff_voltage:
                            log_callback(f"Step-down: cutoff voltage {cutoff_voltage}V reached.")
                            cutoff_hit = True
                            break

                        time.sleep(1)

                    self.outputOff()

                    if cutoff_hit or (stop_event and stop_event.is_set()):
                        # still do a final OCV read so caller has a value
                        if not (stop_event and stop_event.is_set()):
                            log_callback(f"Step-down: resting {rest_s}s after cutoff for final OCV...")
                            for _ in range(rest_s):
                                if stop_event and stop_event.is_set():
                                    break
                                time.sleep(1)
                            final_ocv = self.readVoltage()
                            now = datetime.now().isoformat()
                            elapsed = time.time() - start_time
                            try:
                                writer.writerow([now, round(elapsed, 2), final_ocv, 0.0, "OCV", battery_id])
                                file.flush()
                                os.fsync(file.fileno())
                            except Exception:
                                pass
                            log_callback(f"Step-down: final OCV after cutoff = {final_ocv:.3f}V")
                        break
        finally:
            self.outputOff()
        return round(total_ah, 3), final_ocv

    def discharge_fixed_time(self, current, duration_minutes, voltage_cutoff,
                             log_path="discharge_log.csv", stop_event=None, battery_id=""):
        """Discharge at a fixed current for a given duration or until voltage cutoff."""
        self.setPowerLimits(pos_power=0.0, neg_power=800.0)
        sink_current = -abs(current)
        self.sendCommand("SOUR:FUNC CURR")
        self.setCurrent(sink_current)
        self.setVoltage(15)
        self.setCurrentLimits(pos_current=0.0, neg_current=abs(current))
        self.sendCommand("SYST:SENS:REM OFF")
        time.sleep(2)
        self.outputOn()
        time.sleep(1)

        start_time = time.time()
        capacity_ah = 0.0

        try:
            with open(log_path, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode", "Battery_ID"])

                while True:
                    if stop_event is not None and stop_event.is_set():
                        break

                    voltage = self.readVoltage()
                    current_val = self.readCurrent()
                    elapsed = time.time() - start_time

                    try:
                        writer.writerow([datetime.now().isoformat(), round(elapsed, 2), voltage, current_val, "CC", battery_id])
                        file.flush()
                        os.fsync(file.fileno())
                    except Exception as e:
                        print(f"Error writing log: {e}")

                    if voltage > 15.2 or voltage < voltage_cutoff:
                        break
                    if elapsed > duration_minutes * 60:
                        break

                    capacity_ah += abs(current_val) / 3600.0
                    time.sleep(1)
        finally:
            self.outputOff()

        return round(capacity_ah, 3)

    def charge_battery_cc_cv(self, battery_psu=None, target_voltage=14.4, max_current=5.4,
                              duration_hours=3, voltage_tolerance=0.05,
                              log_path="charge_log.csv", stop_event=None, battery_id=""):
        """
        Charge the battery using CC/CV method.
        Returns total supplied capacity in Ah.
        """
        self.sendCommand("SOUR:FUNC VOLT")
        self.setVoltage(target_voltage)
        self.setCurrent(max_current)
        self.setCurrentLimits(pos_current=max_current, neg_current=0.0)
        self.setPowerLimits(pos_power=800.0, neg_power=0.0)
        self.sendCommand("SYST:SENS:REM OFF")
        self.outputOn()
        print("Charge CC/CV: configuration sent")
        time.sleep(2)

        start_time = time.time()
        capacity_ah = 0.0
        cv_mode = False
        ever_charged = False

        try:
            with open(log_path, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode", "Battery_ID"])

                while True:
                    if stop_event is not None and stop_event.is_set():
                        break

                    voltage = self.readVoltage()
                    current_val = self.readCurrent()
                    elapsed = time.time() - start_time
                    mode = "CV" if cv_mode else "CC"
                    now_iso = datetime.now().isoformat()

                    try:
                        writer.writerow([now_iso, round(elapsed, 2), voltage, current_val, mode, battery_id])
                        file.flush()
                        os.fsync(file.fileno())
                    except Exception as e:
                        print(f"Error writing log: {e}")

                    print(f"[{now_iso}] | V: {voltage:.2f} V | I: {current_val:.2f} A | Mode: {mode}")

                    if current_val > 0.5:
                        ever_charged = True

                    if not cv_mode and voltage >= target_voltage - voltage_tolerance:
                        cv_mode = True
                        print("Switched to CV mode")

                    if elapsed > duration_hours * 3600:
                        print("Max charge duration reached.")
                        break

                    if cv_mode and current_val < 0.3:
                        print("Current tapered below threshold in CV mode, stopping.")
                        break

                    capacity_ah += max(current_val, 0.0) / 3600.0
                    time.sleep(1)
        finally:
            self.outputOff()

        if not ever_charged:
            print("Warning: BDPS never sourced current. Verify voltage/current settings.")
        return round(capacity_ah, 3)
