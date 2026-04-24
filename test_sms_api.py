#!/usr/bin/env python3
"""Test SMS API directly to debug the issue."""

import os
import urllib.request
import urllib.parse

from dotenv import load_dotenv


load_dotenv()

# SMS API configuration from .env
SMS_API_URL = os.getenv("SMS_API_URL", "http://157.245.105.5/index.php/sms/urlsms").strip()
SMS_API_KEY = os.getenv("SMS_API_KEY", "").strip()
SMS_SENDER = os.getenv("SMS_SENDER", "Dappto").strip() or "Dappto"
SMS_MESSAGE_TYPE = os.getenv("SMS_MESSAGE_TYPE", "TXT").strip() or "TXT"
SMS_RESPONSE = os.getenv("SMS_RESPONSE", "Y").strip() or "Y"
CREDIT_API_BASE_URL = os.getenv("SMS_CREDIT_API_BASE_URL", "http://127.0.0.1:4000").strip()
INTERNAL_API_KEY = os.getenv("X_INTERNAL_API_KEY", os.getenv("INTERNAL_API_KEY", "")).strip()

def test_sms_api():
    """Test SMS API directly."""
    if not SMS_API_KEY:
        print("SMS_API_KEY is not configured in environment/.env; skipping SMS API test.")
        return

    phone_number = "6394753866"  # Your test number
    message = "Test SMS from API - ignore this message"
    
    # Build SMS API request URL (same as in SMSNotificationService)
    api_url = (
        f"{SMS_API_URL}"
        f"?sender={urllib.parse.quote(SMS_SENDER)}"
        f"&numbers={phone_number}"
        f"&messagetype={urllib.parse.quote(SMS_MESSAGE_TYPE)}"
        f"&message={urllib.parse.quote(message)}"
        f"&response={urllib.parse.quote(SMS_RESPONSE)}"
        f"&apikey={urllib.parse.quote(SMS_API_KEY)}"
    )
    
    print(f"Testing SMS API...")
    redacted_url = api_url.replace(urllib.parse.quote(SMS_API_KEY), "***")
    print(f"URL: {redacted_url}")
    print(f"Phone: {phone_number}")
    print(f"Message: {message}")
    print()
    
    try:
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=10) as response:
            response_text = response.read().decode("utf-8")
            response_status = response.status
            
        print(f"✅ SMS API Response:")
        print(f"Status: {response_status}")
        print(f"Response: {response_text}")
        
        if response_status == 200:
            print("✅ SMS API is working!")
        else:
            print("❌ SMS API returned non-200 status")
            
    except Exception as exc:
        print(f"❌ SMS API Error: {exc}")

def test_credit_api():
    """Test credit API connectivity."""
    import requests

    if not INTERNAL_API_KEY:
        print("INTERNAL_API_KEY or X_INTERNAL_API_KEY is not configured in environment/.env; skipping Credit API test.")
        return
    
    doctor_id = 4  # Your test doctor ID
    appointment_id = 233  # Your test appointment ID
    
    print(f"Testing Credit API...")
    print(f"Base URL: {CREDIT_API_BASE_URL}")
    print(f"Doctor ID: {doctor_id}")
    print(f"Appointment ID: {appointment_id}")
    print()
    
    try:
        # Test credit status endpoint
        url = f"{CREDIT_API_BASE_URL}/api/internal/doctors/{doctor_id}/sms-status"
        headers = {
            "X-Internal-API-Key": INTERNAL_API_KEY,
            "Content-Type": "application/json"
        }
        
        print(f"Testing status endpoint: {url}")
        response = requests.get(url, headers=headers, timeout=5)
        
        print(f"Credit API Status Response:")
        print(f"Status: {response.status_code}")
        print(f"Raw Response: '{response.text}'")
        
        try:
            data = response.json()
            print(f"JSON Data: {data}")
        except Exception as e:
            print(f"JSON Parse Error: {e}")
            data = None
        
        # Test credit reservation endpoint
        url = f"{CREDIT_API_BASE_URL}/api/internal/doctors/{doctor_id}/sms-consume"
        payload = {"appointmentId": appointment_id}
        
        print(f"\nTesting consume endpoint: {url}")
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        try:
            data = response.json()
        except Exception as e:
            print(f"JSON Parse Error: {e}")
            data = None
        
        print(f"✅ Credit API Consume Response:")
        print(f"Status: {response.status_code}")
        print(f"Data: {data}")
        
    except Exception as exc:
        print(f"❌ Credit API Error: {exc}")

if __name__ == "__main__":
    print("=== SMS API Test ===")
    test_sms_api()
    print("\n" + "="*50 + "\n")
    print("=== Credit API Test ===")
    test_credit_api()
