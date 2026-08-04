import time
from smbus2 import SMBus

ADS1115_ADDRESS = 0x48
ADS1115_CONVERSION_REG = 0x00
ADS1115_CONFIG_REG = 0x01

# A0 single-ended, 4.096V range, single-shot, 128SPS
CONFIG = 0xC183

def read_adc():
    with SMBus(1) as bus:
        # Write config to start single conversion
        bus.write_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONFIG_REG, [(CONFIG >> 8) & 0xFF, CONFIG & 0xFF])

        # Wait for conversion to complete
        time.sleep(0.01)

        # Read conversion result (2 bytes)
        data = bus.read_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONVERSION_REG, 2)
        raw_adc = data[0] << 8 | data[1]

        # Convert from twos complement
        if raw_adc > 0x7FFF:
            raw_adc -= 0x10000

        # ADS1115 full scale for ±4.096V = 2^15 = 32768
        voltage = (raw_adc / 32768.0) * 4.096
        return voltage

while True:
    voltage = read_adc()
    # Assume HASS 50-S: 2.5V = 0A, 12.5mV/A
    current = (voltage - 2.5) / 0.0125
    print(f"Sensor voltage: {voltage:.3f} V | Estimated current: {current:.2f} A")
    time.sleep(1)
