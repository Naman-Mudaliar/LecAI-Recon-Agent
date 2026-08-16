#!/usr/bin/env python3

# one invocation of this script = one real reconciliation cycle for
# route 263. fetches live + static, runs all six field checks per active
# vehicle, applies the phase 4 policy (who wins / anomaly / data quality
# flag / suspect / chronic-conflict freeze), and writes everything to the
# ledger + an append-only cycle log.
#
# theres no --cycles loop on purpose - state genuinely persists to disk
# between separate runs (data/ledger.json, data/observation_state.json),
# so two reconciliation cycles means running this twice with real time
# between, not looping inside one process. run it again in 15ish minutes
# during service hours to see it in action.
#
#     python run.py

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
    # a handful of stops around wherever we last confirmed the bus was -
    # not the whole trip (some have 90+ stops), just enough to show the
    # schedule context the reconciliation is actually working against
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
    # the handful of scheduled stops around wherever we last confirmed
    # this bus - not the whole trip, just the bit the reconciliation is
    # actually looking at right now
    if not schedule_window:
        return
    print("    schedule:")
    for s in schedule_window:
        marker = "->" if s["matched_this_cycle"] else "  "
        tp = "  (timepoint)" if s["timepoint"] else ""
        # most stops have no scheduled dwell - arrival and departure are
        # the same time, so showing both is just noise. only split them
        # out when theres an actual difference to see.
        if s["arrival_time"] == s["departure_time"]:
            time_col = s["arrival_time"]
        else:
            time_col = f"{s['arrival_time']} -> {s['departure_time']}"
        print(f"     {marker} {s['stop_sequence']:>2}  {s['stop_name']:<28} {time_col}{tp}")


_VERDICT_BADGE = {
    "LIVE_WINS": "LIVE WINS",
    "ANOMALY": "ANOMALY",
    "DATA_QUALITY_FLAG": "DATA QUALITY",
    "WITHHELD_UNDER_REVIEW": "FROZEN (review)",
}


def _section(title):
    print(f"\n{title}")
    print(HR)


def _print_field_line(r):
    badge = _VERDICT_BADGE.get(r["verdict"], r["verdict"])
    value = f"  =  {r['resolved_value']}" if r["resolved_value"] is not None else ""
    source = f"  [{r['source']}]" if r.get("source") else ""
    print(f"      {r['field']:<20} {badge:<16}{value}{source}")
    print(f"        {r['reason']}")


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

    print()
    print(HR)
    print(f" ROUTE 263  --  live vs schedule  --  {ts}")
    print(HR)
    print(f" {summary['vehicles_checked']} bus{'es' if summary['vehicles_checked'] != 1 else ''} checked   "
          f"{len(suspects)} suspect   {flagged_count} flagged   "
          f"{withheld_count} frozen   {len(summary['cancellations'])} possible cancellation"
          f"{'s' if len(summary['cancellations']) != 1 else ''}")

    if not summary["vehicles"]:
        print("\n  no active buses matched to a known trip this cycle - try again during service hours")

    if suspects:
        _section(" SUSPECT  (quarantined - not resolved field-by-field this cycle)")
        for vr in suspects:
            print(f"\n  {vr['vehicle_id']}  ->  {vr['trip_headsign']}   @ {vr['lat']:.4f}, {vr['lon']:.4f}")
            print(f"      {vr['outcome']['reason']}")
            _print_schedule_window(vr["schedule_window"])

    if normal:
        _section(" ACTIVE BUSES")
        for vr in normal:
            notable = [r for r in vr["outcome"]["resolutions"] if r["verdict"] not in ROUTINE_VERDICTS]
            routine = len(vr["outcome"]["resolutions"]) - len(notable)

            print(f"\n  {vr['vehicle_id']}  ->  {vr['trip_headsign']}   @ {vr['lat']:.4f}, {vr['lon']:.4f}")
            _print_schedule_window(vr["schedule_window"])
            print()

            if not notable:
                print(f"      all clear  ({routine} field(s) routine this cycle)")
            else:
                for r in notable:
                    _print_field_line(r)
                if routine:
                    print(f"      + {routine} other field(s) routine this cycle")

            if verbose:
                print("      -- full field dump --")
                for r in vr["outcome"]["resolutions"]:
                    _print_field_line(r)

    if summary["cancellations"]:
        _section(" POSSIBLE CANCELLATIONS  (scheduled, never seen live, past grace period)")
        for c in summary["cancellations"]:
            print(f"  {c['trip_id'][:16]}...   {c['record']['reason']}")

    print()
    print(HR)
    print(" run again in ~15 min for the next real cycle -- state is real,")
    print(" persisted to data/ledger.json, not a lookup table.")
    print(HR)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="route 263 reconciliation - one real cycle per run")
    parser.add_argument("--verbose", action="store_true", help="also show the full six-field dump per bus")
    args = parser.parse_args()

    summary = run_cycle()
    print_report(summary, verbose=args.verbose)
