# tests for the six field checks. all synthetic data, no network - these
# pin down the DETECTION logic (agent/fields.py), not the live sources.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import fields, time_utils
from agent.state import ObservationState


def make_stop(seq, lat=53.48, lon=-2.24, arrival="12:00:00", departure="12:00:00", timepoint=False, name=None):
    return {
        "stop_id": f"stop{seq}", "stop_sequence": seq, "stop_lat": lat, "stop_lon": lon,
        "arrival_time": arrival, "departure_time": departure, "timepoint": timepoint,
        "stop_name": name or f"Stop {seq}",
    }


def make_vehicle(**overrides):
    v = {
        "vehicle_id": "BUS1", "trip_id": "TRIP1", "route_id": "10477",
        "start_date": "20260815", "start_time": "12:00:00",
        "schedule_relationship": 0, "lat": 53.48, "lon": -2.24, "timestamp": 0,
    }
    v.update(overrides)
    return v


CALENDAR = {"931": {"weekdays": {5}, "start_date": "20260101", "end_date": "20261231"}}  # saturday only
CALENDAR_DATES = {}


# --- field 1/2: timing -------------------------------------------------

def test_arrival_timing_no_match_means_no_observation():
    result = fields.check_arrival_timing(make_vehicle(), None)
    assert result["disagreement"] is None


def test_arrival_timing_small_delay_is_not_a_disagreement():
    stop = make_stop(3, arrival="12:00:00")
    epoch = time_utils.gtfs_time_to_epoch("20260815", "12:00:00")
    vehicle = make_vehicle(start_date="20260815", timestamp=epoch + 10)  # 10s late
    result = fields.check_arrival_timing(vehicle, stop)
    assert result["disagreement"] is False


def test_arrival_timing_big_delay_is_a_disagreement():
    stop = make_stop(3, arrival="12:00:00")
    epoch = time_utils.gtfs_time_to_epoch("20260815", "12:00:00")
    vehicle = make_vehicle(start_date="20260815", timestamp=epoch + 300)  # 5 min late
    result = fields.check_arrival_timing(vehicle, stop)
    assert result["disagreement"] is True


def test_departure_timing_uses_departure_time_not_arrival_time():
    # a stop with a scheduled dwell - arrival and departure are different,
    # so the two fields should be able to disagree independently
    stop = make_stop(3, arrival="12:00:00", departure="12:05:00")
    epoch_at_arrival = time_utils.gtfs_time_to_epoch("20260815", "12:00:00")
    vehicle = make_vehicle(start_date="20260815", timestamp=epoch_at_arrival + 5)
    arrival_result = fields.check_arrival_timing(vehicle, stop)
    departure_result = fields.check_departure_timing(vehicle, stop)
    assert arrival_result["disagreement"] is False  # 5s off scheduled arrival
    assert departure_result["disagreement"] is True  # ~5 min off scheduled departure


# --- field 3: per-stop adherence ----------------------------------------

def test_per_stop_adherence_no_progress_is_no_observation():
    trip = {"stops": [make_stop(i) for i in range(5)]}
    result = fields.check_per_stop_adherence(trip, 2, 2)
    assert result["disagreement"] is None


def test_per_stop_adherence_first_ever_match_is_no_observation_even_with_timepoints_between():
    # prior_last_matched == -1 means this is the trip's first-ever match -
    # matching.py's bootstrap search can now land anywhere on the route
    # (see agent/matching.py), so jumping straight past several real
    # timepoints on a first sighting must NOT be flagged as skipped - we
    # were never watching before this cycle, so there's no evidence
    # anything was actually skipped, just stops we hadn't started polling
    # for yet
    stops = [make_stop(i, timepoint=(i in (2, 5, 8))) for i in range(10)]
    trip = {"stops": stops}
    result = fields.check_per_stop_adherence(trip, -1, 9)
    assert result["disagreement"] is None


def test_per_stop_adherence_progress_with_no_timepoints_skipped():
    trip = {"stops": [make_stop(i, timepoint=False) for i in range(5)]}
    result = fields.check_per_stop_adherence(trip, 0, 4)
    assert result["disagreement"] is False


def test_per_stop_adherence_flags_a_skipped_timepoint():
    stops = [make_stop(i, timepoint=(i == 2)) for i in range(5)]
    trip = {"stops": stops}
    result = fields.check_per_stop_adherence(trip, 0, 4)
    assert result["disagreement"] is True
    assert "Stop 2" in result["live_value"]


def test_per_stop_adherence_ignores_non_timepoint_stops_jumped_over():
    # jump straight from 0 to 4, skipping 1,2,3 - but none are timepoints,
    # which is the normal case under sparse polling, not an anomaly
    stops = [make_stop(i, timepoint=False) for i in range(5)]
    trip = {"stops": stops}
    result = fields.check_per_stop_adherence(trip, 0, 4)
    assert result["disagreement"] is False


# --- field 4: trip level adherence --------------------------------------

def test_trip_level_adherence_ok_when_service_runs_today():
    # 20260815 is a saturday
    vehicle = make_vehicle(start_date="20260815")
    result = fields.check_trip_level_adherence(vehicle, CALENDAR, CALENDAR_DATES, "931")
    assert result["disagreement"] is False


def test_trip_level_adherence_flags_service_not_running_today():
    # 20260817 is a monday, service 931 only runs saturdays
    vehicle = make_vehicle(start_date="20260817")
    result = fields.check_trip_level_adherence(vehicle, CALENDAR, CALENDAR_DATES, "931")
    assert result["disagreement"] is True


def test_trip_level_adherence_respects_calendar_dates_exception():
    # normally doesn't run mondays, but an exception adds this specific one
    vehicle = make_vehicle(start_date="20260817")
    cal_dates = {"931": {"20260817": 1}}  # 1 = added
    result = fields.check_trip_level_adherence(vehicle, CALENDAR, cal_dates, "931")
    assert result["disagreement"] is False


# --- field 5: direction --------------------------------------------------

def test_direction_no_last_position_is_no_observation():
    trip = {"stops": [make_stop(0, lat=53.48, lon=-2.24), make_stop(1, lat=53.49, lon=-2.24)]}
    result = fields.check_direction(make_vehicle(lat=53.48, lon=-2.24), trip, None)
    assert result["disagreement"] is None


def test_direction_too_little_movement_is_no_observation():
    trip = {"stops": [make_stop(0, lat=53.48, lon=-2.24), make_stop(1, lat=53.49, lon=-2.24)]}
    last_pos = {"lat": 53.4800, "lon": -2.2400, "timestamp": 0}
    vehicle = make_vehicle(lat=53.48001, lon=-2.24001)  # a few metres, not enough
    result = fields.check_direction(vehicle, trip, last_pos)
    assert result["disagreement"] is None


def test_direction_matches_expected_route_bearing():
    # route heads due north (increasing latitude), vehicle also moved north
    trip = {"stops": [make_stop(0, lat=53.4800, lon=-2.2400), make_stop(1, lat=53.4900, lon=-2.2400)]}
    last_pos = {"lat": 53.4800, "lon": -2.2400, "timestamp": 0}
    vehicle = make_vehicle(lat=53.4830, lon=-2.2400)  # moved north too
    result = fields.check_direction(vehicle, trip, last_pos)
    assert result["disagreement"] is False


def test_direction_flags_travel_opposite_to_expected_route():
    # route heads north, vehicle actually moved south - wrong way
    trip = {"stops": [make_stop(0, lat=53.4800, lon=-2.2400), make_stop(1, lat=53.4900, lon=-2.2400)]}
    last_pos = {"lat": 53.4830, "lon": -2.2400, "timestamp": 0}
    vehicle = make_vehicle(lat=53.4800, lon=-2.2400)  # moved south
    result = fields.check_direction(vehicle, trip, last_pos)
    assert result["disagreement"] is True


# --- field 6: start time/date --------------------------------------------
#
# bods's real live feed reports start_time without applying the BST
# offset (confirmed - see check_start_time_date's comment), so a bus
# that's genuinely on time in august reports a start_time that looks 1h
# "behind" the schedule. these fixtures use that real reporting pattern
# (start_time = true local time minus the BST offset) rather than an
# idealised already-correct value, since that's what the code actually
# has to handle in production.

def test_start_time_date_matches_expected():
    trip = {"stops": [make_stop(0, departure="12:00:00")]}
    vehicle = make_vehicle(start_date="20260815", start_time="11:00:00")  # true local 12:00 BST, reported as bods actually does
    result = fields.check_start_time_date(vehicle, trip, CALENDAR, CALENDAR_DATES, "931")
    assert result["disagreement"] is False


def test_start_time_date_flags_large_drift():
    trip = {"stops": [make_stop(0, departure="12:00:00")]}
    vehicle = make_vehicle(start_date="20260815", start_time="11:20:00")  # true local 12:20 BST - genuinely 20 min late, not just the BST quirk
    result = fields.check_start_time_date(vehicle, trip, CALENDAR, CALENDAR_DATES, "931")
    assert result["disagreement"] is True


def test_start_time_date_bst_quirk_is_compensated_not_flagged():
    # the exact real bug this project found: bods serialises start_time as
    # if GMT (UTC+0) year-round, so during BST the raw string is exactly
    # 1h "behind" the schedule even when the bus is genuinely on time.
    # gtfs_time_to_epoch_fixed_gmt exists specifically to cancel this out
    # - this must NOT be flagged as a live/warehouse conflict any more.
    trip = {"stops": [make_stop(0, departure="19:41:00")]}
    vehicle = make_vehicle(start_date="20260815", start_time="18:41:00")
    result = fields.check_start_time_date(vehicle, trip, CALENDAR, CALENDAR_DATES, "931")
    assert result["disagreement"] is False


def test_start_time_date_winter_needs_no_correction():
    # outside BST, Europe/London and fixed-GMT agree (GMT IS UTC+0), so
    # the compensation is a no-op then - a genuinely correct winter
    # start_time should still show no disagreement, uncorrected
    trip = {"stops": [make_stop(0, departure="12:00:00")]}
    vehicle = make_vehicle(start_date="20260117", start_time="12:00:00")  # january, GMT, same weekday as the other fixtures
    result = fields.check_start_time_date(vehicle, trip, CALENDAR, CALENDAR_DATES, "931")
    assert result["disagreement"] is False


# check_vehicle() integration test - matching.match_vehicle_to_stop is
# unit-tested on its own in tests/test_matching.py; this one just confirms
# check_vehicle wires it through end to end (state -> match -> new anchor)
# without a lookahead cap getting in the way across consecutive cycles.

def _fresh_state():
    return ObservationState(path=Path("__test_only_never_written__.json"))


def test_check_vehicle_confirms_a_stop_many_sequences_ahead_in_one_cycle():
    trip_stops = [make_stop(i, lat=53.4800 + i * 0.001, lon=-2.2400) for i in range(10)]
    trip = {"stops": trip_stops, "service_id": "931"}
    state = _fresh_state()

    # cycle A: confirmed at stop 0
    result_a = fields.check_vehicle(make_vehicle(lat=53.4800, lon=-2.2400, timestamp=0), trip, CALENDAR, CALENDAR_DATES, state)
    assert result_a["matched_stop"]["stop_sequence"] == 0

    # cycle B: sat right on stop 9 a moment later - a real gap in polling
    # can easily move the bus this far in one cycle; must confirm right
    # away, not get stuck waiting on a second recovery pass
    result_b = fields.check_vehicle(make_vehicle(lat=53.4890, lon=-2.2400, timestamp=60), trip, CALENDAR, CALENDAR_DATES, state)
    assert result_b["matched_stop"]["stop_sequence"] == 9
