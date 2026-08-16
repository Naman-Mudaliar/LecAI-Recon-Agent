# turns a bus's raw lat/lon into a human address for the report ("99
# Oxford Street" reads a lot better than "53.4188, -2.3327"). uses OSM's
# Nominatim - free, no api key, but its usage policy requires a real
# identifying user-agent and caps at ~1 req/sec, so this stays strictly
# cosmetic: best-effort only, never blocks or fails a cycle. if the
# lookup errors, times out, or comes back with nothing usable, callers
# fall back to the raw coordinates - nothing in the six-field
# reconciliation depends on this working.

import time

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "route263-recon-agent/1.0 (build assessment; namanrm06@gmail.com)"

_MIN_INTERVAL_SECONDS = 1.0  # nominatim's usage policy: max 1 req/sec
_last_call = 0.0


def reverse_geocode(lat, lon):
    global _last_call
    wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18},
            headers={"User-Agent": USER_AGENT},
            timeout=5,
        )
        resp.raise_for_status()
        addr = resp.json().get("address", {})
    except (requests.RequestException, ValueError):
        return None

    road = addr.get("road")
    if not road:
        return None
    house_number = addr.get("house_number")
    return f"{house_number} {road}" if house_number else road
