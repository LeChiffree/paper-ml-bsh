import base64
import json
import urllib.request
from pathlib import Path

values = {}
for line in Path(".env").read_text(encoding="utf-8-sig").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

token = base64.b64encode(
    f"{values['PT_DASHBOARD_USER']}:{values['PT_DASHBOARD_PASSWORD']}".encode()
).decode()
request = urllib.request.Request(
    "http://127.0.0.1:8765/api/motors",
    headers={"Authorization": f"Basic {token}"},
)
with urllib.request.urlopen(request, timeout=20) as response:
    payload = json.load(response)
print(json.dumps(payload, ensure_ascii=False, indent=2))
