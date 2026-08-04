"""
Class to connect with the Bidirectional Power Supply (BDPS) and send commands to it.

The BDPS is a device that can both source and sink power. It is used to test the power consumption of a device under test (DUT).
The BDPS is controlled via a TCP socket with SCPI commands as found in the programming manual.
"""
import socket
import time
import csv
from datetime import datetime
import os

class bdps_control():
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
        self.supplySocket.settimeout(2)  # 2 seconds max wait
        while True:
            try:
                chunk = self.supplySocket.recv(self.BUFFER_SIZE)
                if not chunk:
                    break
                chunks.append(chunk.decode("UTF-8"))
                if "\n" in chunk.decode("UTF-8"):
                    break  # assume response is newline-terminated
            except socket.timeout:
                break
        return ''.join(chunks).strip()

    def sendCommand(self, msg):
        msg = msg + "\n"
        self.supplySocket.sendall(msg.encode("UTF-8"))

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
        """
        Set both positive and negative current limits.
        For bidirectional operation.
        """
        if 0 <= pos_current <= self.MAX_CUR:
            self.sendCommand(f"SOUR:CURR:POS {pos_current}")
        else:
            raise ValueError("Positive current out of range")

        if 0 <= neg_current <= self.MAX_CUR:
            self.sendCommand(f"SOUR:CURR:NEG {-abs(neg_current)}")  # Negative sign required
        else:
            raise ValueError("Negative current out of range")

    def setPowerLimits(self, pos_power, neg_power):
        """
        Sets the positive (source) and negative (sink) power limits for a bidirectional supply.
        """
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
                                   min_runtime_s=5, timeout_s=6*3600, stop_event=None):
        """
        Discharge battery at constant current until terminal voltage <= target_voltage.

        current          : requested discharge current in A (positive number)
        target_voltage   : cutoff voltage in V
        log_path         : csv log file
        min_runtime_s    : ignore 'current dropped' for the first seconds (ramp-up)
        timeout_s        : hard safety timeout
        """
        # Configure SM500-CP-90
        self.setPowerLimits(0.0, 800.0)                  # 800 W limit, adjust if needed
        self.sendCommand("SOUR:FUNC CURR")
        self.setCurrentLimits(0.0, abs(current))
        time.sleep(0.5)

        # Negative current = sink
        self.setCurrent(-abs(current))
        self.setVoltage(target_voltage)                  # lower voltage limit
        self.sendCommand("SYST:SENS:REM OFF")            # local sensing
        time.sleep(0.5)

        self.outputOn()
        print("Output state:", self.sendAndReceiveCommand("OUTP?"))  # should be '1'

        capacity_Ah = 0.0
        start_time = time.time()
        ever_discharged = False

        with open(log_path, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode"])

            while True:
                # Allow external stop from dashboard
                if stop_event is not None and stop_event.is_set():
                    print("Stop requested by user, stopping discharge.")
                    break

                now = datetime.now().isoformat()
                elapsed = time.time() - start_time

                voltage = self.readVoltage()
                current_val = self.readCurrent()
                try:
                    writer.writerow([now, round(elapsed, 2), voltage, current_val, "CC"])
                    file.flush()
                    os.fsync(file.fileno())

                    print(f"BDPS Discharge: [{now}] | V: {voltage:.2f} V | I: {current_val:.2f} A")

                except Exception as e:
                    print(f"Error writing to log file: {e}")

                # Track if we ever actually started discharging
                if abs(current_val) > 0.5:
                    ever_discharged = True

                # Main stop condition: cutoff voltage reached
                if voltage <= target_voltage:
                    # check again to avoid sensor glitches
                    time.sleep(0.1)
                    voltage_check = self.readVoltage()
                    if voltage_check <= target_voltage:
                        print("Target voltage reached, stopping discharge.")
                        break

                # Safety: timeout
                if elapsed > timeout_s:
                    print("Timeout reached, stopping discharge.")
                    break

                # Safety: current collapsed after having been active
                if elapsed > min_runtime_s and ever_discharged and abs(current_val) < 0.05:
                    print("Current collapsed, probably out of CC range or sink stopped, stopping.")
                    break

                # Integrate capacity as positive Ah
                capacity_Ah += abs(current_val) / 3600.0
                time.sleep(1)

        self.outputOff()

        if not ever_discharged:
            print("Warning: BDPS never really sank current. "
                  "Check that requested current * battery voltage exceeds the minimum sink power.")

        return round(capacity_Ah, 3)

    def discharge_fixed_time(self, current, duration_minutes, voltage_cutoff,
                             log_path="discharge_log.csv", stop_event=None):
        """
        Discharge battery at a fixed current for a given duration or until voltage drops below cutoff.
        """
        self.setPowerLimits(pos_power=0.0, neg_power=800.0)  # Set power limits for sink
        print(f"Setting power limits: pos=0.0 W, neg=800.0 W")
        sink_current = -abs(current)
        self.sendCommand("SOUR:FUNC CURR")      # set current control (sink) mode
        self.setCurrent(sink_current)

        battery_voltage = self.readVoltage()

        self.setVoltage(15)                   # Voltage ceiling for safety
        print("Set Voltage to 15 Volt")
        self.setCurrentLimits(pos_current=0.0, neg_current=abs(current))  # restrict to sink only

        self.sendCommand("SYST:SENS:REM OFF")
        print("Sended Command Remote sensing OFF")

        time.sleep(2)  # Allow settings to take effect

        self.outputOn()

        print(self.sendAndReceiveCommand("OUTP?"))       # Should return '1'
        time.sleep(1)

        start_time = time.time()
        capacity_Ah = 0.0
        print("Discharging in BDPS Class, save to: ", log_path)
        with open(log_path, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode"])

            while True:
                # Allow external stop from dashboard
                if stop_event is not None and stop_event.is_set():
                    print("Stop requested by user, stopping fixed-time discharge.")
                    break

                voltage = self.readVoltage()
                current_val = self.readCurrent()
                now = time.time()
                elapsed = now - start_time
                mode = 'CC'

                try:
                    writer.writerow([datetime.now().isoformat(), round(elapsed, 2), voltage, current_val, mode])
                    file.flush()  # ensures the data is written to disk
                    os.fsync(file.fileno())  # ensures flush also hits disk, not just OS buffer
                    print(f"BDPS Discharge: [{datetime.now().isoformat()}] | V: {round(voltage,2)} V | I: {round(current_val,2)} A")

                except Exception as e:
                    print(f"Error writing to log file: {e}")

                # for safety
                if voltage > 15.2:
                    print("Voltage over limit, break.")
                    break

                if voltage < voltage_cutoff:
                    print("Voltage cutoff reached.")
                    break

                if elapsed > duration_minutes * 60:
                    print("Max discharge time reached.")
                    break

                # Integrate current to estimate capacity as positive Ah
                capacity_Ah += abs(current_val) / 3600.0

                time.sleep(1)  # Log every second

        self.outputOff()
        return round(capacity_Ah, 3)

    def getPowerLimits(self):
        pos = self.sendAndReceiveCommand("SOUR:POW:POS?")
        neg = self.sendAndReceiveCommand("SOUR:POW:NEG?")
        return float(pos), float(neg)

    def charge_battery_cc_cv(
            self,
            battery_psu=None,
            target_voltage=14.4,
            max_current=5.4,
            duration_hours=3,
            voltage_tolerance=0.05,
            log_path="charge_log.csv",
            stop_event=None
        ):
        """
        Charges the battery using CC/CV method:
        - Apply constant current up to the target voltage.
        - Then hold constant voltage and taper current.
        Stops after max duration, if current drops significantly, or if stop_event is set.

        Returns total supplied capacity in Ah (always positive).
        """

        # Configure source mode, similar structure to discharge functions
        self.sendCommand("SOUR:FUNC VOLT")
        self.setVoltage(target_voltage)
        self.setCurrent(max_current)

        # Only allow positive source current in normal operation
        self.setCurrentLimits(pos_current=max_current, neg_current=0.0)

        # Only allow positive power (source), no sink
        self.setPowerLimits(pos_power=800.0, neg_power=0.0)

        self.sendCommand("SYST:SENS:REM OFF")
        self.outputOn()

        print("Charge CC/CV: configuration sent")

        time.sleep(2)

        start_time = time.time()
        capacity_Ah = 0.0
        cv_mode = False
        ever_charged = False

        with open(log_path, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Timestamp", "Elapsed_s", "Voltage", "Current", "Mode"])

            while True:
                # Allow Stop button to interrupt but still return Ah
                if stop_event is not None and stop_event.is_set():
                    print("Stop requested by user, stopping charge.")
                    break

                voltage = self.readVoltage()
                current_val = self.readCurrent()
                elapsed = time.time() - start_time
                mode = "CV" if cv_mode else "CC"

                now_iso = datetime.now().isoformat()

                try:
                    writer.writerow([now_iso, round(elapsed, 2), voltage, current_val, mode])
                    file.flush()
                    os.fsync(file.fileno())
                except Exception as e:
                    print(f"Error writing to log file: {e}")

                print(f"[{now_iso}] | V: {round(voltage, 2)} V | I: {round(current_val, 2)} A | Mode: {mode}")

                # Track if we actually started charging
                if current_val > 0.5:
                    ever_charged = True

                # CC to CV switch
                if not cv_mode and voltage >= target_voltage - voltage_tolerance:
                    cv_mode = True
                    print("Switched to CV mode")

                # Stop criteria:
                # a) absolute max time
                if elapsed > duration_hours * 3600:
                    print("Max charge duration reached.")
                    break

                # b) in CV mode, current has tapered below threshold
                if cv_mode and current_val < 0.3:
                    print("Current tapered below threshold in CV mode, stopping.")
                    break

                # Integrate capacity as positive Ah
                capacity_Ah += max(current_val, 0.0) / 3600.0

                time.sleep(1)

        self.outputOff()

        if not ever_charged:
            print("Warning: BDPS never really sourced current. Verify voltage/current settings.")

        return round(capacity_Ah, 3)

    def current_sink(self):
        print("Current sink mode")
        self.sendCommand("SOUR:FUNC CURR")  # Current sink mode
        desired_sink_current = 5
        self.setCurrent(desired_sink_current)  # e.g. 5A

    def emulate_battery_behavior(self,
                                initial_soc=1.0,
                                capacity_ah=17.0,
                                internal_resistance=0.05,
                                soc_cutoff=0.05,
                                timestep=1.0,
                                log_path="battery_emulator_log.csv"):
        soc = initial_soc
        elapsed = 0
        self.setCurrent(30.0)  # Large limit to allow current flow
        self.outputOn()

        print("🔋 Battery emulation (passive) started...")

        with open(log_path, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Time_s", "SOC", "Voltage", "Current"])

            while soc > soc_cutoff:
                try:
                    voltage = self.readVoltage()
                    current = self.readCurrent()

                    # Update SoC
                    soc -= (current * timestep / 3600) / capacity_ah
                    soc = max(0.0, soc)
                    elapsed += timestep

                    writer.writerow([round(elapsed, 1), round(soc, 4), round(voltage, 3), round(current, 3)])

                    print(f"[{elapsed}s] SOC: {round(soc,3)} | V: {round(voltage,2)} V | I: {round(current,2)} A")
                    time.sleep(timestep)

                except Exception as e:
                    print(f"⚠️ Error during emulation: {e}")
                    break

        self.outputOff()
        print("🛑 Battery emulation stopped (SOC below cutoff or error).")

    def _open_circuit_voltage(self, soc):
        """
        Approximate a lead-acid 12V battery OCV vs SoC curve.
        You can replace this with real data later.
        """
        soc = max(0.0, min(1.0, soc))  # Clamp between 0 and 1

        # Very simplified 12V OCV curve for lead-acid
        return (
            10.5 +                # Cutoff voltage
            2.1 * soc             # Simple linear slope to ~12.6V at full charge
        )

    def test_draw_70A_for_3s(self):
        print("=== BDPS 70 A SINK TEST START ===")

        try:
            print("Setting power limits pos=0, neg=1000")
            self.sendCommand("SOUR:FUNC CURR")
            self.setPowerLimits(pos_power=0.0, neg_power=1000.0)

            print("Setting current limits pos=0, neg=80")
            self.setCurrentLimits(pos_current=0.0, neg_current=80.0)

            print("Setting voltage limit to 10.8")
            self.setVoltage(10.8)
            self.sendCommand("SYST:SENS:REM OFF")
            time.sleep(0.5)

            print("Output ON")
            self.outputOn()

            print("Requesting -70 A")
            self.setCurrent(-70.0)

            for i in range(3):
                v = self.readVoltage()
                i_now = self.readCurrent()
                print(f"[{i+1}] V={v:.2f} I={i_now:.2f}")
                time.sleep(1)

            print("Returning to 0 A")
            self.setCurrent(0.0)
            time.sleep(0.5)

        except Exception as e:
            print(f"ERROR DURING 70A TEST: {e}")

        finally:
            print("Output OFF")
            self.outputOff()
            print("=== BDPS 70 A SINK TEST END ===")

    def sink_test_high_current(
        self,
        current=50.0,
        duration_s=3.0,
        safety_neg_power_w=1500.0
    ):
        """
        Try to sink a high current from the battery for a short time,
        respecting the SM500 power and system limits.

        current      : desired sink current in A (positive number)
        duration_s          : duration in seconds
        safety_neg_power_w  : negative power limit in W (must be <= HW capability)
        """

        print("=== BDPS HIGH CURRENT SINK TEST START ===")
        # 1. Configure source in current control, sink only
        try:
            # Configure SM500-CP-90
            self.setPowerLimits(0.0, 1500.0)                  # 800 W limit, adjust if needed
            self.sendCommand("SOUR:FUNC CURR")
            self.setCurrentLimits(0.0, abs(current))
            time.sleep(0.5)

            # Negative current = sink
            self.setCurrent(-abs(current))
            self.setVoltage(10.2)                  # lower voltage limit
            self.sendCommand("SYST:SENS:REM OFF")            # local sensing
            time.sleep(0.5)


            # 3. Determine current that respects power limit at actual battery voltage
            v_now = self.readVoltage()
            if v_now <= 0.1:
                print(f"Measured voltage {v_now:.2f} V is too low, abort.")
                return

            self.outputOn()
            self.setCurrent(-abs(current))

            # 4. Hold for duration_s, log a few samples
            t0 = time.time()
            n = 0
            while time.time() - t0 < duration_s:
                n += 1
                v = self.readVoltage()
                i = self.readCurrent()
                print(f"[{n}] V={v:.2f} V  I={i:.2f} A")
                time.sleep(0.5)

        except Exception as e:
            print(f"Error during high current sink test: {e}")
        finally:
            try:
                print("Returning to 0 A and switching output OFF")
                self.setCurrent(0.0)
            except Exception:
                pass
            self.outputOff()
            print("=== BDPS HIGH CURRENT SINK TEST END ===")


def main():
    bdps_ip = "192.168.0.130"

    # Desired limits
    pos_power_limit = 800.0   # for source (charging)
    neg_power_limit = 500.0   # for sink (discharging)

    bdps = bdps_control(bdps_ip)
    bdps.connect()

    print("Switching to VOLT mode to set POS power limit...")
    bdps.sendCommand("SOUR:FUNC VOLT")
    time.sleep(1)

    # Try setting POS power limit if supported (won't throw error if ignored)
    try:
        bdps.sendCommand(f"SOUR:POW: {pos_power_limit}")
        print(f"✔️ Set SOUR:POW: {pos_power_limit} W")
    except Exception as e:
        print(f"⚠️ Failed to set POS limit: {e}")

    print("Switching to CURR mode to set NEG power limit...")
    bdps.sendCommand("SOUR:FUNC CURR")
    time.sleep(1)

    try:
        bdps.sendCommand(f"SOUR:POW:NEG {-neg_power_limit}")
        print(f"✔️ Set SOUR:POW:NEG -{neg_power_limit} W")
    except Exception as e:
        print(f"⚠️ Failed to set NEG limit: {e}")

    print("ℹ️ Done. Close manually if needed.")
    bdps.close()


if __name__ == "__main__":
    main()
