"""HTTP probe without rendering sensitive failure response bodies."""
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:5000/health/db", timeout=3) as response:
    if response.status != 200:
        raise SystemExit(1)
