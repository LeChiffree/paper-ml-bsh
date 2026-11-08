import hashlib
from pathlib import Path

values = {}
for line in Path(".env").read_text(encoding="utf-8-sig").splitlines():
    if line and not line.lstrip().startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

for key in (
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_FUTURES_10X_API_KEY",
    "BINANCE_FUTURES_10X_API_SECRET",
):
    value = values.get(key, "")
    print(key, len(value), hashlib.sha256(value.encode()).hexdigest()[:16] if value else "missing")
