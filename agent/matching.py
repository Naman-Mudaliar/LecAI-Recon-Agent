# the nearest-stop matching engine. this is the thing that stands in for
# the "eta already in the feed" idea from the original plan, which turned
# out not to exist for bods (see agent/config.py for the full story).
#
# approach: no route geometry to project onto, no current_stop_sequence
# from the live feed to anchor on - so instead we anchor on our OWN
# memory. every trip we're tracking remembers the highest stop_sequence
# it's confidently matched so far (persisted across cycles), and each new
# cycle we only ever look at stops AHEAD of that - pick whichever is
# closest to the live position, and if it's within the match radius, call
# it matched and move the anchor forward. that forward-only rule is the
# real protection against a route that loops back near itself (263 does
# exactly this around altrincham interchange) matching the wrong pass of
# the loop - it can never re-match a stop already passed, loop or not.
#
# earlier version of this also capped the search to the next few stops
# only, on the theory that unbounded search risked snapping to a wrong,
# much-later stop that happened to be spatially close (the loop). checked
# that against every real stop pair on this route (both directions): zero
# pairs 9+ stops apart in sequence are within the match radius of each
# other. the cap wasn't buying any real safety on this route - it was
# only ever causing a trip to get stuck unconfirmed the moment a real gap
# in polling let the bus travel further than the cap allowed (confirmed
# live, twice: a trip stuck 24 stops behind reality, and another stuck 11
# stops behind after just 7 minutes). the forward-only rule already does
# the actual protective work; searching every remaining stop every cycle
# is one straightforward rule instead of a capped search plus a second,
# time-gated fallback search bolted on top of it.

import math

from agent import config


def haversine_meters(lat1, lon1, lat2, lon2):
    # distance between two lat/lon points in metres. good enough for bus
    # stop spacing distances (hundreds of metres) - not worried about the
    # tiny errors this formula has over huge distances since Manchester
    # isn't huge
    r = 6371000  # earth radius, metres
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_degrees(lat1, lon1, lat2, lon2):
    # compass bearing (0-360, 0=north) from point 1 to point 2. used for
    # field 5 - since the live feed never gives us direction_id (checked,
    # 0 out of 759 vehicles had it), we work out which way the bus is
    # actually pointed by comparing two of its own positions instead
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def bearing_difference(b1, b2):
    # smallest angle between two bearings, 0-180. cant just subtract since
    # bearings wrap around at 360/0
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)


def _nearest_candidate(trip_stops, live_lat, live_lon, last_matched_sequence):
    # shared candidate search - every stop strictly ahead of the anchor
    # (or every stop at all, on a trip's first-ever match, see
    # match_vehicle_to_stop's docstring), no cap. returns the closest one
    # and its distance, without applying the confirm-radius cutoff yet.
    if last_matched_sequence == -1:
        candidates = trip_stops
    else:
        candidates = [s for s in trip_stops if s["stop_sequence"] > last_matched_sequence]

    best_stop = None
    best_distance = None
    for stop in candidates:
        if stop["stop_lat"] is None or stop["stop_lon"] is None:
            continue
        d = haversine_meters(live_lat, live_lon, stop["stop_lat"], stop["stop_lon"])
        if best_distance is None or d < best_distance:
            best_distance = d
            best_stop = stop
    return best_stop, best_distance


def match_vehicle_to_stop(trip_stops, live_lat, live_lon, last_matched_sequence=-1):
    # trip_stops is the ordered stop list for one trip (from the static
    # schedule). returns the matched stop dict plus the distance to it in
    # metres, or (None, distance-to-nearest) if nothing's within the
    # match radius this cycle - which just means the bus is between
    # stops, not that something's wrong.
    #
    # bootstrap case (this trip has never been matched before,
    # last_matched_sequence == -1): search every stop on the route. a
    # vehicle's first-ever observed position is just as likely to be
    # mid-route as at the beginning - BODS doesn't hand us a trip's
    # history, we only see whatever's live when we happen to poll.
    best_stop, best_distance = _nearest_candidate(trip_stops, live_lat, live_lon, last_matched_sequence)

    if best_stop is not None and best_distance <= config.STOP_MATCH_RADIUS_METERS:
        return best_stop, best_distance

    return None, best_distance


def nearest_candidate_stop(trip_stops, live_lat, live_lon, last_matched_sequence=-1):
    # same search as match_vehicle_to_stop, minus the confirm-radius
    # cutoff - purely informational, for explaining WHY nothing confirmed
    # this cycle ("closest scheduled stop was still 900m away"). never
    # used to advance any persisted state - only match_vehicle_to_stop's
    # result is allowed to do that.
    return _nearest_candidate(trip_stops, live_lat, live_lon, last_matched_sequence)
