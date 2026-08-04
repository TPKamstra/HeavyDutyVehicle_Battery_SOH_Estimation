import serial
import time

# Adjust if needed — check with: ls /dev/ttyUSB*
SERIAL_PORT = "/dev/ttyACM0"
BAUDRATE = 9600

try:
    ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
    time.sleep(2)  # Wait for Arduino to reset
except Exception as e:
    print("Error opening serial port:", e)
    exit(1)

print("Reading Arduino temperature/humidity via serial...\n")

while True:
    try:
        line = ser.readline().decode("utf-8").strip()
        if line:
            print(f"From Arduino: {line}")
    except Exception as e:
        print("Read error:", e)
        time.sleep(1)
