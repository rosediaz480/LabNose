
import os
import time
import sqlite3
from datetime import datetime, timezone

import bme680
from gpiozero import PWMOutputDevice
from RPLCD.i2c import CharLCD
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_ads1x15.ads1x15 import Pin
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from twilio.base.exceptions import TwilioRestException

DB_FILE = "readings.db"

# -- voice alerts --
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_API_KEY_SID = os.environ.get("TWILIO_API_KEY_SID")
TWILIO_API_KEY_SECRET = os.environ.get("TWILIO_API_KEY_SECRET")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")
TWILIO_TO_NUMBER = os.environ.get("TWILIO_TO_NUMBER")
CALL_REMINDER_INTERVAL_SECONDS = int(os.environ.get("CALL_REMINDER_INTERVAL_SECONDS", 1800))

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET and TWILIO_FROM_NUMBER and TWILIO_TO_NUMBER:
    twilio_client = Client(TWILIO_API_KEY_SID, TWILIO_API_KEY_SECRET, TWILIO_ACCOUNT_SID)
else:
    print("[sensor] Twilio credentials not fully set - voice alerts are disabled.")

_last_call_placed_at = 0.0


def send_voice_alert(alert_level, iaq_score, mq135_voltage):
    global _last_call_placed_at
 
    if twilio_client is None:
        return

    now = time.time()
    if now - _last_call_placed_at < CALL_REMINDER_INTERVAL_SECONDS:
        return
 
    message = (
        f"Lab Nose Alert"
        f"This alert was recorded at {datetime.now().strftime('%I:%M %p')}. "
        f"The V O C score is {iaq_score:.0f} out of 100. "
        f"M Q 135 reading is {mq135_voltage:.2f} volts. "
        "Please check the laboratory or Lab Nose Dashboard."
    )
 
    twiml = VoiceResponse()
    twiml.say(message)
    # repeat once in case they pick up mid-sentence
    twiml.pause(length=2)
    twiml.say(message)
 
    try:
        twilio_client.calls.create(
            twiml=str(twiml),
            from_=TWILIO_FROM_NUMBER,
            to=TWILIO_TO_NUMBER,
        )
        _last_call_placed_at = now
    except TwilioRestException as e:
        print(f"[sensor] Twilio voice call failed: {e}")
 
 

#--end of sms alerts--

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            temp_f REAL,
            humidity REAL,
            pressure_kpa REAL,
            iaq_score REAL,
            mq135_voltage REAL,
            alert_level TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def log_reading(temp_f, humidity, pressure_kpa, iaq_score, mq135_voltage, alert_level):
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute(
            """INSERT INTO readings
               (timestamp, temp_f, humidity, pressure_kpa, iaq_score, mq135_voltage, alert_level)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, temp_f, humidity, pressure_kpa, iaq_score, mq135_voltage, alert_level),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[sensor] DB write failed: {e}")


# lcd
lcd = CharLCD(
    i2c_expander='PCF8574',
    address=0x27,
    port=1,
    cols=16,
    rows=2,
    charmap='A02',
    auto_linebreaks=False
)

lcd.clear()
lcd.write_string("Initializing")
time.sleep(2)

buzzer = PWMOutputDevice(17, initial_value=0, frequency=440)
buzzer.value = 0


# bme688 setup
sensor = bme680.BME680(bme680.I2C_ADDR_PRIMARY)

sensor.set_humidity_oversample(bme680.OS_2X)
sensor.set_pressure_oversample(bme680.OS_4X)
sensor.set_temperature_oversample(bme680.OS_8X)
sensor.set_filter(bme680.FILTER_SIZE_3)

sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
sensor.set_gas_heater_temperature(320)
sensor.set_gas_heater_duration(150)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_FILE = os.path.join(SCRIPT_DIR, "bme688_baseline.txt")
BURN_IN_TIME = 300  # 5 minutes
hum_baseline = 40.0  # 40% humidity, the ideal
hum_weighting = 0.25  # 25% humidity


def load_baseline():
    try:
        with open(BASELINE_FILE, "r") as f:
            return float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def save_baseline(value):
    with open(BASELINE_FILE, "w") as f:
        f.write(str(value))


gas_baseline = load_baseline()

if gas_baseline is None:
    burn_in_data = []

    lcd.clear()
    lcd.write_string("First run!")
    lcd.cursor_pos = (1, 0)
    lcd.write_string("Warming up 5m...")
    time.sleep(2)

    start_time = time.time()
    while time.time() - start_time < BURN_IN_TIME:
        if sensor.get_sensor_data() and sensor.data.heat_stable:
            burn_in_data.append(sensor.data.gas_resistance)
            time.sleep(1)
    gas_baseline = sum(burn_in_data[-50:]) / 50.0
    save_baseline(gas_baseline)

    lcd.clear()
    lcd.write_string("Baseline saved!")
    time.sleep(2)
else:
    lcd.clear()
    lcd.write_string("Baseline loaded.")
    time.sleep(2)


def celsius_to_fahrenheit(c):
    return (c * 9 / 5) + 32


def compute_iaq_score(gas_resistance, humidity):
    # returns the air quality score from 0-100 (higher equals better air quality) converted to the opposite later on.

    gas_offset = gas_baseline - gas_resistance
    hum_offset = humidity - hum_baseline

    if hum_offset > 0:
        hum_score = (100 - hum_baseline - hum_offset) / (100 - hum_baseline) * (hum_weighting * 100)
    else:
        hum_score = (hum_baseline + hum_offset) / hum_baseline * (hum_weighting * 100)

    if gas_offset > 0:
        gas_score = (gas_resistance / gas_baseline) * (100 - (hum_weighting * 100))
    else:
        gas_score = 100 - (hum_weighting * 100)

    return hum_score + gas_score
CALL_REMINDER_INTERVAL_SECONDS = int(os.environ.get("CALL_REMINDER_INTERVAL_SECONDS", 1800))

# MQ135 analog output to channel AO
i2c_bus = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c_bus)
mq135_channel = AnalogIn(ads, Pin.A0)

# MQ135 preheat period
MQ135_WARMUP_SECONDS = 60

# volt alert starting point
MQ135_ALERT_THRESHOLD = 3.400  # volts

time.sleep(MQ135_WARMUP_SECONDS)

lcd.clear()
lcd.write_string("Sensors Ready!")
time.sleep(2)


def main():
    init_db()

    try:
        while True:
            if not sensor.get_sensor_data():
                time.sleep(1)
                continue

            temp_c = sensor.data.temperature
            temp_f = celsius_to_fahrenheit(temp_c) + 4
            humidity = sensor.data.humidity - 4
            pressure = sensor.data.pressure / 10  # converted from hPa to kPa

            if sensor.data.heat_stable:
                gas_raw = sensor.data.gas_resistance  # in ohms
                iaq_score = 100 - compute_iaq_score(gas_raw, humidity)
            else:
                gas_raw = 0
                iaq_score = 0

            mq135_voltage = mq135_channel.voltage

            if mq135_voltage > MQ135_ALERT_THRESHOLD and iaq_score > 48:
                lcd.clear()
                lcd.write_string("VOC ALERT")
                lcd.cursor_pos = (1, 0)
                lcd.write_string(f"{iaq_score:.1f} / 100")
                buzzer.on()
                time.sleep(0.5)
                buzzer.off()
                time.sleep(2)

                send_voice_alert("VOC_ALERT_1", iaq_score, mq135_voltage)
                log_reading(temp_f, humidity, pressure, iaq_score, mq135_voltage, "VOC_ALERT_1")
 

            elif iaq_score > 48:
                lcd.clear()
                lcd.write_string("VOC ALERT")
                lcd.cursor_pos = (1, 0)
                lcd.write_string(f"{iaq_score:.1f} / 100")
                buzzer.on()
                time.sleep(0.5)
                buzzer.off()
                time.sleep(2)

                send_voice_alert("VOC_ALERT_2", iaq_score, mq135_voltage)
                log_reading(temp_f, humidity, pressure, iaq_score, mq135_voltage, "VOC_ALERT_2")

            else:
                lcd.clear()
                lcd.write_string(f"Temp: {temp_f:5.1f} F")
                lcd.cursor_pos = (1, 0)
                lcd.write_string(f"Hum: {humidity:5.1f}%")
                time.sleep(1)

                lcd.clear()
                lcd.write_string("Pressure:")
                lcd.cursor_pos = (1, 0)
                lcd.write_string(f"{pressure:7.1f} kPa")
                time.sleep(1)

                lcd.clear()
                lcd.write_string("VOC 0-100")
                lcd.cursor_pos = (1, 0)
                lcd.write_string(f"{iaq_score:.1f} / 100")
                time.sleep(3)

                log_reading(temp_f, humidity, pressure, iaq_score, mq135_voltage, "OK")

    except KeyboardInterrupt:
        lcd.clear()
        lcd.write_string("Stopped")
        time.sleep(1)
        lcd.close()


if __name__ == "__main__":
    main()
