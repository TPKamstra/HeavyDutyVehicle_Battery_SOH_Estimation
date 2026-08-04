import time
from smbus2 import SMBus

ADS1115_ADDRESS = 0x48
ADS1115_CONVERSION_REG = 0x00
ADS1115_CONFIG_REG = 0x01

# ADS1115 config values
CONFIG_A0 = 0x8483  # AIN0, 4.096V, single-shot
CONFIG_A3 = 0xE183  # AIN3, 4.096V, single-shot

# Constants
SENSOR_ZERO_VOLTAGE = 2.5      # V at 0 A
CURRENT_SENSITIVITY = 0.0125   # V/A (HASS 50-S)
VOLTAGE_SCALE_FACTOR = 7.5     # Adjust if needed

def read_adc(config):
    with SMBus(1) as bus:
        bus.write_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONFIG_REG,
                                 [(config >> 8) & 0xFF, config & 0xFF])
        time.sleep(0.01)
        data = bus.read_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONVERSION_REG, 2)
        raw = data[0] << 8 | data[1]
        if raw > 0x7FFF:
            raw -= 0x10000
        voltage = (raw / 32768.0) * 4.096
        return voltage

while True:
    try:
        # Read current sensor on A0
        voltage_a0 = read_adc(CONFIG_A0)
        current = (voltage_a0 - SENSOR_ZERO_VOLTAGE) / CURRENT_SENSITIVITY

        # Read voltage divider on A3
        voltage_a3 = read_adc(CONFIG_A3)
        system_voltage = voltage_a3 * VOLTAGE_SCALE_FACTOR

        # Display
        print(f"Current Sensor (A0): {voltage_a0:.3f} V → {current:.2f} A")
        print(f"Voltage Sensor (A3): {voltage_a3:.3f} V → {system_voltage:.2f} V")
        print("-" * 50)
        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(2)
