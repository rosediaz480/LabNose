#!/bin/bash
cd /home/labnose/myenv/labnose
source /home/labnose/myenv/bin/activate

# TWILIO CREDENTIALS
export TWILIO_ACCOUNT_SID="your_account_sid_here"
export TWILIO_API_KEY_SID="your_api_key_sid_here"
export TWILIO_API_KEY_SECRET="your_api_key_secret_here"
export TWILIO_FROM_NUMBER="+1XXXXXXXXXX"
export TWILIO_TO_NUMBER="+1XXXXXXXXXX"
export CALL_REMINDER_INTERVAL_SECONDS="1800"

# Start the sensor script in the background
python /home/labnose/myenv/labnose/Controller/sensor.py &
SENSOR_PID=$!

# Start the web server in the foreground
python /home/labnose/myenv/labnose/View/web_server.py

# If web_server.py exits/stops, also stop the sensor script
kill $SENSOR_PID
