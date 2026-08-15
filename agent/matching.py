"""
the nearest-stop matching engine. this is the thing that stands in for
the "eta already in the feed" idea from the original plan, which turned
out not to exist for bods (see agent/config.py for the full story).

approach ("option 1"): no route geometry to project onto, no
current_stop_sequence from the live feed to anchor on - so instead we
anchor on our OWN memory. every trip we're tracking remembers the highest
stop_sequence it's confidently matched so far (persisted across cycles in
the ledger), and each new cycle we only look at stops AHEAD of that. pick
whichever of those candidate stops is closest to the live position, and
if its within the match radius, call it matched and move the anchor
forward.

this is what stops a route that loops back near itself (263 does exactly
this around altrincham interchange) from matching the vehicle to the
wrong pass of the loop - we're never doing a blind nearest-stop search
across the whole route, only the next few stops after wherever we last
confirmed the bus actually was.
"""

import math

from agent import config


def haversine_meters(lat1, lon1, lat2, lon2):
    """distance between two lat/lon points in meters. good enough for bus
    stop spacing distances (hundreds of meters) - not worried about the
    tiny errors this formula has over huge distances since Manchester
    isn't huge."""
    r = 6371000  # earth radius, meters
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_degrees(lat1, lon1, lat2, lon2):
    """compass bearing (0-360, 0=north) from point 1 to point 2. used for
    field 5 - since the live feed never gives us direction_id (checked,
    0 out of 759 vehicles had it), we work out which way the bus is
    actually pointed by comparing two of its own positions instead."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def bearing_difference(b1, b2):
    """smallest angle between two bearings, 0-180. cant just subtract
    since bearings wrap around at 360/0."""
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)


def match_vehicle_to_stop(trip_stops, live_lat, live_lon, last_matched_sequence=-1):
    """trip_stops is the ordered stop list for one trip (from the static
    schedule). returns the matched stop dict plus the distance to it in
    meters, or (None, None) if nothing within range this cycle - which
    just means the bus is between stops, not that somethings wrong.
    """
    candidates = [s for s in trip_stops if s["stop_sequence"] > last_matched_sequence]
    candidates = candidates[:config.STOP_MATCH_LOOKAHEAD]

    if not candidates:
        return None, None

    best_stop = None
    best_distance = None
    for stop in candidates:
        if stop["stop_lat"] is None or stop["stop_lon"] is None:
            continue
        d = haversine_meters(live_lat, live_lon, stop["stop_lat"], stop["stop_lon"])
        if best_distance is None or d < best_distance:
            best_distance = d
            best_stop = stop

    if best_stop is None or best_distance > config.STOP_MATCH_RADIUS_METERS:
        return None, best_distance

    return best_stop, best_distance
