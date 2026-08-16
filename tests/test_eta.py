# tests for the speed-based eta estimation between confirmed stop
# matches. synthetic positions/timestamps, no network.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import eta, time_utils


def make_stop(seq, lat, lon, arrival="12:10:00", departure="12:10:00"):
    return {"stop_sequence": seq, "stop_lat": lat, "stop_lon": lon,
            "arrival_time": arrival, "departure_time": departure, "stop_name": f"Stop {seq}"}


# roughly 1100m apart (0.01 deg latitude ~ 1111m)
STOPS = [make_stop(0, 53.4800, -2.2400), make_stop(1, 53.4900, -2.2400, arrival="12:10:00")]


def test_no_estimate_without_a_prior_position():
    next_stop, predicted, detail = eta.estimate_next_stop_delay(
        STOPS, -1, 53.4800, -2.2400, 1000, None, "20260815"
    )
    assert next_stop is None


def test_no_estimate_when_speed_is_too_low():
    last_position = {"lat": 53.4800, "lon": -2.2400, "timestamp": 1000}
    # moved almost nothing in 60s - way under the trust floor
    next_stop, predicted, detail = eta.estimate_next_stop_delay(
        STOPS, -1, 53.48001, -2.2400, 1060, last_position, "20260815"
    )
    assert next_stop is None
    assert "too low" in detail


def test_no_estimate_when_no_next_stop_left():
    last_position = {"lat": 53.4800, "lon": -2.2400, "timestamp": 1000}
    next_stop, predicted, detail = eta.estimate_next_stop_delay(
        STOPS, 1, 53.4850, -2.2400, 1100, last_position, "20260815"  # already past the last stop
    )
    assert next_stop is None


def test_estimate_produced_when_moving_at_reasonable_speed():
    # 500m in 50s = 10 m/s (36 km/h), reasonable bus speed
    last_position = {"lat": 53.4800, "lon": -2.2400, "timestamp": 1000}
    next_stop, predicted_arrival, detail = eta.estimate_next_stop_delay(
        STOPS, -1, 53.4845, -2.2400, 1050, last_position, "20260815"
    )
    assert next_stop["stop_sequence"] == 0
    assert predicted_arrival is not None
    assert predicted_arrival > 1050  # still some distance left to cover


def test_no_estimate_when_next_stop_is_implausibly_far():
    # last_matched stuck way behind reality (the real bug this was built
    # to catch) - next_stop is technically "next" in sequence but 20+ km
    # away, which means tracking is stale, not that the bus teleported
    far_stops = [make_stop(0, 53.4800, -2.2400), make_stop(1, 53.6800, -2.2400)]  # ~22km apart
    last_position = {"lat": 53.4800, "lon": -2.2400, "timestamp": 1000}
    # last_matched=0 -> "next" is stop 1, which is genuinely ~22km away
    next_stop, predicted, detail = eta.estimate_next_stop_delay(
        far_stops, 0, 53.4850, -2.2400, 1050, last_position, "20260815"
    )
    assert next_stop is None
    assert "too far" in detail


def test_estimate_projects_forward_not_backward():
    # moving AWAY from the next stop should still produce a (large, late) estimate,
    # not crash or go negative in a way that breaks the math
    last_position = {"lat": 53.4800, "lon": -2.2400, "timestamp": 1000}
    next_stop, predicted_arrival, detail = eta.estimate_next_stop_delay(
        STOPS, -1, 53.4700, -2.2400, 1050, last_position, "20260815"
    )
    # moved south, away from stop 0 which is north - speed is still positive
    # (its a straight-line speed magnitude), estimate should still resolve
    assert next_stop is not None
    assert predicted_arrival > 1050
