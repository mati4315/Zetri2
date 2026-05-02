import requests
headers={"Range": "bytes=0-0", "User-Agent": "SVX-Pro/1.0"}
url = "https://www.mediafire.com" # Just a test, usually it's a download url
try:
    r = requests.get(url, headers=headers, timeout=5, verify=False, allow_redirects=True)
    print(f"Status: {r.status_code}")
    print(f"Headers: {r.headers.get('Content-Type')}")
except Exception as e:
    print(f"Error: {e}")
