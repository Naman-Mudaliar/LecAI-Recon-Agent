"""
warehouse source: bods static gtfs timetable, published in advance by
human schedulers - genuinely independent of the live feed, built on a
totally different pipeline (see agent/sources/live_source.py for the
contrast).

bods publishes this in two shapes: TransXChange xml per-operator (the
native uk format), and a converted bulk GTFS zip per english region,
which is the one we actually want since it comes with normal
trips.txt/stop_times.txt/calendar.txt files instead of a pile of xml.
greater manchester is in the "north_west" region file.

note the region zip is not small - stop_times.txt alone is something
like 470mb uncompressed for the whole north west, so we download+cache
it once locally (gitignored, never committed) rather than re-fetching it
every cycle.
"""

import zipfile

import requests

from agent import config

GTFS_DOWNLOAD_URL = f"{config.BASE_URL}/timetable/download/gtfs-file/{config.GTFS_REGION}/"


def ensure_static_gtfs_downloaded(force=False):
    """downloads and unzips the region gtfs file if we don't already have
    it. returns the path it extracted to. doesn't re-download every time
    you run something, just checks if it's already there."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    if force or not config.STATIC_GTFS_ZIP_PATH.exists():
        resp = requests.get(
            GTFS_DOWNLOAD_URL,
            params={"api_key": config.BODS_API_KEY},
            timeout=120,
        )
        resp.raise_for_status()
        config.STATIC_GTFS_ZIP_PATH.write_bytes(resp.content)

    if force or not config.STATIC_GTFS_EXTRACT_DIR.exists():
        config.STATIC_GTFS_EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(config.STATIC_GTFS_ZIP_PATH) as z:
            z.extractall(config.STATIC_GTFS_EXTRACT_DIR)

    return config.STATIC_GTFS_EXTRACT_DIR


def find_route_id(route_short_name):
    """looks up the internal bods route_id for a human readable route
    number like "263". routes.txt is small (a few thousand rows) so we
    can just read it straight, unlike stop_times.txt which needs to be
    streamed."""
    routes_path = config.STATIC_GTFS_EXTRACT_DIR / "routes.txt"
    with open(routes_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        short_name_idx = header.index("route_short_name")
        id_idx = header.index("route_id")
        for line in f:
            fields = _parse_csv_line(line)
            # route_short_name is quoted in this file e.g. "263"
            if fields[short_name_idx].strip('"') == route_short_name:
                return fields[id_idx]
    return None


def _parse_csv_line(line):
    """dumb but works csv split - good enough for these gtfs files since
    the only quoted fields we care about dont contain commas themselves.
    would use the csv module for anything less predictable than this."""
    return line.strip().split(",")
