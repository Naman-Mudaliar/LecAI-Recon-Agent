"""
tests for the nearest-stop matching logic. all synthetic coords, no
network - the point is to pin down the matching behaviour (forward-only
search, radius cutoff, lookahead cap) without needing a live bus.
"""

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


def test_lookahead_caps_the_search_window():
    # sat right on stop 9 (the last one) but last_matched is -1, so with a
    # lookahead smaller than 9 we should NOT find it
    original_lookahead = config.STOP_MATCH_LOOKAHEAD
    config.STOP_MATCH_LOOKAHEAD = 3
    try:
        stop, distance = matching.match_vehicle_to_stop(TRIP_STOPS, 53.4890, -2.2400, last_matched_sequence=-1)
        assert stop is None or stop["stop_sequence"] <= 2
    finally:
        config.STOP_MATCH_LOOKAHEAD = original_lookahead
