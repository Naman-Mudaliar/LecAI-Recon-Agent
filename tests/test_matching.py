# tests for the nearest-stop matching logic. all synthetic coords, no
# network - the point is to pin down the matching behaviour (forward-only
# search, radius cutoff) without needing a live bus.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import matching, config


def make_stop(seq, lat, lon):
    return {"stop_sequence": seq, "stop_lat": lat, "stop_lon": lon,
            "stop_id": f"stop{seq}", "arrival_time": "12:00:00", "departure_time": "12:00:00"}


# manchester-ish coords, roughly 100m apart in latitude per stop, easy to
# reason about
TRIP_STOPS = [make_stop(i, 53.4800 + i * 0.001, -2.2400) for i in range(10)]


def test_haversine_zero_for_same_point():
    assert matching.haversine_meters(53.48, -2.24, 53.48, -2.24) == 0


def test_haversine_roughly_right_over_short_distance():
    # 0.001 degrees latitude is about 111m, give it some slack
    d = matching.haversine_meters(53.4800, -2.2400, 53.4810, -2.2400)
    assert 100 < d < 120


def test_matches_stop_right_on_top_of_it():
    stop, distance = matching.match_vehicle_to_stop(TRIP_STOPS, 53.4830, -2.2400)
    assert stop["stop_sequence"] == 3
    assert distance < 5


def test_no_match_when_too_far_from_everything():
    stop, distance = matching.match_vehicle_to_stop(TRIP_STOPS, 55.0, -1.0)  # nowhere near manchester
    assert stop is None


def test_never_matches_a_stop_already_passed():
    # vehicle is literally sat on stop 2's coordinates, but we've already
    # confirmed stop 5 - should not jump backwards
    stop, distance = matching.match_vehicle_to_stop(TRIP_STOPS, 53.4820, -2.2400, last_matched_sequence=5)
    assert stop is None or stop["stop_sequence"] > 5


def test_search_is_not_capped_to_the_next_few_stops():
    # a trip HAS a confirmed prior match (last_matched=0), and the vehicle
    # is sat right on stop 9 - far more than a few stops ahead. must still
    # find it immediately, no waiting on a second recovery pass needed.
    # (an earlier version of this capped the forward search to only the
    # next 8 stops, which meant a real gap between polls - the bus
    # genuinely travelling that far between two cycles - got a trip stuck
    # unconfirmed for the rest of the trip, confirmed live more than once.
    # checked every real stop pair on route 263: nothing 9+ stops apart in
    # sequence is ever within match radius of something else, so nothing
    # is lost by searching the whole forward remainder every time.)
    stop, distance = matching.match_vehicle_to_stop(TRIP_STOPS, 53.4890, -2.2400, last_matched_sequence=0)
    assert stop["stop_sequence"] == 9
    assert distance < 5


def test_bootstrap_search_covers_the_whole_route():
    # first-ever observation of this trip (last_matched_sequence == -1) -
    # sat right on stop 9 (the last one). bootstrap has to search the
    # whole route, not just stops near the start, or a trip first seen
    # mid-route could never match on any future cycle either (nothing
    # advances last_matched off -1 without a first successful match)
    stop, distance = matching.match_vehicle_to_stop(TRIP_STOPS, 53.4890, -2.2400, last_matched_sequence=-1)
    assert stop["stop_sequence"] == 9
    assert distance < 5


def test_nearest_candidate_stop_ignores_the_confirm_radius():
    # purely informational helper - the actual "why didn't this confirm"
    # case: nothing within STOP_MATCH_RADIUS_METERS, but there's still a
    # genuine closest stop ahead worth showing, with its real distance
    far_stops = [make_stop(0, 53.4800, -2.2400), make_stop(1, 53.5200, -2.2400)]
    stop, distance = matching.nearest_candidate_stop(far_stops, 53.4800, -2.2400, last_matched_sequence=0)
    assert stop["stop_sequence"] == 1
    assert distance > config.STOP_MATCH_RADIUS_METERS
