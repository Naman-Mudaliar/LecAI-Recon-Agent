"""
config for the route 263 reconciliation agent. all the stuff that would
normally be hardcoded scattered through the codebase lives here instead.

route 263 runs manchester piccadilly gardens <-> altrincham interchange,
operated by bee network (was stagecoach manchester before the franchise
takeover). confirmed present in both the bods static timetable and the
live gtfs-rt feed on 2026-08-15 - see scripts/check_bods_connection.py.

fallback routes if 263 ever stops publishing: 43 (manchester-stockport)
or 192 (manchester-hyde/glossop), both long established on bods.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BODS_API_KEY = os.environ.get("BODS_API_KEY", "")

BASE_URL = "https://data.bus-data.dft.gov.uk"

# --- route identity -------------------------------------------------------

ROUTE_SHORT_NAME = "263"

# these were found by searching the bods dataset catalogue for "Bee Network"
# datasets and checking which one's `lines` field contained "263" - see
# scripts/check_bods_connection.py for the actual discovery process, this
# is just the cached result so we don't have to re-search every run.
STATIC_DATASET_ID = 17472
STATIC_ROUTE_ID = "10477"  # this is BODS's internal route_id, not the "263" you see on the bus

# bods doesn't let you filter the gtfs-rt feed by route server-side (well,
# routeId is a param but it wants an id you dont really have ahead of time
# and it barely matches anything when we tried it) so instead we pull
# everything inside a bounding box around greater manchester and filter
# client-side by route_id after parsing.
MANCHESTER_BBOX = {
    "min_lon": -2.35,
    "min_lat": 53.35,
    "max_lon": -2.10,
    "max_lat": 53.55,
}

# --- static gtfs source -----------------------------------------------------

# bods publishes a converted bulk GTFS file per english region (not just
# per operator), and that's the one that actually has trips.txt /
# stop_times.txt / calendar.txt in the normal gtfs shape. greater
# manchester falls under "north_west".
GTFS_REGION = "north_west"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATIC_GTFS_ZIP_PATH = DATA_DIR / "nw_gtfs.zip"
STATIC_GTFS_EXTRACT_DIR = DATA_DIR / "nw_gtfs"

# --- stop matching (see NOTES / commit history for why this exists) --------

# bods's live feed is bare AVL - vehicle id, lat/lon, bearing, timestamp,
# trip identity. no current_stop_sequence, no stop_id, no delay, nothing.
# checked this properly: 0 out of 776 vehicles in a manchester-wide
# snapshot had current_stop_sequence populated. and route 263's trips all
# have an empty shape_id in the static feed too, so there's no route
# geometry to project onto either.
#
# so instead of a proper distance-along-shape projection we do nearest
# stop by straight line distance, anchored by our own memory of the last
# stop we matched for that trip (persisted in the ledger across cycles) -
# we only ever search forward from there. that's what stops us from
# accidentally matching a stop the bus already passed on an earlier loop
# of the route.
STOP_MATCH_RADIUS_METERS = 350
# bus stops in urban manchester can be as close as ~250-300m apart, so this
# needs to be tight enough not to straddle two stops at once, but loose
# enough to survive normal gps drift (usually 10-50m, occasionally worse
# near tall buildings/tunnels). 350m is a starting point, not gospel -
# worth revisiting once we see real match rates.

STOP_MATCH_LOOKAHEAD = 8
# only consider the next 8 scheduled stops after our last confirmed match.
# keeps the search cheap and stops a single bad gps ping from jumping the
# match miles down the route.
