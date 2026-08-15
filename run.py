#!/usr/bin/env python3
"""
one invocation of this script = one real reconciliation cycle for route
263. fetches live + static, runs all six field checks per active
vehicle, applies the phase 4 policy (who wins / anomaly / data quality
flag / suspect / chronic-conflict freeze), and writes everything to the
ledger + an append-only cycle log.

theres no --cycles loop on purpose - state genuinely persists to disk
between separate runs (data/ledger.json, data/observation_state.json),
so two reconciliation cycles means running this twice with real time
between, not looping inside one process. run it again in 15ish minutes
during service hours to see it in action.

    python run.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import config, fields, policy
from agent.ledger import Ledger
from agent.sources import live_source, static_source
from agent.state import ObservationState

CYCLE_LOG_PATH = config.DATA_DIR / "cycle_history.jsonl"

SCHEDULE_WINDOW_BEFORE = 1
SCHEDULE_WINDOW_AFTER = 4


def _schedule_window(trip_stops, last_matched_stop_sequence, matched_stop_id):
    """a handful of stops around wherever we last confirmed the bus was -
    not the whole trip (some have 90+ stops), just enough to show the
    schedule context the reconciliation is actually working against."""
    lo = max(0, last_matched_stop_sequence - SCHEDULE_WINDOW_BEFORE)
    hi = last_matched_stop_sequence + SCHEDULE_WINDOW_AFTER
    window = [s for s in trip_stops if lo <= s["stop_sequence"] <= hi]
    return [
        {
            "stop_sequence": s["stop_sequence"],
            "stop_name": s["stop_name"],
            "arrival_time": s["arrival_time"],
            "departure_time": s["departure_time"],
            "timepoint": s["timepoint"],
            "matched_this_cycle": s["stop_id"] == matched_stop_id,
        }
        for s in window
    ]


def run_cycle():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y%m%d")

    schedule = static_source.load_route_schedule_cached()
    calendar = static_source.load_calendar()
    calendar_dates = static_source.load_calendar_dates()

    vehicles = live_source.fetch_vehicle_positions()

    obs_state = ObservationState()
    ledger = Ledger()

    vehicle_reports = []
    for v in vehicles:
        trip = schedule.get(v["trip_id"])
        if trip is None:
            continue
        check = fields.check_vehicle(v, trip, calendar, calendar_dates, obs_state)
        outcome = policy.reconcile_vehicle(v, check["field_results"], ledger, now)

        matched_stop_id = check["matched_stop"]["stop_id"] if check["matched_stop"] else None
        vehicle_reports.append({
            "vehicle_id": v["vehicle_id"],
            "trip_id": v["trip_id"],
            "trip_headsign": trip["trip_headsign"],
            "lat": v["lat"],
            "lon": v["lon"],
            "matched_this_cycle": check["matched_this_cycle"],
            "schedule_window": _schedule_window(trip["stops"], check["last_matched_stop_sequence"], matched_stop_id),
            "field_results": check["field_results"],
            "outcome": outcome,
        })

    cancellations = policy.check_for_cancellations(schedule, calendar, calendar_dates, ledger, now, today)

    obs_state.save()
    ledger.save()

    summary = {
        "timestamp": now.isoformat(),
        "vehicles_checked": len(vehicle_reports),
        "vehicles": vehicle_reports,
        "cancellations": [{"trip_id": tid, "record": rec} for tid, rec in cancellations],
    }
    CYCLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CYCLE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, default=str) + "\n")

    return summary


# verdicts that arent worth printing on their own every single time -
# "nothing happened" shouldnt take up as much room as "something happened"
ROUTINE_VERDICTS = {"NO_OBSERVATION", "NO_CONFLICT"}

HR = "-" * 70


def _print_schedule_window(schedule_window):
    """the handful of scheduled stops around wherever we last confirmed
    this bus - not the whole trip, just the bit the reconciliation is
    actually looking at right now."""
    if not schedule_window:
        return
    print("    nearby schedule:")
    for s in schedule_window:
        marker = "-> " if s["matched_this_cycle"] else "   "
        tp = " (timepoint)" if s["timepoint"] else ""
        print(f"    {marker}seq {s['stop_sequence']:<3} {s['stop_name']:<28} "
              f"arr {s['arrival_time']}  dep {s['departure_time']}{tp}")


def print_report(summary, verbose=False):
    ts = summary["timestamp"].replace("T", " ")[:19] + " UTC"
    suspects = [vr for vr in summary["vehicles"] if vr["outcome"]["suspect"]]
    normal = [vr for vr in summary["vehicles"] if not vr["outcome"]["suspect"]]

    flagged_count = sum(
        1 for vr in normal for r in vr["outcome"]["resolutions"] if r["verdict"] not in ROUTINE_VERDICTS
    )
    withheld_count = sum(
        1 for vr in normal for r in vr["outcome"]["resolutions"] if r["verdict"] == "WITHHELD_UNDER_REVIEW"
    )

    print(f"\n{'=' * 70}")
    print(f"  ROUTE 263 -- LIVE vs SCHEDULE, cycle at {ts}")
    print(f"{'=' * 70}")
    print(f"  {summary['vehicles_checked']} bus(es) checked  |  "
          f"{len(suspects)} suspect  |  {flagged_count} field(s) flagged  |  "
          f"{withheld_count} frozen (under review)  |  "
          f"{len(summary['cancellations'])} possible cancellation(s)")

    if not summary["vehicles"]:
        print("\n  no active buses matched to a known trip this cycle - try again during service hours")

    if suspects:
        print(f"\n{HR}\n  SUSPECT -- quarantined, not resolved field-by-field this cycle\n{HR}")
        for vr in suspects:
            print(f"\n  bus {vr['vehicle_id']}  ->  {vr['trip_headsign']}  @ ({vr['lat']:.4f}, {vr['lon']:.4f})")
            print(f"    {vr['outcome']['reason']}")
            _print_schedule_window(vr["schedule_window"])

    if normal:
        print(f"\n{HR}\n  ACTIVE BUSES\n{HR}")
        for vr in normal:
            notable = [r for r in vr["outcome"]["resolutions"] if r["verdict"] not in ROUTINE_VERDICTS]
            routine = len(vr["outcome"]["resolutions"]) - len(notable)

            print(f"\n  bus {vr['vehicle_id']}  ->  {vr['trip_headsign']}  @ ({vr['lat']:.4f}, {vr['lon']:.4f})")
            _print_schedule_window(vr["schedule_window"])
            if not notable:
                print(f"    all {routine} field(s) routine (no conflict / nothing observed this cycle)")
            for r in notable:
                value_note = f"  [resolved: {r['resolved_value']}]" if r["resolved_value"] is not None else ""
                print(f"    {r['field']:<22} {r['verdict']:<20}{value_note}")
                print(f"      {r['reason']}")
            if notable and routine:
                print(f"    (+ {routine} other field(s) routine this cycle)")

            if verbose:
                print("    -- full field dump --")
                for r in vr["outcome"]["resolutions"]:
                    print(f"    {r['field']:<22} {r['verdict']:<20} {r['reason']}")

    if summary["cancellations"]:
        print(f"\n{HR}\n  POSSIBLE CANCELLATIONS -- scheduled, never seen live, past grace period\n{HR}")
        for c in summary["cancellations"]:
            print(f"  trip {c['trip_id'][:16]}...  {c['record']['reason']}")

    print(f"\n{'=' * 70}")
    print("  run again in ~15 min for the next real cycle -- state is real,")
    print("  persisted to data/ledger.json, not a lookup table.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="route 263 reconciliation - one real cycle per run")
    parser.add_argument("--verbose", action="store_true", help="also show the full six-field dump per bus")
    args = parser.parse_args()

    summary = run_cycle()
    print_report(summary, verbose=args.verbose)
