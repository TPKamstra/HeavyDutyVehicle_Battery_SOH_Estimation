import time
from smbus2 import SMBus

ADS1115_ADDRESS = 0x48
ADS1115_CONVERSION_REG = 0x00
ADS1115_CONFIG_REG = 0x01

# A3 single-ended, 4.096V range, single-shot, 128SPS
CONFIG_A3 = 0xE183  # MUX[2:0] = 111 = AIN3

def read_adc(config):
    with SMBus(1) as bus:
        # Write config to start single conversion
        bus.write_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONFIG_REG,
                                 [(config >> 8) & 0xFF, config & 0xFF])
        time.sleep(0.01)
        data = bus.read_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONVERSION_REG, 2)
        raw_adc = data[0] << 8 | data[1]
        if raw_adc > 0x7FFF:
            raw_adc -= 0x10000
        voltage = (raw_adc / 32768.0) * 4.096
        return voltage

while True:
    measured_voltage = read_adc(CONFIG_A3)
    actual_voltage = measured_voltage * 7.5  # Adjust this factor if needed
    print(f"ADC A3 Voltage: {measured_voltage:.3f} V → Real Voltage: {actual_voltage:.2f} V")
    time.sleep(1)
