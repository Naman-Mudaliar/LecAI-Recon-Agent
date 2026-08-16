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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import config, fields, geocode, pdf_report, policy, stop_names, time_utils
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

    print("[1/4] loading warehouse: static GTFS timetable for route 263...")
    schedule = static_source.load_route_schedule_cached()
    calendar = static_source.load_calendar()
    calendar_dates = static_source.load_calendar_dates()
    print(f"      {len(schedule)} scheduled trip(s) loaded")

    print("[2/4] fetching live: BODS GTFS-RT feed (real-time GPS + status)...")
    vehicles = live_source.fetch_vehicle_positions()
    vehicles, stale_ignored = _split_by_freshness(vehicles, now)
    print(f"      {len(vehicles)} live vehicle(s) on route 263 right now, {len(stale_ignored)} ignored (stale gps)")

    obs_state = ObservationState()
    ledger = Ledger()

    print(f"[3/4] comparing live vs. warehouse on 6 fields, per vehicle...")
    vehicle_reports = []
    for v in vehicles:
        trip = schedule.get(v["trip_id"])
        if trip is None:
            continue
        check = fields.check_vehicle(v, trip, calendar, calendar_dates, obs_state)
        outcome = policy.reconcile_vehicle(v, check["field_results"], ledger, now)

        matched_stop = check["matched_stop"]
        next_stop = check["next_stop"]
        # we already know exactly where every scheduled stop on this
        # route is (agent/stop_names.py) - only fall back to a live
        # reverse-geocode of the raw gps point when there's truly no
        # stop context to lean on at all (matched nothing, and not even
        # a nearest candidate to point at)
        needs_geocode = matched_stop is None and check["nearest_candidate_stop"] is None
        address = geocode.reverse_geocode(v["lat"], v["lon"]) if needs_geocode else None
        vehicle_reports.append({
            "vehicle_id": v["vehicle_id"],
            "trip_id": v["trip_id"],
            "trip_headsign": trip["trip_headsign"],
            "lat": v["lat"],
            "lon": v["lon"],
            "address": address,
            "matched_this_cycle": check["matched_this_cycle"],
            "prev_stop_name": matched_stop["stop_name"] if matched_stop else None,
            "prev_stop_sequence": matched_stop["stop_sequence"] if matched_stop else None,
            "nearest_candidate_name": check["nearest_candidate_stop"]["stop_name"] if check["nearest_candidate_stop"] else None,
            "nearest_candidate_distance": check["nearest_candidate_distance"],
            "next_stop_name": next_stop["stop_name"] if next_stop else None,
            "next_stop_scheduled": next_stop["arrival_time"] if next_stop else None,
            "next_stop_eta_epoch": check["next_stop_eta_epoch"],
            "next_stop_eta_detail": check["next_stop_eta_detail"],
            "schedule_window": _schedule_window(trip["stops"], check["last_matched_stop_sequence"], matched_stop["stop_id"] if matched_stop else None),
            "field_results": check["field_results"],
            "outcome": outcome,
        })

    print("[4/4] applying policy: who wins, anomaly flags, chronic-conflict freezes...")
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


HR = "-" * 70

# short badges for a resolved field's verdict - one row per field, no
# reason sentence or chronic-streak explanation on screen. that detail
# still lives in the pdf report, which stays the full explainable record.
_VERDICT_BADGE = {
    "LIVE_WINS": "LIVE WINS",
    "ON_SCHEDULE": "ON SCHEDULE",
    "ANOMALY": "ANOMALY",
    "DATA_QUALITY_FLAG": "DATA QUALITY",
    "WITHHELD_UNDER_REVIEW": "TRUST WAREHOUSE",
    "NO_OBSERVATION": "no obs",
    "NO_CONFLICT": "match",
}

# code-y field keys -> what to actually print, so someone watching doesnt
# have to know the six field names from the brief by heart
_FIELD_LABEL = {
    "arrival_timing": "arrival time",
    "departure_timing": "departure time",
    "per_stop_adherence": "stop-by-stop adherence",
    "trip_level_adherence": "trip running/cancelled",
    "direction": "direction of travel",
    "start_time_date": "trip start time/date",
}


def _fmt(v):
    return str(v) if v is not None else "-"


def _format_reg(vehicle_id):
    # BODS vehicle ids are just the plate with no space (YX74OJJ) - split
    # into the normal "AA00 AAA" plate shape, nicer to read on screen
    if len(vehicle_id) == 7:
        return f"{vehicle_id[:4]} {vehicle_id[4:]}"
    return vehicle_id


# display-only rewrites for the two verbose fields (trip_level_adherence,
# per_stop_adherence) - drops the redundant date (the cycle header already
# has today's date/time) and the "service_id"/"possible" boilerplate the
# field name already implies. order matters: date-suffixed patterns first.
_SHORT_PATTERNS = [
    (re.compile(r"^service_id (\S+) not scheduled on \d+$"), r"sid \1 not scheduled"),
    (re.compile(r"^service_id (\S+) active on \d+$"), r"sid \1 active"),
    (re.compile(r"^running on \d+$"), "running live"),
]

# raw GTFS-rt ScheduleRelationship codes, decoded for a non-technical reader
_SCHEDULE_RELATIONSHIP = {
    "0": "running (scheduled)",
    "1": "added (extra service)",
    "2": "unscheduled",
    "3": "cancelled",
    "5": "deleted",
    "6": "duplicated",
}
_SCHEDULE_RELATIONSHIP_RE = re.compile(r"^schedule_relationship=(\d+)$")

# field 1/2's raw "+385s" / "~-42s" delay values - technically correct,
# not something you'd want to mentally parse off a screen recording
_DELAY_RE = re.compile(r"^(~?)([+-]\d+)s$")


def _humanize_delay(seconds):
    if seconds == 0:
        return "on time"
    tag = "late" if seconds > 0 else "early"
    mins, secs = divmod(abs(seconds), 60)
    if mins and secs:
        return f"{mins}m {secs}s {tag}"
    if mins:
        return f"{mins}m {tag}"
    return f"{secs}s {tag}"


def _short(v):
    # display-only trim for the field table - drops boilerplate parts of
    # the value the field name already implies. full untrimmed values
    # still go to the ledger and the pdf, this is purely about what's
    # legible in a terminal table.
    s = _fmt(v)
    if " (" in s:
        s = s.split(" (", 1)[0]

    m = _DELAY_RE.match(s)
    if m:
        prefix = "~" if m.group(1) else ""
        return prefix + _humanize_delay(int(m.group(2)))

    m = _SCHEDULE_RELATIONSHIP_RE.match(s)
    if m:
        return _SCHEDULE_RELATIONSHIP.get(m.group(1), s)

    for pattern, repl in _SHORT_PATTERNS:
        s = pattern.sub(repl, s)
    s = s.replace("every scheduled stop assumed served", "all served (assumed)")
    s = s.replace("possible skip: ", "skip: ")
    return s


def _print_schedule_window(schedule_window):
    # the handful of scheduled stops around wherever we last confirmed
    # this bus - not the whole trip, just the bit the reconciliation is
    # actually looking at right now. --verbose only.
    if not schedule_window:
        return
    print("    schedule:")
    for s in schedule_window:
        marker = "->" if s["matched_this_cycle"] else "  "
        tp = "  (timepoint)" if s["timepoint"] else ""
        if s["arrival_time"] == s["departure_time"]:
            time_col = s["arrival_time"]
        else:
            time_col = f"{s['arrival_time']} -> {s['departure_time']}"
        print(f"     {marker} {s['stop_sequence']:>2}  {s['stop_name']:<28} {time_col}{tp}")


def _section(title):
    print(f"\n{title}")
    print(HR)


# eta.estimate_next_stop_delay's detail strings, in full, explain exactly
# why there's no trustworthy eta - useful in the pdf, too long for a
# terminal line. these are the honest short versions of the same reasons.
_ETA_DETAIL_SHORT = [
    ("too far to be genuinely", "untracked - too far from expected stop"),
    ("no prior position yet", "no eta yet (first sighting)"),
    ("no time elapsed", "no eta (same position as last poll)"),
    ("speed too low to trust", "stopped/queued"),
    ("no next scheduled stop", "end of trip"),
]


def _short_eta_detail(detail):
    if detail is None:
        return "no eta"
    for needle, short in _ETA_DETAIL_SHORT:
        if needle in detail:
            return short
    return detail


def _print_bus_header(vr, now_epoch):
    # location + previous stop + next stop - the three things asked for,
    # nothing else. previous stop is whatever this cycle actually matched
    # the bus to (or "-" if there was no confirmed match this cycle).
    location = stop_names.location_description(
        vr["prev_stop_name"], vr["nearest_candidate_name"], vr["nearest_candidate_distance"],
        vr["address"], vr["lat"], vr["lon"],
    )
    print(f"\n  Bus Reg: {_format_reg(vr['vehicle_id'])}  @ {location}   -> {vr['trip_headsign']}")
    if vr["prev_stop_name"]:
        print(f"      prev stop   :  {vr['prev_stop_name']}  (seq {vr['prev_stop_sequence']})")
    elif vr["nearest_candidate_name"]:
        print(f"      prev stop   :  not confirmed this cycle - nearest scheduled stop is "
              f"{vr['nearest_candidate_name']} ({vr['nearest_candidate_distance']:.0f}m away, "
              f"needs within {config.STOP_MATCH_RADIUS_METERS:.0f}m to confirm)")
    else:
        print("      prev stop   :  -  (no scheduled stop ahead to compare against)")
    if vr["next_stop_name"]:
        eta_str = time_utils.format_eta(vr["next_stop_eta_epoch"], now_epoch)
        eta_part = f"   eta {eta_str}" if eta_str else f"   eta: {_short_eta_detail(vr['next_stop_eta_detail'])}"
        print(f"      next stop   :  {vr['next_stop_name']}  (scheduled {vr['next_stop_scheduled']}){eta_part}")
    else:
        print("      next stop   :  none left on this trip")


def _print_field_row(r):
    # one line per field: live value, warehouse value, verdict. no reason
    # sentence, no chronic-streak explanation - full detail's in the pdf.
    label = _FIELD_LABEL.get(r["field"], r["field"])
    badge = _VERDICT_BADGE.get(r["verdict"], r["verdict"])
    print(f"      {label:<24} live={_short(r['live_value']):<22} wh={_short(r['warehouse_value']):<22} {badge}")


def _print_field_row_raw(r):
    # suspect buses never get resolved field-by-field this cycle (policy
    # quarantines the whole observation instead), so there's no verdict to
    # show - just the raw live/warehouse values and whether they disagreed
    label = _FIELD_LABEL.get(r["field"], r["field"])
    if r["disagreement"] is True:
        tag = "DISAGREE"
    elif r["disagreement"] is False:
        tag = "match"
    else:
        tag = "no obs"
    print(f"      {label:<24} live={_short(r['live_value']):<22} wh={_short(r['warehouse_value']):<22} {tag}")


def print_report(summary, verbose=False):
    now_dt = datetime.fromisoformat(summary["timestamp"])
    now_epoch = now_dt.timestamp()
    ts = time_utils.format_london(now_dt)
    suspects = [vr for vr in summary["vehicles"] if vr["outcome"]["suspect"]]
    normal = [vr for vr in summary["vehicles"] if not vr["outcome"]["suspect"]]

    print()
    print(HR)
    print(f" ROUTE 263  --  live feed  --  {ts}")
    print(HR)
    print(" LIVE = real-time GPS/status feed (BODS GTFS-RT)   WAREHOUSE = published timetable (BODS static GTFS)")
    print(" one row per field below: what the bus is doing right now vs. what the timetable says it should be doing")
    print(f" {summary['vehicles_checked']} bus{'es' if summary['vehicles_checked'] != 1 else ''} on the road   "
          f"{len(suspects)} suspect   "
          f"{len(summary['stale_ignored'])} ignored (stale gps)")

    if not summary["vehicles"]:
        print("\n  no active buses matched to a known trip this cycle - try again during service hours")

    if suspects:
        _section(" SUSPECT  (quarantined - not resolved field-by-field this cycle)")
        for vr in suspects:
            _print_bus_header(vr, now_epoch)
            print(f"      {vr['outcome']['reason']}")
            for r in vr["field_results"]:
                _print_field_row_raw(r)

    if normal:
        _section(" ACTIVE BUSES  (bus -> prev/next stop -> all six fields)")
        for vr in normal:
            _print_bus_header(vr, now_epoch)
            for r in vr["outcome"]["resolutions"]:
                _print_field_row(r)
            if verbose:
                _print_schedule_window(vr["schedule_window"])

    if summary["stale_ignored"]:
        _section(" IGNORED  (gps position too old to count as live)")
        for s in summary["stale_ignored"]:
            mins = s["age_seconds"] // 60
            print(f"  Bus Reg: {_format_reg(s['vehicle_id']):<9} last real position {mins} min ago - not treated as an active bus this cycle")

    print()
    print(HR)
    print(" one cycle completed")
    print(HR)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="route 263 reconciliation - one real cycle per run")
    parser.add_argument("--verbose", action="store_true", help="also show the nearby schedule window per bus")
    args = parser.parse_args()

    summary = run_cycle()
    print_report(summary, verbose=args.verbose)

    if pdf_report.has_anything_to_report(summary):
        stamp = datetime.fromisoformat(summary["timestamp"]).strftime("%Y%m%d_%H%M%S")
        pdf_path = pdf_report.generate(summary, REPORTS_DIR / f"conflicts_{stamp}.pdf")
        print(f"\n conflicts from this cycle saved to {pdf_path}")
