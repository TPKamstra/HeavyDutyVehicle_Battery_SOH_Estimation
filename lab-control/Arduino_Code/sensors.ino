#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include "DFRobot_SHT20.h"

// Sensors
Adafruit_ADS1115 ads;
DFRobot_SHT20 sht20;

// Constants
const float ADC_LSB = 4.096 / 32767.0;     // GAIN_ONE range
const float CURRENT_ZERO_OFFSET = 2.5230;  // Your measured offset
const float CURRENT_SENSITIVITY = 0.0125;  // HASS 50-S = 12.5 mV/A
const float VOLTAGE_DIVIDER_SCALE = 5.0;   // Your measured scale

// Timing
unsigned long lastTempUpdate = 0;
const unsigned long TEMP_INTERVAL_MS = 1000;

// Cache
float temperature = 0.0;
float humidity = 0.0;

void setup() {
  Serial.begin(115200);  // Faster for higher sampling rate
  ads.setGain(GAIN_ONE);
  ads.begin(0x48);

  sht20.initSHT20();
  delay(100);
  sht20.checkSHT20();
}

void loop() {
  unsigned long now = millis();

  // Read ADC
  int16_t rawA0 = ads.readADC_SingleEnded(0);
  int16_t rawA3 = ads.readADC_SingleEnded(3);

  float voltageA0 = rawA0 * ADC_LSB;
  float voltageA3 = rawA3 * ADC_LSB;

  float current = (voltageA0 - CURRENT_ZERO_OFFSET) / CURRENT_SENSITIVITY;
  float realVoltage = voltageA3 * VOLTAGE_DIVIDER_SCALE;

  // Read temp/humidity once per second
  if (now - lastTempUpdate >= TEMP_INTERVAL_MS) {
    temperature = sht20.readTemperature();
    humidity = sht20.readHumidity();
    lastTempUpdate = now;
  }

  // Send data in CSV format for Pi: voltage,current,temp,humidity
  Serial.print(realVoltage, 2);
  Serial.print(",");
  Serial.print(current, 2);
  Serial.print(",");
  Serial.print(temperature, 2);
  Serial.print(",");
  Serial.println(humidity, 2);

  delay(50);  // ~20 Hz
}
