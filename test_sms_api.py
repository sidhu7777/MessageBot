#!/usr/bin/env python3
"""Test SMS API directly to debug the issue."""

import urllib.request
import urllib.parse

# SMS API configuration from .env
SMS_API_URL = "http://157.245.105.5/index.php/sms/urlsms"
SMS_API_KEY = "34645a-1d71a1-2ff799-ca100e-9cb8bc"
SMS_SENDER = "Dappto"
SMS_MESSAGE_TYPE = "TXT"
SMS_RESPONSE = "Y"

def test_sms_api():
    """Test SMS API directly."""
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
    print(f"URL: {api_url}")
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
    
    CREDIT_API_BASE_URL = "http://10.5.63.167:3000"
    SUPERADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEsImVtYWlsIjoic3VwZXJhZG1pbkB2aW5mb2NvbS5jb20iLCJyb2xlIjoiU1VQRVJfQURNSU4iLCJpYXQiOjE3NzY4NDYxMjgsImV4cCI6MTc3NzQ1MDkyOH0.Xo8wtuf_8LybKI0f0BYWt97UmhFqLkFGQ0CK78iRfR8"
    
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
            "Authorization": f"Bearer {SUPERADMIN_TOKEN}",
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
        data = response.json()
        
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