"""
phase 3 - run the disagreement checker against whatever's live on route
263 right now, for every field, for every vehicle. this is detection
only, no winner picked yet - just "here's what we observed live, here's
what the schedule says, do they disagree".

run from repo root:
    python scripts/check_disagreements.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, fields
from agent.sources import live_source, static_source
from agent.state import ObservationState


def main():
    print("loading schedule + calendar...")
    schedule = static_source.load_route_schedule_cached()
    calendar = static_source.load_calendar()
    calendar_dates = static_source.load_calendar_dates()

    print("fetching live vehicles...")
    vehicles = live_source.fetch_vehicle_positions()
    print(f"  {len(vehicles)} vehicle(s) live on route {config.ROUTE_SHORT_NAME}\n")

    if not vehicles:
        print("nothing running right now, try again during service hours")
        return

    state = ObservationState()

    for v in vehicles:
        trip = schedule.get(v["trip_id"])
        print(f"vehicle {v['vehicle_id']}  trip {v['trip_id'][:16]}...")

        if trip is None:
            print("  trip not in our cached schedule, skipping\n")
            continue

        check = fields.check_vehicle(v, trip, calendar, calendar_dates, state)
        results = check["field_results"]

        disagreement_count = sum(1 for r in results if r["disagreement"] is True)

        for r in results:
            if r["disagreement"] is None:
                marker = "  -   "
            elif r["disagreement"]:
                marker = "DISAGREE"
            else:
                marker = "  ok  "
            print(f"  [{marker}] {r['field']:<22} {r['detail']}")

        print(f"  -> {disagreement_count}/6 fields in disagreement this cycle\n")

    # save once at the end, not per vehicle - so a crash partway through
    # cant leave the state file half written
    state.save()


if __name__ == "__main__":
    main()
