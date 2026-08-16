# speed-based eta estimation for the gap between confirmed stop matches.
#
# straight line distance, not distance-along-route - route 263 has no
# shape geometry in the static feed (checked, every trip's shape_id is
# empty, see agent/config.py), so theres no polyline to project onto.
# this is a real approximation, not ground truth, which is exactly why
# every estimate produced here gets tagged "estimated" downstream rather
# than "confirmed" - see agent/fields.py and agent/policy.py for how that
# tag changes behaviour (an estimate can move the resolved value day to
# day, but it can never clear a chronic-conflict freeze - only an actual
# confirmed stop match can do that).
#
# no signal/junction/dwell modelling either - just "how fast were you
# actually moving between your last two real gps fixes, project that
# forward". least accurate right when a bus is about to stop at a light
# it cant see coming. thats a real limitation, not hidden by the source
# tag, just made visible by it.

from agent import config, matching, time_utils


def estimate_next_stop_delay(trip_stops, last_matched_stop_sequence, live_lat, live_lon, live_timestamp,
                              last_position, start_date):
    # returns (next_stop, predicted_arrival_epoch, detail) if we can
    # produce a trustworthy estimate this cycle, or (None, None, reason)
    # if we cant - too little history, too slow to trust, or no next stop
    # left to project towards.
    if last_position is None:
        return None, None, "no prior position yet, cant derive speed"

    time_delta = live_timestamp - last_position["timestamp"]
    if time_delta <= 0:
        return None, None, "no time elapsed since last position, cant derive speed"

    distance_moved = matching.haversine_meters(last_position["lat"], last_position["lon"], live_lat, live_lon)
    speed = distance_moved / time_delta

    if speed < config.MIN_SPEED_MPS_FOR_ESTIMATE:
        return None, None, f"speed too low to trust ({speed:.2f} m/s over {time_delta:.0f}s) - likely stopped/queued"

    next_stop = next((s for s in trip_stops if s["stop_sequence"] > last_matched_stop_sequence), None)
    if next_stop is None or next_stop["stop_lat"] is None:
        return None, None, "no next scheduled stop to project towards"

    distance_remaining = matching.haversine_meters(live_lat, live_lon, next_stop["stop_lat"], next_stop["stop_lon"])
    if distance_remaining > config.MAX_ESTIMATE_DISTANCE_METERS:
        # last_matched_stop_sequence is almost certainly stale - the bus
        # moved further than the matcher's lookahead could catch between
        # polls, so "next_stop" isnt really next anymore. dont project
        # against a stop this far away, the number would look precise and
        # mean nothing.
        return None, None, (
            f"next scheduled stop ({next_stop['stop_name']}) is {distance_remaining:.0f}m away - "
            "too far to be genuinely 'next', tracking is probably stale"
        )
    seconds_remaining = distance_remaining / speed
    predicted_arrival_epoch = live_timestamp + seconds_remaining

    detail = (f"{speed:.1f} m/s over last {time_delta:.0f}s, "
              f"{distance_remaining:.0f}m straight-line to {next_stop['stop_name']}")
    return next_stop, predicted_arrival_epoch, detail
