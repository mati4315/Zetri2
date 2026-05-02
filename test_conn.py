import requests
try:
    r = requests.get("https://www.mediafire.com", timeout=5, verify=False)
    print(f"Status: {r.status_code}")
except Exception as e:
    print(f"Error: {e}")
