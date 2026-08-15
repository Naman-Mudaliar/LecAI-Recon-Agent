"""
phase 1 check - make sure we can actually reach bods and that route 263
shows up in both the static timetable and the live feed before building
anything on top of it. run this straight from the repo root:

    python scripts/check_bods_connection.py

this is basically the thing the build brief asks for up front: "confirm
route 263 actually appears in both the live and static bods datasets...
this has not yet been verified - do it first". well, it has now.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config
from agent.sources import live_source, static_source


def check_static():
    print("downloading/checking static gtfs (north west region)...")
    static_source.ensure_static_gtfs_downloaded()

    route_id = static_source.find_route_id(config.ROUTE_SHORT_NAME)
    if route_id is None:
        print(f"  route {config.ROUTE_SHORT_NAME} NOT FOUND in static data")
        return False

    print(f"  found route_id={route_id} for route {config.ROUTE_SHORT_NAME}")
    if route_id != config.STATIC_ROUTE_ID:
        print(f"  heads up: this doesn't match the cached STATIC_ROUTE_ID in config.py ({config.STATIC_ROUTE_ID}) - the dataset may have been refreshed, update config")

    trips_path = config.STATIC_GTFS_EXTRACT_DIR / "trips.txt"
    trip_count = 0
    with open(trips_path, encoding="utf-8") as f:
        next(f)  # skip header
        for line in f:
            if line.startswith(route_id + ","):
                trip_count += 1
    print(f"  {trip_count} scheduled trips found for this route")
    return trip_count > 0


def check_live():
    print("querying live gtfs-rt feed for manchester...")
    vehicles = live_source.fetch_vehicle_positions()
    print(f"  {len(vehicles)} vehicle(s) currently reporting on route {config.ROUTE_SHORT_NAME}")
    for v in vehicles[:5]:
        print(f"    vehicle {v['vehicle_id']}  trip={v['trip_id'][:20]}...  "
              f"pos=({v['lat']:.5f}, {v['lon']:.5f})  ts={v['timestamp']}")
    return len(vehicles)


if __name__ == "__main__":
    if not config.BODS_API_KEY:
        print("no BODS_API_KEY set - copy .env.example to .env and fill it in")
        sys.exit(1)

    static_ok = check_static()
    live_count = check_live()

    print()
    print("summary:")
    print(f"  static data: {'ok' if static_ok else 'MISSING'}")
    print(f"  live data:   {live_count} vehicle(s) active right now")
    print("  (live count will be 0 outside service hours - route runs approx"
          " every 15 min daytime mon-sat, that doesn't mean its broken)")
