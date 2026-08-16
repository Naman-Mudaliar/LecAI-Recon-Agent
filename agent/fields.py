# phase 3 - the disagreement checker. compares live observations against
# the warehouse schedule field by field, per the six-field plan from the
# brief. this is DETECTION only - deciding who wins when they disagree is
# phase 4, deliberately not built here yet.
#
# each check returns a dict shaped like:
#   {"field": ..., "live_value": ..., "warehouse_value": ..., "disagreement": True/False/None, "detail": ...}
# disagreement is None when there just isnt an observation to compare this
# cycle (e.g. the bus wasnt near any stop) - thats not "they agree", its
# "nothing to say yet", and those two things shouldn't get mixed up.
#
# check_vehicle() returns a dict, not just the six results - it also hands
# back matched_stop and last_matched_stop_sequence, since callers (like
# run.py) want that for showing the actual nearby schedule, not just the
# pass/fail verdicts.

from datetime import datetime

from agent import config, eta, matching, time_utils


def check_vehicle(vehicle, trip, calendar, calendar_dates, state):
    # runs all six field checks for one live vehicle against its schedule.
    # also does the matching + state updates needed along the way (this is
    # the one place that actually mutates state - callers should call
    # state.save() once after processing everyone, not per vehicle, so a
    # crash mid-cycle cant leave things half written)
    trip_state = state.get_trip_state(vehicle["trip_id"])
    prior_last_matched = trip_state["last_matched_stop_sequence"]

    matched_stop, match_distance = matching.match_vehicle_to_stop(
        trip["stops"], vehicle["lat"], vehicle["lon"], prior_last_matched
    )
    new_last_matched = matched_stop["stop_sequence"] if matched_stop else prior_last_matched

    last_position = state.get_vehicle_last_position(vehicle["vehicle_id"])

    # whatever's next on the schedule from wherever we are now - a plain
    # lookup, always known if the trip has a stop left, regardless of
    # whether we can actually trust an eta to it yet (that needs two real
    # gps fixes and a sane speed, see agent/eta.py - "no next stop" and
    # "cant trust an estimate to the next stop" are different things and
    # eta.estimate_next_stop_delay deliberately conflates them into one
    # None for the fields 1/2 fallback below, so this is looked up
    # separately purely for display, "whats the next stop" shouldnt depend
    # on whether an estimate was trustworthy)
    next_stop = next((s for s in trip["stops"] if s["stop_sequence"] > new_last_matched), None)

    # eta to that stop - this is the same projection fields 1/2 use as a
    # fallback when theres no confirmed match this cycle (new_last_matched
    # == prior_last_matched in that case, so its literally the same call),
    # but its now always run so it can be shown for display too regardless
    # of whether this cycle's match was confirmed
    eta_next_stop, predicted_arrival_epoch, eta_detail = eta.estimate_next_stop_delay(
        trip["stops"], new_last_matched, vehicle["lat"], vehicle["lon"],
        vehicle["timestamp"], last_position, vehicle["start_date"],
    )
    estimate = (eta_next_stop, predicted_arrival_epoch, eta_detail) if (matched_stop is None and eta_next_stop is not None) else None

    results = [
        check_arrival_timing(vehicle, matched_stop, estimate),
        check_departure_timing(vehicle, matched_stop, estimate),
        check_per_stop_adherence(trip, prior_last_matched, new_last_matched),
        check_trip_level_adherence(vehicle, calendar, calendar_dates, trip["service_id"]),
        check_direction(vehicle, trip, last_position),
        check_start_time_date(vehicle, trip, calendar, calendar_dates, trip["service_id"]),
    ]

    if matched_stop is not None:
        state.update_trip_last_matched(vehicle["trip_id"], new_last_matched)
    state.update_vehicle_position(vehicle["vehicle_id"], vehicle["lat"], vehicle["lon"], vehicle["timestamp"])

    return {
        "field_results": results,
        "matched_stop": matched_stop,
        "matched_this_cycle": matched_stop is not None,
        "last_matched_stop_sequence": new_last_matched,
        "next_stop": next_stop,
        "next_stop_eta_epoch": predicted_arrival_epoch if eta_next_stop is not None else None,
        "next_stop_eta_detail": eta_detail,
    }


def check_arrival_timing(vehicle, matched_stop, estimate=None):
    # field 1 - does the observed (or estimated) arrival line up with the
    # scheduled arrival. a real confirmed stop match always takes priority
    # over an estimate if somehow both are available. source is tagged
    # "confirmed" or "estimated" - agent/policy.py uses that to decide
    # whether this observation is allowed to clear a chronic-conflict
    # freeze (only confirmed can) or just move the resolved value day to
    # day (either can)
    if matched_stop is not None:
        scheduled_epoch = time_utils.gtfs_time_to_epoch(vehicle["start_date"], matched_stop["arrival_time"])
        delay = vehicle["timestamp"] - scheduled_epoch
        disagreement = abs(delay) > config.MIN_DELAY_SECONDS_TO_LOG
        return {
            "field": "arrival_timing",
            "live_value": f"{delay:+d}s",
            "warehouse_value": matched_stop["arrival_time"],
            "disagreement": disagreement,
            "source": "confirmed",
            "detail": f"observed {delay:+d}s vs scheduled at {matched_stop['stop_name']} (seq {matched_stop['stop_sequence']})",
        }

    if estimate is not None:
        next_stop, predicted_arrival_epoch, detail = estimate
        scheduled_epoch = time_utils.gtfs_time_to_epoch(vehicle["start_date"], next_stop["arrival_time"])
        delay = int(predicted_arrival_epoch - scheduled_epoch)
        disagreement = abs(delay) > config.MIN_DELAY_SECONDS_TO_LOG
        return {
            "field": "arrival_timing",
            "live_value": f"~{delay:+d}s",
            "warehouse_value": next_stop["arrival_time"],
            "disagreement": disagreement,
            "source": "estimated",
            "detail": f"projected {delay:+d}s vs scheduled at {next_stop['stop_name']} ({detail})",
        }

    return _no_observation("arrival_timing")


def check_departure_timing(vehicle, matched_stop, estimate=None):
    # field 2 - same idea against departure_time. worth repeating the
    # caveat: a confirmed match uses the SAME observed timestamp as field 1
    # (one gps ping, not separate arrival/departure events like a real
    # TripUpdate would give) just compared to a different scheduled time -
    # can still genuinely disagree independently of field 1 if the stop
    # has a scheduled dwell (arrival_time != departure_time). same
    # confirmed vs estimated source tagging as field 1
    if matched_stop is not None:
        scheduled_epoch = time_utils.gtfs_time_to_epoch(vehicle["start_date"], matched_stop["departure_time"])
        delay = vehicle["timestamp"] - scheduled_epoch
        disagreement = abs(delay) > config.MIN_DELAY_SECONDS_TO_LOG
        return {
            "field": "departure_timing",
            "live_value": f"{delay:+d}s",
            "warehouse_value": matched_stop["departure_time"],
            "disagreement": disagreement,
            "source": "confirmed",
            "detail": f"observed {delay:+d}s vs scheduled at {matched_stop['stop_name']} (seq {matched_stop['stop_sequence']})",
        }

    if estimate is not None:
        next_stop, predicted_arrival_epoch, detail = estimate
        scheduled_epoch = time_utils.gtfs_time_to_epoch(vehicle["start_date"], next_stop["departure_time"])
        delay = int(predicted_arrival_epoch - scheduled_epoch)
        disagreement = abs(delay) > config.MIN_DELAY_SECONDS_TO_LOG
        return {
            "field": "departure_timing",
            "live_value": f"~{delay:+d}s",
            "warehouse_value": next_stop["departure_time"],
            "disagreement": disagreement,
            "source": "estimated",
            "detail": f"projected {delay:+d}s vs scheduled at {next_stop['stop_name']} ({detail})",
        }

    return _no_observation("departure_timing")


def check_per_stop_adherence(trip, prior_last_matched, new_last_matched):
    # field 3 - possible skipped stops. only timepoint stops count (see
    # config.py for why) - a timepoint strictly between where we were last
    # cycle and where we are now, that wasnt the stop we just matched, is
    # one we went past without ever being detected near it
    if new_last_matched <= prior_last_matched:
        return _no_observation("per_stop_adherence")

    skipped_timepoints = [
        s for s in trip["stops"]
        if prior_last_matched < s["stop_sequence"] < new_last_matched and s["timepoint"]
    ]

    if not skipped_timepoints:
        return {
            "field": "per_stop_adherence",
            "live_value": "none skipped",
            "warehouse_value": "every scheduled stop assumed served",
            "disagreement": False,
            "detail": f"progressed stop {prior_last_matched} -> {new_last_matched}, no timepoints in between",
        }

    names = ", ".join(s["stop_name"] for s in skipped_timepoints)
    return {
        "field": "per_stop_adherence",
        "live_value": f"possible skip: {names}",
        "warehouse_value": "every scheduled stop assumed served",
        "disagreement": True,
        "detail": f"{len(skipped_timepoints)} timepoint stop(s) passed without ever being matched - ANOMALY, not a simple delay",
    }


def check_trip_level_adherence(vehicle, calendar, calendar_dates, service_id):
    # field 4 - is this trip actually supposed to be running today. only
    # catches the "unscheduled extra" direction per-cycle (a live trip
    # with no valid service today) - catching genuine cancellations needs
    # tracking which scheduled trips never showed up all day, which
    # belongs at the ledger level (phase 4), not in a single-cycle check
    service_today = service_runs_on(calendar, calendar_dates, service_id, vehicle["start_date"])

    if not service_today:
        return {
            "field": "trip_level_adherence",
            "live_value": f"running on {vehicle['start_date']}",
            "warehouse_value": f"service_id {service_id} not scheduled on {vehicle['start_date']}",
            "disagreement": True,
            "detail": "live vehicle is running a trip whose service_id isn't active today - ANOMALY (unscheduled extra, or a calendar/matching fault)",
        }

    return {
        "field": "trip_level_adherence",
        "live_value": f"schedule_relationship={vehicle['schedule_relationship']}",
        "warehouse_value": f"service_id {service_id} active on {vehicle['start_date']}",
        "disagreement": False,
        "detail": "trip is validly scheduled for today",
    }


def check_direction(vehicle, trip, last_position):
    # field 5 - the live feed never reports direction_id (checked: 0 out
    # of 759 real vehicles had it), so theres nothing to directly compare
    # against trips.txt's direction_id. instead: work out the bearing
    # between this position and the vehicles last known one, and compare
    # it against the bearing implied by the trip's own scheduled stops
    # around here. a big mismatch means the vehicle is actually moving a
    # way that doesn't match the trip its assigned to
    if last_position is None:
        return _no_observation("direction")

    displacement = matching.haversine_meters(last_position["lat"], last_position["lon"], vehicle["lat"], vehicle["lon"])
    if displacement < config.MIN_DISPLACEMENT_METERS_FOR_BEARING:
        return _no_observation("direction")

    observed_bearing = matching.bearing_degrees(last_position["lat"], last_position["lon"], vehicle["lat"], vehicle["lon"])
    expected_bearing = _expected_bearing_near(trip, vehicle["lat"], vehicle["lon"])
    if expected_bearing is None:
        return _no_observation("direction")

    diff = matching.bearing_difference(observed_bearing, expected_bearing)
    disagreement = diff > config.MAX_BEARING_DIFFERENCE_DEGREES

    return {
        "field": "direction",
        "live_value": f"{observed_bearing:.0f} deg (from last 2 real positions)",
        "warehouse_value": f"{expected_bearing:.0f} deg (from scheduled stop sequence)",
        "disagreement": disagreement,
        "detail": f"{diff:.0f} deg apart - {'likely wrong direction / matching fault' if disagreement else 'consistent with assigned trip'}",
    }


def check_start_time_date(vehicle, trip, calendar, calendar_dates, service_id):
    # field 6 - does the trip's reported start time/date match what the
    # schedule expects.
    #
    # real finding while building this: every single active vehicle on
    # the route right now shows an exact -3600s (1 hour) drift here.
    # checked it wasnt our own bug - the two epoch conversions go through
    # the exact same function, so a bug in our own tz handling would
    # cancel out, not produce a clean 3600s gap. the raw strings
    # themselves are 1h apart (live says "18:41:00", schedule says
    # "19:41:00"), and 18:41 + 1h = 19:41 exactly - consistent with
    # bods's live feed not applying the BST offset when it serializes
    # start_time. real upstream data quality issue, not us. this is
    # genuinely what field 6 is supposed to catch.
    if not trip["stops"]:
        return _no_observation("start_time_date")

    first_stop = trip["stops"][0]
    expected_start_epoch = time_utils.gtfs_time_to_epoch(vehicle["start_date"], first_stop["departure_time"])
    reported_start_epoch = time_utils.gtfs_time_to_epoch(vehicle["start_date"], vehicle["start_time"])
    drift = reported_start_epoch - expected_start_epoch

    service_today = service_runs_on(calendar, calendar_dates, service_id, vehicle["start_date"])

    disagreement = abs(drift) > config.MAX_START_TIME_DRIFT_SECONDS or not service_today
    detail = f"start time drift {drift:+d}s"
    if not service_today:
        detail += f", and {vehicle['start_date']} isn't a valid service day for this trip at all"

    return {
        "field": "start_time_date",
        "live_value": f"{vehicle['start_date']} {vehicle['start_time']}",
        "warehouse_value": f"expected ~{first_stop['departure_time']}",
        "disagreement": disagreement,
        "detail": detail,
    }


def _no_observation(field_name):
    return {
        "field": field_name,
        "live_value": None,
        "warehouse_value": None,
        "disagreement": None,
        "source": None,
        "detail": "no observation this cycle",
    }


def service_runs_on(calendar, calendar_dates, service_id, date_str):
    # does this service_id actually run on this date - checks the
    # one-off exceptions first, then falls back to the normal weekday
    # pattern
    exceptions = calendar_dates.get(service_id, {})
    if date_str in exceptions:
        return exceptions[date_str] == 1  # 1 = added, 2 = removed

    cal = calendar.get(service_id)
    if cal is None:
        return False
    if not (cal["start_date"] <= date_str <= cal["end_date"]):
        return False

    weekday = datetime.strptime(date_str, "%Y%m%d").weekday()
    return weekday in cal["weekdays"]


def _expected_bearing_near(trip, lat, lon):
    # finds the two consecutive scheduled stops closest to the given
    # position and returns the bearing between them - a rough stand-in
    # for "which way is the route pointing right here" since we dont have
    # proper route geometry (shapes.txt is empty for this route, checked
    # in phase 2)
    stops = trip["stops"]
    if len(stops) < 2:
        return None

    def dist_to(i):
        if stops[i]["stop_lat"] is None:
            return float("inf")
        return matching.haversine_meters(lat, lon, stops[i]["stop_lat"], stops[i]["stop_lon"])

    closest_idx = min(range(len(stops)), key=dist_to)
    # use the pair either side of the closest stop so the bearing reflects
    # the direction of travel through this point, not just towards it
    i = min(closest_idx, len(stops) - 2)
    a, b = stops[i], stops[i + 1]
    if a["stop_lat"] is None or b["stop_lat"] is None:
        return None
    return matching.bearing_degrees(a["stop_lat"], a["stop_lon"], b["stop_lat"], b["stop_lon"])
