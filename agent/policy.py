# phase 4 - the actual conflict-resolution policy. takes the six field
# observations from agent/fields.py and decides what to DO with each one:
# who wins, is it an anomaly, is it a data quality flag - and tracks
# whether a field has been in conflict long enough to freeze it and
# demand manual review (the chronic-conflict requirement from the brief).
#
# fields 1/2 (LIVE_WINS_FIELDS) arent a flat "live always wins" - its
# margin based. within MIN_DELAY_SECONDS_TO_LOG of the scheduled time,
# the timetable is still accurate enough that we keep the warehouse's
# clean value (verdict ON_SCHEDULE). only past that margin does live's
# own reading actually take over (verdict LIVE_WINS). see
# _resolve_verdict for where that split happens.
#
# the meta-trigger (>3 fields disagreeing at once = SUSPECT) is checked
# FIRST, before any individual field gets resolved - a suspect observation
# doesn't get its fields resolved individually that cycle at all, its
# logged as its own distinct case instead. see reconcile_vehicle().

from agent import config, fields, time_utils

LIVE_WINS_FIELDS = {"arrival_timing", "departure_timing"}
ANOMALY_FIELDS = {"per_stop_adherence", "trip_level_adherence"}
DATA_QUALITY_FIELDS = {"direction", "start_time_date"}


def reconcile_vehicle(vehicle, field_results, ledger, now):
    # field_results is the list of 6 dicts from fields.check_vehicle().
    # returns {"suspect": bool, "reason": str|None, "resolutions": [...]}
    ledger.mark_trip_seen(vehicle["trip_id"], now.isoformat())

    disagreeing = [r for r in field_results if r["disagreement"] is True]

    if len(disagreeing) > 3:
        names = ", ".join(r["field"] for r in disagreeing)
        return {
            "suspect": True,
            "reason": (
                f"{len(disagreeing)}/6 fields disagreeing simultaneously ({names}) - treating the "
                "whole observation as suspect (bad vehicle-to-trip match, gps fault, or genuinely "
                "disrupted service) rather than resolving each field independently this cycle"
            ),
            "resolutions": [],
        }

    resolutions = [resolve_field(vehicle["trip_id"], r, ledger, now) for r in field_results]
    return {"suspect": False, "reason": None, "resolutions": resolutions}


def resolve_field(trip_id, field_result, ledger, now):
    # applies the policy to one field's observation and updates the
    # ledger. this is the one place the chronic-conflict streak/freeze
    # logic lives - see the module comment in agent/ledger.py for the
    # record shape.
    #
    # fields 1/2 carry a "source" tag - "confirmed" (a real stop match) or
    # "estimated" (a speed-projected guess between stops, see
    # agent/eta.py). fields without the concept (3-6) just default to
    # "confirmed" here so their behaviour is unchanged. the rule: a clean
    # ESTIMATED reading can still move the resolved value day to day when
    # nothing's frozen, but it can never reset a conflict streak or clear
    # a chronic-review freeze - only a real confirmed measurement earns
    # that. a run of shaky estimates that happen to agree shouldn't be
    # what clears a manual-review flag.
    field_name = field_result["field"]
    source = field_result.get("source") or "confirmed"
    prior = ledger.get_field(trip_id, field_name)
    streak = prior["conflict_streak"] if prior else 0
    review_status = prior["review_status"] if prior else "NORMAL"

    if field_result["disagreement"] is None:
        # nothing observed this cycle - leave everything exactly as it was
        if prior is None:
            return _write(trip_id, field_name, None, "NO_OBSERVATION", "no observation this cycle", 0, "NORMAL", now, ledger, None, None, None)
        return prior

    can_reset = (not field_result["disagreement"]) and source == "confirmed"

    if field_result["disagreement"]:
        new_streak = streak + 1
    elif can_reset:
        new_streak = 0
    else:
        # clean, but only an ESTIMATE - doesn't earn a reset, doesn't add
        # to the streak either, just sits neutral
        new_streak = streak

    # just crossed the threshold this cycle -> enter review
    if field_result["disagreement"] and review_status == "NORMAL" and new_streak >= config.CHRONIC_CONFLICT_STREAK_THRESHOLD:
        review_status = "MANUAL_REVIEW_REQUIRED"

    if review_status == "MANUAL_REVIEW_REQUIRED":
        if can_reset:
            # one clean CONFIRMED cycle clears it - exact spec from the
            # brief, not a streak of clean cycles like a stricter policy
            # might use
            review_status = "NORMAL"
            new_streak = 0
            verdict, resolved_value, reason = _resolve_verdict(field_name, field_result)
        elif not field_result["disagreement"]:
            verdict = "WITHHELD_UNDER_REVIEW"
            resolved_value = prior["resolved_value"] if prior else None
            reason = (
                f"clean, but only ESTIMATED - only a CONFIRMED match clears a freeze "
                f"({streak} cycles in review). {field_result['detail']}"
            )
        else:
            verdict = "WITHHELD_UNDER_REVIEW"
            resolved_value = prior["resolved_value"] if prior else None
            reason = f"frozen at last resolved value ({streak} cycles in review). {field_result['detail']}"
    else:
        verdict, resolved_value, reason = _resolve_verdict(field_name, field_result)

    return _write(
        trip_id, field_name, resolved_value, verdict, reason, new_streak, review_status, now, ledger, source,
        field_result["live_value"], field_result["warehouse_value"],
    )


def _resolve_verdict(field_name, field_result):
    # reason strings deliberately dont restate the verdict/policy category
    # in words (e.g. "live wins by default...") - the verdict badge next
    # to the reason already says that. just the actual observation here.
    if field_name in LIVE_WINS_FIELDS:
        if not field_result["disagreement"]:
            # within the margin of error (MIN_DELAY_SECONDS_TO_LOG) -
            # live and warehouse are close enough that the timetable is
            # still a good description of reality, so we keep the clean
            # scheduled value instead of a jittery live one. this is a
            # real decision, not a no-op - just one that lands on the
            # warehouse's side instead of live's.
            return "ON_SCHEDULE", field_result["warehouse_value"], field_result["detail"]
        return "LIVE_WINS", field_result["live_value"], field_result["detail"]

    if field_name in ANOMALY_FIELDS:
        if not field_result["disagreement"]:
            return "NO_CONFLICT", None, field_result["detail"]
        return "ANOMALY", field_result["live_value"], field_result["detail"]

    if field_name in DATA_QUALITY_FIELDS:
        if not field_result["disagreement"]:
            return "NO_CONFLICT", None, field_result["detail"]
        return "DATA_QUALITY_FLAG", None, field_result["detail"]

    raise ValueError(f"unknown field: {field_name}")


def _write(trip_id, field_name, resolved_value, verdict, reason, streak, review_status, now, ledger, source, live_value, warehouse_value):
    record = {
        "field": field_name,
        "live_value": live_value,
        "warehouse_value": warehouse_value,
        "resolved_value": resolved_value,
        "verdict": verdict,
        "reason": reason,
        "conflict_streak": streak,
        "review_status": review_status,
        "source": source,
        "updated_at": now.isoformat(),
    }
    ledger.set_field(trip_id, field_name, record)
    return record


def check_for_cancellations(schedule, calendar, calendar_dates, ledger, now, today_date_str):
    # cancellations don't fit the per-vehicle flow above - a cancelled
    # trip by definition never shows up live, so there's no vehicle
    # observation to hang the check off. instead this walks every trip
    # thats supposed to run today and flags any that are more than
    # CANCELLATION_GRACE_PERIOD_SECONDS past their scheduled start with
    # no live sighting ever, via the same resolve_field machinery (so a
    # trip that keeps being "never seen" run after run will chronic-freeze
    # too)
    results = []
    for trip_id, trip in schedule.items():
        if not trip["stops"]:
            continue
        if not fields.service_runs_on(calendar, calendar_dates, trip["service_id"], today_date_str):
            continue
        if ledger.get_trip_first_seen(trip_id) is not None:
            continue

        scheduled_start = time_utils.gtfs_time_to_epoch(today_date_str, trip["stops"][0]["departure_time"])
        overdue_seconds = now.timestamp() - scheduled_start
        # too soon: might just be running late. too long ago: we cant
        # tell, might've run fine before we ever started watching.
        if overdue_seconds <= config.CANCELLATION_GRACE_PERIOD_SECONDS:
            continue
        if overdue_seconds > config.CANCELLATION_MAX_LOOKBACK_SECONDS:
            continue

        fake_result = {
            "field": "trip_level_adherence",
            "live_value": "never observed",
            "warehouse_value": f"expected to start {trip['stops'][0]['departure_time']}",
            "disagreement": True,
            "detail": f"scheduled trip never appeared live, {int(overdue_seconds / 60)} min past scheduled start - possible cancellation",
        }
        results.append((trip_id, resolve_field(trip_id, fake_result, ledger, now)))
    return results
