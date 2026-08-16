# config for the route 263 reconciliation agent. all the stuff that would
# normally be hardcoded scattered through the codebase lives here instead.
#
# route 263 runs manchester piccadilly gardens <-> altrincham interchange,
# operated by bee network (was stagecoach manchester before the franchise
# takeover). confirmed present in both the bods static timetable and the
# live gtfs-rt feed on 2026-08-15 - see scripts/check_bods_connection.py.
#
# fallback routes if 263 ever stops publishing: 43 (manchester-stockport)
# or 192 (manchester-hyde/glossop), both long established on bods.

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

# --- field 1/2: timing -----------------------------------------------------

MIN_DELAY_SECONDS_TO_LOG = 60
# per the brief: "subject to a defined minimum-delay threshold below
# which its not worth logging as a conflict at all". a minute felt like
# the right floor - our own detection timestamp is exact to the second
# but its still a single position ping, not a precise operator
# measurement, so treating anything under a minute as noise rather than
# a real conflict seems fair.
#
# this is also the margin of error for who actually wins fields 1/2 (see
# agent/policy.py _resolve_verdict) - within a minute either way, the
# timetable is still a perfectly good description of reality so we keep
# the warehouse's clean scheduled value instead of a jittery live one.
# only past this margin does live's own reading become more trustworthy
# than the schedule, which is when it actually takes over.

# --- field 3: per-stop adherence --------------------------------------------

# only timepoint stops count for skip detection (see agent/fields.py) -
# checked route 263 directly: a real 49-stop trip only has 7 timepoints.
# checking all 49 against a ~15 min poll cycle would flag basically every
# cycle as "skipped a bunch of stops", which is just normal service, not
# an anomaly.

# --- field 4: trip level adherence -----------------------------------------

# nothing extra needed here yet - uses calendar.txt + calendar_dates.txt
# directly, see agent/fields.py

# --- field 5: direction ------------------------------------------------

MIN_DISPLACEMENT_METERS_FOR_BEARING = 80
# below this, two consecutive gps pings are too close together to trust
# the bearing between them - normal gps drift is often 10-50m on its own,
# so a bus that's barely moved (stuck in traffic, stopped at lights) would
# give a basically random bearing if we didn't skip the check here.

# --- eta estimation between confirmed stop matches --------------------

MIN_SPEED_MPS_FOR_ESTIMATE = 1.0
# below this (~3.6 km/h) we dont trust a speed derived from two position
# fixes enough to project an eta from it - the bus is stopped, queued, or
# at a light, and dividing by a near-zero speed gives a wildly unstable
# estimate. below this floor we just dont estimate that cycle rather than
# emit garbage.

MAX_ESTIMATE_DISTANCE_METERS = 2000
# real bug caught while building this: if a bus moves further than
# STOP_MATCH_LOOKAHEAD stops between two polls (easy to happen with sparse
# polling - 49 stops over a ~45 min trip is roughly a stop a minute), the
# matcher never catches back up and last_matched_stop_sequence gets stuck
# pointing at a stop the bus left behind ages ago. the eta estimator was
# then happily projecting against that stale "next stop" and producing
# things like "6826s late" - not a real finding, just stale tracking state
# treated as current. 2km is generously more than any real gap between
# adjacent stops on this route (a few hundred meters, worst case) - past
# that, we're not tracking the right stop anymore, so dont estimate at all
# rather than emit a number that looks precise but means nothing.

MAX_BEARING_DIFFERENCE_DEGREES = 90
# if the vehicles actual direction of travel is more than 90 degrees off
# from what its assigned trip expects, thats not "slightly off due to a
# bendy road", thats probably heading the wrong way entirely - a genuine
# vehicle-to-trip matching fault.

# --- field 6: start time/date -----------------------------------------------

MAX_START_TIME_DRIFT_SECONDS = 300
# how far live's reported start_time can differ from the trip's first
# scheduled stop time before we call it a mismatch rather than just
# normal early/late departure. 5 minutes, same rough ballpark as the
# official dft "on time" punctuality window for buses.

# --- phase 4: the actual policy --------------------------------------------

CHRONIC_CONFLICT_STREAK_THRESHOLD = 3
# how many consecutive cycles a field has to stay in conflict before we
# stop auto-resolving it and freeze it instead. at the brief's own ~15
# min polling cadence thats about 45 min of continuous disagreement -
# picked 3 over 2 because real bus delays genuinely flip back and forth
# cycle to cycle (catches up a bit, falls behind again), so 2 felt too
# quick to trigger on normal noise.

CANCELLATION_GRACE_PERIOD_SECONDS = 1200  # 20 min
# how long past a trip's scheduled start we wait, having never seen it
# live even once, before calling it cancelled rather than just running
# late. 20 min is well past the dft "on time" window (+6 min), so a trip
# that still hasn't shown up by then is very likely not coming at all.

CANCELLATION_MAX_LOOKBACK_SECONDS = 7200  # 2 hours
# the other half of the window - dont check trips that started MORE than
# 2h ago either. without this, a trip that ran and finished normally 12
# hours before we ever started running this script would show up as
# "never seen, therefore cancelled" forever, since we genuinely have no
# data either way for anything before we started watching. this bounds
# the check to "recently due and unaccounted for", not "the whole day's
# backlog".
