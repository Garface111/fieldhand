"""
FIELDHAND server launcher.
Starts FastAPI + creates an ngrok tunnel + wires Twilio webhook automatically.

Usage: python start_server.py
"""
import os
import sys
import time
import threading
import subprocess
from dotenv import load_dotenv

load_dotenv()

def start_uvicorn():
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "src.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--reload",
    ])

def main():
    print("\n=== FIELDHAND SERVER ===\n")

    # Start uvicorn in a background thread
    server_thread = threading.Thread(target=start_uvicorn, daemon=True)
    server_thread.start()
    print("Starting FastAPI server on port 8000...")
    time.sleep(3)

    # Start ngrok tunnel
    from pyngrok import ngrok as pyngrok
    ngrok_token = os.getenv("NGROK_AUTH_TOKEN", "")
    if ngrok_token:
        pyngrok.set_auth_token(ngrok_token)

    tunnel = pyngrok.connect(8000)
    public_url = tunnel.public_url
    # Force HTTPS
    if public_url.startswith("http://"):
        public_url = "https://" + public_url[7:]

    webhook_url = f"{public_url}/webhook/sms"
    print(f"Public URL:   {public_url}")
    print(f"SMS Webhook:  {webhook_url}")

    # Wire webhook into Twilio
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    phone = os.getenv("TWILIO_PHONE_NUMBER")

    if sid and token and phone:
        from twilio.rest import Client
        client = Client(sid, token)
        numbers = client.incoming_phone_numbers.list()
        for number in numbers:
            if number.phone_number == phone:
                client.incoming_phone_numbers(number.sid).update(
                    sms_url=webhook_url,
                    sms_method="POST",
                )
                print(f"Twilio webhook set on {phone}")
                break
    else:
        print("Twilio credentials not set — webhook not wired. Set them in .env")

    print(f"\nFIELDHAND is live. Text {phone} to talk to it.")
    print("Ctrl+C to stop.\n")

    # Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down.")
        pyngrok.disconnect(tunnel.public_url)
        pyngrok.kill()

if __name__ == "__main__":
    os.environ["PYTHONPATH"] = os.path.dirname(__file__)
    sys.path.insert(0, os.path.dirname(__file__))
    main()
