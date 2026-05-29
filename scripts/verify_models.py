#!/usr/bin/env python
"""Quick check that every configured Open-Meteo model id still resolves.

Run this occasionally (and after Open-Meteo announcements) to catch renamed
identifiers before they silently drop a model from the log.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from powwx import _netcompat, config as cfg  # noqa: E402

_netcompat.enable()


def main() -> int:
    models_cfg = cfg.load_models()
    url = models_cfg["api"]["forecast_url"]
    loc = cfg.load_locations()[0]
    ok = True
    for m in models_cfg["models"]:
        params = {
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "hourly": "temperature_2m",
            "models": m["id"],
            "forecast_days": 1,
        }
        r = requests.get(url, params=params, timeout=30)
        status = "OK  " if r.status_code == 200 else "FAIL"
        if r.status_code != 200:
            ok = False
        print(f"{status} {m['id']:24s} ({m['label']})  -> HTTP {r.status_code}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
