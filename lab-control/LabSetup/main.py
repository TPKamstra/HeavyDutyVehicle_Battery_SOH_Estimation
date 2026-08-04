from Classes import functions_class as cl
import threading
import time
import os 
from datetime import datetime

# Make a USB mount path
USB_MOUNT_PATH = "/media/pi/KINGSTON"
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

if not os.path.isdir(USB_MOUNT_PATH):
    raise RuntimeError("❌ USB stick not found or not mounted. Please check /media/pi/")

# Build file paths
emulation_log = os.path.join(USB_MOUNT_PATH, f"emulation_log_{timestamp}.csv")
charge_log = os.path.join(USB_MOUNT_PATH, f"charge_log_{timestamp}.csv")
discharge_log = os.path.join(USB_MOUNT_PATH, f"discharge_log_{timestamp}.csv")

# Set IPs of your two power supplies
IP_TESTER = "192.168.0.130"   # PSU-A: Charging/discharging side
IP_BATTERY = "192.168.0.160"  # PSU-B: Emulated battery

# Connect to both
tester = cl.bdps_control(IP_TESTER)
battery = cl.bdps_control(IP_BATTERY)

tester.connect()
battery.connect()

# Set Power Min and Max of both PSU's 
tester.setPowerLimits(500, 500)
battery.setPowerLimits(500, 500)

# Set current Min and Max of both PSU's
tester.setCurrentLimits(10, 10)
battery.setCurrentLimits(10, 10)

# 🟥 Start battery emulation on PSU-B
emulator_thread = threading.Thread(
    target=battery.emulate_battery_behavior,
    kwargs={
        'initial_soc': 0.2,
        'capacity_ah': 17.0,
        'internal_resistance': 0.05,
        'soc_cutoff': 0.05,
        'timestep': 1.0,
        'log_path': emulation_log
    }
)
# emulator_thread = threading.Thread(
#     target=battery.current_sink
# )
emulator_thread.start()

# Let the emulator stabilize
time.sleep(2)

# 🟦 Use existing class method to charge the emulated battery (10 minutes = 1/6 hour)
print("\n🔌 Charging with class method...\n")
charged_Ah = tester.charge_battery_cc_cv(
    battery_psu=battery,
    target_voltage=14.4,
    max_current=7.5,
    duration_hours=1/6,  # 10 minutes
    voltage_tolerance=0.05,
    log_path=charge_log
)
print(f"✅ Charge complete — estimated charged capacity: {charged_Ah:.3f} Ah")

# Wait before starting discharge
print("\n⏸️ Waiting 5 seconds before discharge...\n")
time.sleep(5)

# 🟦 Use existing class method to discharge the emulated battery (10 minutes)
print("🔋 Discharging with class method...\n")
discharged_Ah = tester.discharge_fixed_time(
    current=10.0,
    duration_minutes=10,
    voltage_cutoff=10.5,
    log_path=discharge_log
)
print(f"✅ Discharge complete — estimated discharged capacity: {discharged_Ah:.3f} Ah")

# Wait for emulator to finish
emulator_thread.join()

# Close connections
tester.close()
battery.close()

print("\n🔁 Full charge/discharge cycle completed using only class methods.")