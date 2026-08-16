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

from agent import config, fields, pdf_report, policy, time_utils
from agent.ledger import Ledger
from agent.sources import live_source, static_source
from agent.state import ObservationState

CYCLE_LOG_PATH = config.DATA_DIR / "cycle_history.jsonl"
REPORTS_DIR = config.DATA_DIR.parent / "reports"

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


def _split_by_freshness(vehicles, now):
    # bods keeps a vehicle's last real position in the feed long after it
    # stops actually reporting (see config.MAX_VEHICLE_POSITION_AGE_SECONDS)
    # - splitting here means a stale echo never even reaches the matching
    # engine, instead of getting quietly treated as a currently active bus
    fresh, stale = [], []
    for v in vehicles:
        age = now.timestamp() - v["timestamp"]
        if age <= config.MAX_VEHICLE_POSITION_AGE_SECONDS:
            fresh.append(v)
        else:
            stale.append({"vehicle_id": v["vehicle_id"], "age_seconds": int(age)})
    return fresh, stale


def run_cycle():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y%m%d")

    schedule = static_source.load_route_schedule_cached()
    calendar = static_source.load_calendar()
    calendar_dates = static_source.load_calendar_dates()

    vehicles = live_source.fetch_vehicle_positions()
    vehicles, stale_ignored = _split_by_freshness(vehicles, now)

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
        next_stop = check["next_stop"]
        vehicle_reports.append({
            "vehicle_id": v["vehicle_id"],
            "trip_id": v["trip_id"],
            "trip_headsign": trip["trip_headsign"],
            "lat": v["lat"],
            "lon": v["lon"],
            "matched_this_cycle": check["matched_this_cycle"],
            "next_stop_name": next_stop["stop_name"] if next_stop else None,
            "next_stop_scheduled": next_stop["arrival_time"] if next_stop else None,
            "next_stop_eta_epoch": check["next_stop_eta_epoch"],
            "next_stop_eta_detail": check["next_stop_eta_detail"],
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
        "stale_ignored": stale_ignored,
    }
    CYCLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CYCLE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, default=str) + "\n")

    return summary


# verdicts that arent worth printing on their own every single time -
# "nothing happened" shouldnt take up as much room as "something happened"
ROUTINE_VERDICTS = {"NO_OBSERVATION", "NO_CONFLICT", "ON_SCHEDULE"}

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
    "ON_SCHEDULE": "ON SCHEDULE",
    "ANOMALY": "ANOMALY",
    "DATA_QUALITY_FLAG": "DATA QUALITY",
    "WITHHELD_UNDER_REVIEW": "FROZEN (review)",
}

# these are the verdicts that mean live and warehouse actually disagreed
# this cycle - everything else (no observation, no conflict, on schedule)
# is the two sources agreeing, or nothing to compare yet
_CONFLICT_VERDICTS = {"LIVE_WINS", "ANOMALY", "DATA_QUALITY_FLAG", "WITHHELD_UNDER_REVIEW"}


def _section(title):
    print(f"\n{title}")
    print(HR)


def _print_bus_block(vr, now_epoch, ts, verbose):
    # leads with the thing a rider (or a reviewer) actually wants first -
    # which bus, where its actually headed vs whats immediately next, and
    # when it'll get there. schedule detail moves to --verbose only.
    # current time gets repeated on every bus so each block reads on its
    # own - "eta ~4m" only means something next to the moment it was
    # computed against, and this report gets read one bus at a time, not
    # top to bottom in one sitting
    print(f"\n  [{ts}]")
    print(f"  bus {vr['vehicle_id']}  ->  {vr['trip_headsign']}  (final destination)   @ {vr['lat']:.4f}, {vr['lon']:.4f}")
    if vr["next_stop_name"]:
        eta_str = time_utils.format_eta(vr["next_stop_eta_epoch"], now_epoch)
        eta_part = f"   eta {eta_str}" if eta_str else f"   eta: {vr['next_stop_eta_detail']}"
        print(f"      next stop   :  {vr['next_stop_name']}  (scheduled {vr['next_stop_scheduled']}){eta_part}")
    else:
        print("      next stop   :  none left on this trip")
    if verbose:
        _print_schedule_window(vr["schedule_window"])


def _print_field_line(r):
    print(f"      {r['field']}")
    if r["verdict"] == "NO_OBSERVATION":
        print("        no observation this cycle")
        return

    badge = _VERDICT_BADGE.get(r["verdict"], r["verdict"])
    conflict = "yes" if r["verdict"] in _CONFLICT_VERDICTS else "no"
    source = f"  [{r['source']}]" if r.get("source") else ""

    print(f"        live feed :  {r['live_value']}")
    print(f"        warehouse :  {r['warehouse_value']}")
    resolved = f"  ->  resolved as {r['resolved_value']}" if r["resolved_value"] is not None else ""
    print(f"        conflict  :  {conflict}   ({badge}){resolved}{source}")
    print(f"        {r['reason']}")
    if conflict == "yes":
        print(f"        to clear  :  {policy.recon_steps(r)}")


def print_report(summary, verbose=False):
    now_dt = datetime.fromisoformat(summary["timestamp"])
    now_epoch = now_dt.timestamp()
    ts = time_utils.format_london(now_dt)
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
          f"{'s' if len(summary['cancellations']) != 1 else ''}   "
          f"{len(summary['stale_ignored'])} ignored (stale gps)")

    if not summary["vehicles"]:
        print("\n  no active buses matched to a known trip this cycle - try again during service hours")

    if suspects:
        _section(" SUSPECT  (quarantined - not resolved field-by-field this cycle)")
        for vr in suspects:
            _print_bus_block(vr, now_epoch, ts, verbose)
            print(f"      {vr['outcome']['reason']}")
            print("      to clear  :  quarantined this cycle only - resolves field-by-field again "
                  "once 3 or fewer of 6 fields disagree in a future cycle")

    if normal:
        _section(" ACTIVE BUSES  (bus -> next stop vs destination -> eta -> conflicts, if any)")
        for vr in normal:
            notable = [r for r in vr["outcome"]["resolutions"] if r["verdict"] not in ROUTINE_VERDICTS]
            routine = len(vr["outcome"]["resolutions"]) - len(notable)

            _print_bus_block(vr, now_epoch, ts, verbose)

            if not notable:
                print(f"      no conflicts this cycle  ({routine} field(s) clear)")
            else:
                print("      conflicts this cycle:")
                for r in notable:
                    _print_field_line(r)
                if routine:
                    print(f"      + {routine} other field(s) clear this cycle")

            if verbose:
                print("      -- full field dump --")
                for r in vr["outcome"]["resolutions"]:
                    _print_field_line(r)

    if summary["cancellations"]:
        _section(" POSSIBLE CANCELLATIONS  (scheduled, never seen live, past grace period)")
        for c in summary["cancellations"]:
            print(f"  {c['trip_id'][:16]}...   {c['record']['reason']}")

    if summary["stale_ignored"]:
        _section(" IGNORED  (gps position too old to count as live)")
        for s in summary["stale_ignored"]:
            mins = s["age_seconds"] // 60
            print(f"  {s['vehicle_id']:<10} last real position {mins} min ago - not treated as an active bus this cycle")

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

    if pdf_report.has_anything_to_report(summary):
        stamp = datetime.fromisoformat(summary["timestamp"]).strftime("%Y%m%d_%H%M%S")
        pdf_path = pdf_report.generate(summary, REPORTS_DIR / f"conflicts_{stamp}.pdf")
        print(f"\n conflicts from this cycle saved to {pdf_path}")
