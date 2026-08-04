import threading
import time
import random
from collections import deque
from datetime import datetime
import serial
import os
import csv

_ROTATE_AT = 12000   # rows before rotation (~10 min at 20 Hz)
_KEEP_ROWS  = 2000   # rows to keep after rotation (~1.5 min of history)
_DATA_LOG_MAXLEN = 200   # in-memory ring buffer for get_latest() -- NOT the CSV file


class SensorReader:
    def __init__(self, simulate=True, port="/dev/ttyACM0", baudrate=115200, log_path="sensor_log.csv"):
        self.simulate = simulate
        self.port = port
        self.baudrate = baudrate
        self.running = False
        # Bounded ring buffer -- this used to be a plain list that grew
        # forever (one dict appended per sample at 20 Hz, never trimmed,
        # never read anywhere in the actual dashboard). Over ~12 days of
        # continuous running that grew to ~3.3 GB resident memory and got
        # OOM-killed by the kernel. get_latest() is the only consumer and
        # only ever wants the last few samples, so a small bounded deque
        # is sufficient and makes further unbounded growth impossible.
        self.data_log = deque(maxlen=_DATA_LOG_MAXLEN)
        self.thread = None
        self.soc = 1.0
        self.mode = "idle"  # idle, charge, discharge
        self.log_path = log_path
        self._row_count = 0

        # Initialize log file with header
        if not os.path.exists(self.log_path):
            with open(self.log_path, mode="w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["timestamp", "voltage", "current", "temperature", "humidity"])
                writer.writeheader()

        if not self.simulate:
            try:
                self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
                time.sleep(2)  # Wait for Arduino to reset
            except Exception as e:
                print(f"❌ Failed to initialize serial: {e}")
                self.simulate = True

    def change_log_file(self, log_path):
        """Change the log file path and reset the log."""
        self.log_path = log_path
        self._row_count = 0
        with open(self.log_path, mode="w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "voltage", "current", "temperature", "humidity"])
            writer.writeheader()
        print(f"🔄 Log file changed to: {self.log_path}")

    def _rotate_log(self):
        """Keep only the last _KEEP_ROWS rows so the live file stays small."""
        try:
            with open(self.log_path, "r", newline="") as f:
                rows = list(csv.reader(f))
            if len(rows) <= 1:
                return
            header = rows[0]
            tail = rows[max(1, len(rows) - _KEEP_ROWS):]
            with open(self.log_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(tail)
            self._row_count = len(tail)
        except Exception as e:
            print(f"⚠️ Log rotation failed: {e}")
            self._row_count = 0

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False

    def set_mode(self, mode):
        self.mode = mode

    def _run(self):
        if self.simulate:
            print("📡 Simulated sensor thread started.")
        else:
            print("🧪 Real sensor thread started.")

        while self.running:
            if self.simulate:
                if self.mode == "discharge":
                    self.soc -= 0.0005
                    current = random.uniform(25, 30)
                elif self.mode == "charge":
                    self.soc += 0.0003
                    current = -random.uniform(4.0, 5.5)
                else:
                    current = 0.0

                self.soc = max(0.0, min(1.0, self.soc))
                voltage = 10.5 + 2.3 * self.soc
                temperature = 25 + abs(current) * 0.15 + random.uniform(-0.2, 0.3)
                humidity = 50 + random.uniform(-5, 5)
            else:
                try:
                    line = self.serial.readline().decode("utf-8").strip()
                    parts = line.split(",")

                    if len(parts) != 4:
                        print(f"⚠️ Skipping malformed line: '{line}'")
                        continue

                    voltage = float(parts[0])
                    current = float(parts[1])
                    temperature = float(parts[2])
                    humidity = float(parts[3])

                except Exception as e:
                    print(f"❌ Serial read/parse error: {e}")
                    voltage, current, temperature, humidity = 0.0, 0.0, 0.0, 0.0

            entry = {
                "timestamp": datetime.now().isoformat(),
                "voltage": round(voltage, 2),
                "current": round(current, 2),
                "temperature": round(temperature, 1),
                "humidity": round(humidity, 1)
            }

            self.data_log.append(entry)

            try:
                with open(self.log_path, mode="a", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=entry.keys())
                    writer.writerow(entry)
                self._row_count += 1
            except Exception as e:
                print(f"⚠️ Failed to write to log file: {e}")

            if self._row_count >= _ROTATE_AT:
                self._rotate_log()

            if self.simulate:
                time.sleep(0.05)  # regulate to 20 Hz in simulate mode
            # In real mode readline() blocks at the Arduino's natural rate — no extra sleep needed

    def get_latest(self, n=20):
        # deque doesn't support slicing directly
        return list(self.data_log)[-n:]

# 🧪 Run directly: print sensor readings and log to file
if __name__ == "__main__":
    reader = SensorReader(simulate=False, port="/dev/ttyACM0", baudrate=115200, log_path="live_sensor_output.csv")
    reader.start()

    try:
        while True:
            latest = reader.get_latest(1)
            if latest:
                print(latest[0])
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping sensor reader...")
        reader.stop()
