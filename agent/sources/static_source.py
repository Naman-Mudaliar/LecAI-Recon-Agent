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

import csv
import json
import zipfile

import requests

from agent import config

GTFS_DOWNLOAD_URL = f"{config.BASE_URL}/timetable/download/gtfs-file/{config.GTFS_REGION}/"

SCHEDULE_CACHE_PATH = config.DATA_DIR / f"route_{config.ROUTE_SHORT_NAME}_schedule.json"


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
            # route_short_name comes quoted in the raw file e.g. "263" but
            # csv.reader already strips that for us
            if fields[short_name_idx] == route_short_name:
                return fields[id_idx]
    return None


def _parse_csv_line(line):
    """turns out stop names in this data genuinely have commas in them
    (found this the hard way - "Sale, Marsland Road" style naming), so a
    plain .split(",") silently shifts every field after it and you get
    garbage. use the csv module properly instead, it handles quoted
    fields with embedded commas the way its supposed to."""
    return next(csv.reader([line]))


def _get_trips_for_route(route_id):
    """trips.txt is ~15mb, not huge, but still stream it rather than
    loading the whole thing since we only care about ~231 rows out of it."""
    trips_path = config.STATIC_GTFS_EXTRACT_DIR / "trips.txt"
    trips = {}
    with open(trips_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            fields = _parse_csv_line(line)
            if fields[idx["route_id"]] != route_id:
                continue
            trip_id = fields[idx["trip_id"]]
            trips[trip_id] = {
                "service_id": fields[idx["service_id"]],
                "direction_id": fields[idx["direction_id"]],
                "trip_headsign": fields[idx["trip_headsign"]],
            }
    return trips


def _load_stops():
    """stops.txt is a couple mb for the whole region, fine to just load
    it all into memory as a dict."""
    stops_path = config.STATIC_GTFS_EXTRACT_DIR / "stops.txt"
    stops = {}
    with open(stops_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            fields = _parse_csv_line(line)
            stops[fields[idx["stop_id"]]] = {
                "stop_name": fields[idx["stop_name"]],
                "stop_lat": float(fields[idx["stop_lat"]]),
                "stop_lon": float(fields[idx["stop_lon"]]),
            }
    return stops


def load_calendar():
    """calendar.txt -> service_id -> which weekdays it runs + the date
    range its valid for. python's weekday() is monday=0 so we keep that
    convention here too rather than gtfs's own column ordering."""
    calendar_path = config.STATIC_GTFS_EXTRACT_DIR / "calendar.txt"
    calendar = {}
    with open(calendar_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        idx = {name: i for i, name in enumerate(header)}
        weekday_cols = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for line in f:
            fields = _parse_csv_line(line)
            service_id = fields[idx["service_id"]]
            weekdays = {i for i, col in enumerate(weekday_cols) if fields[idx[col]] == "1"}
            calendar[service_id] = {
                "weekdays": weekdays,
                "start_date": fields[idx["start_date"]],
                "end_date": fields[idx["end_date"]],
            }
    return calendar


def load_calendar_dates():
    """calendar_dates.txt -> service_id -> {date: exception_type}.
    exception_type 1 = service added on this date, 2 = removed. this is
    what catches bank holidays and one-off schedule changes that the
    regular weekday pattern in calendar.txt doesn't cover."""
    path = config.STATIC_GTFS_EXTRACT_DIR / "calendar_dates.txt"
    exceptions = {}
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            fields = _parse_csv_line(line)
            service_id = fields[idx["service_id"]]
            date = fields[idx["date"]]
            exception_type = int(fields[idx["exception_type"]])
            exceptions.setdefault(service_id, {})[date] = exception_type
    return exceptions


def _stream_stop_times_for_trips(trip_ids):
    """the big one - stop_times.txt is ~470mb for the whole north west so
    we stream it line by line rather than loading it, and only keep rows
    whose trip_id we actually care about. takes maybe 20-30 seconds on a
    normal laptop, only needs to happen once since we cache the result
    after."""
    stop_times_path = config.STATIC_GTFS_EXTRACT_DIR / "stop_times.txt"
    by_trip = {trip_id: [] for trip_id in trip_ids}

    with open(stop_times_path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            # cheap check before the full parse, trip_id is always the
            # first field so we can bail early on most lines without
            # even splitting them
            trip_id = line[:line.index(",")]
            if trip_id not in by_trip:
                continue
            fields = _parse_csv_line(line)
            by_trip[trip_id].append({
                "stop_id": fields[idx["stop_id"]],
                "stop_sequence": int(fields[idx["stop_sequence"]]),
                "arrival_time": fields[idx["arrival_time"]],
                "departure_time": fields[idx["departure_time"]],
            })

    for trip_id in by_trip:
        by_trip[trip_id].sort(key=lambda s: s["stop_sequence"])
    return by_trip


def build_route_schedule(route_id):
    """pulls together trips + stop_times + stops into one structure:
    trip_id -> {service_id, direction_id, trip_headsign, stops: [...]}
    each stop in the list has its scheduled times AND lat/lon, so this is
    everything later stages need without touching the raw gtfs files
    again."""
    trips = _get_trips_for_route(route_id)
    stop_times_by_trip = _stream_stop_times_for_trips(set(trips.keys()))
    stops = _load_stops()

    schedule = {}
    for trip_id, trip_info in trips.items():
        stop_list = []
        for st in stop_times_by_trip.get(trip_id, []):
            stop_info = stops.get(st["stop_id"], {})
            stop_list.append({
                **st,
                "stop_name": stop_info.get("stop_name"),
                "stop_lat": stop_info.get("stop_lat"),
                "stop_lon": stop_info.get("stop_lon"),
            })
        schedule[trip_id] = {**trip_info, "stops": stop_list}
    return schedule


def load_route_schedule_cached(route_id=config.STATIC_ROUTE_ID, force_rebuild=False):
    """builds the route schedule once and caches it to json so we dont
    have to stream the 470mb stop_times file every single time we want to
    run the agent. delete the cache file (or pass force_rebuild) if the
    static data gets refreshed and you want to pick up changes."""
    if not force_rebuild and SCHEDULE_CACHE_PATH.exists():
        with open(SCHEDULE_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)

    schedule = build_route_schedule(route_id)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(SCHEDULE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)
    return schedule
