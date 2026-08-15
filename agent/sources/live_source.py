"""
live source: bods gtfs-rt feed, vehicle positions only.

turns out bods's "gtfs-rt" is a converted version of their SIRI-VM location
data, so it only ever contains VehiclePosition entities - no TripUpdate,
no delay, no predicted arrival times. this is just raw avl (automatic
vehicle location): where is the bus right now, nothing more. see
agent/config.py for the longer version of why that matters for this
project.
"""

import requests
from google.transit import gtfs_realtime_pb2

from agent import config

GTFS_RT_URL = f"{config.BASE_URL}/api/v1/gtfsrtdatafeed/"


def fetch_vehicle_positions(route_id=config.STATIC_ROUTE_ID):
    """hits the live feed, filters down to just our route, returns plain
    dicts instead of protobuf objects so the rest of the codebase doesn't
    need to know or care about protobuf."""
    params = {
        "api_key": config.BODS_API_KEY,
        "boundingBox": ",".join(str(v) for v in [
            config.MANCHESTER_BBOX["min_lon"],
            config.MANCHESTER_BBOX["min_lat"],
            config.MANCHESTER_BBOX["max_lon"],
            config.MANCHESTER_BBOX["max_lat"],
        ]),
    }
    resp = requests.get(GTFS_RT_URL, params=params, timeout=15)
    resp.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)

    vehicles = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        if v.trip.route_id != route_id:
            continue
        vehicles.append({
            "entity_id": entity.id,
            "vehicle_id": v.vehicle.id,
            "trip_id": v.trip.trip_id,
            "route_id": v.trip.route_id,
            "start_date": v.trip.start_date,
            "start_time": v.trip.start_time,
            "direction_id": v.trip.direction_id if v.trip.HasField("direction_id") else None,
            "schedule_relationship": v.trip.schedule_relationship,
            "lat": v.position.latitude,
            "lon": v.position.longitude,
            "bearing": v.position.bearing if v.position.HasField("bearing") else None,
            "timestamp": v.timestamp,
        })
    return vehicles
