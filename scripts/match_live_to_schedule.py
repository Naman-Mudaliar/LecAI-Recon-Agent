# phase 2 - pull the schedule for route 263 and match live vehicles
# against it using the nearest-stop approach (agent/matching.py). this is
# a one-shot demo, not the real reconciliation cycle yet - no ledger, no
# persisted "last matched stop" memory between runs, that comes in phase
# 3/4. for now we just match against stop_sequence > -1 (i.e. any stop on
# the trip) to prove the matching logic itself works against real live
# data.
#
# run from repo root:
#     python scripts/match_live_to_schedule.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, matching, time_utils
from agent.sources import live_source, static_source


def main():
    print("loading route schedule (builds + caches on first run, this can take a minute)...")
    schedule = static_source.load_route_schedule_cached()
    print(f"  {len(schedule)} trips in the schedule")

    print("fetching live vehicles...")
    vehicles = live_source.fetch_vehicle_positions()
    print(f"  {len(vehicles)} vehicle(s) live on route {config.ROUTE_SHORT_NAME}")

    if not vehicles:
        print("nothing running right now - try again during service hours (~15 min freq, mon-sat daytime)")
        return

    for v in vehicles:
        trip = schedule.get(v["trip_id"])
        print(f"\nvehicle {v['vehicle_id']}  (trip {v['trip_id'][:16]}..., heading {trip['trip_headsign'] if trip else '???'})")

        if trip is None:
            print("  no schedule found for this trip_id - might be a trip added today thats not in our cached schedule")
            continue
        if not trip["stops"]:
            print("  trip exists but has no stops in the schedule (weird, worth a look)")
            continue

        stop, distance = matching.match_vehicle_to_stop(trip["stops"], v["lat"], v["lon"])

        if stop is None:
            nearest_upcoming = trip["stops"][0]
            print(f"  no confident match (nearest candidate was {distance:.0f}m away, "
                  f"threshold is {config.STOP_MATCH_RADIUS_METERS}m) - bus is probably between stops")
            continue

        scheduled_epoch = time_utils.gtfs_time_to_epoch(v["start_date"], stop["arrival_time"])
        delay_seconds = v["timestamp"] - scheduled_epoch

        print(f"  matched stop: {stop['stop_name']} (seq {stop['stop_sequence']}), {distance:.0f}m away")
        print(f"  scheduled arrival {stop['arrival_time']}, observed delay: {delay_seconds:+d}s")


if __name__ == "__main__":
    main()
