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
STOP_MATCH_RADIUS_METERS = 450
# revised from an initial 350m after exactly the "worth revisiting once we
# see real match rates" case this comment used to warn about: a real trip
# that had confirmed cleanly every single cycle for 5 cycles straight
# missed by just 4m (354m) on the 6th. checked real consecutive-stop gaps
# across the whole static feed to find out why: median gap is ~283m, but
# that's skewed by dense city-centre stops - plenty of real suburban gaps
# on this route run 400-900m (Poplar Road -> Crossford Bridge alone is
# 925m), a lot further than the "~250-300m apart, urban manchester"
# assumption 350m was originally set from. 450m sits comfortably past the
# real miss above while staying under the ~481m p90 gap, so it still won't
# usually span a full gap between two consecutive real stops. straddling
# isn't actually driven by this constant anyway - the nearest-candidate
# pick already happens before the radius check, so a wider radius changes
# the confidence bar for calling something confirmed, not which candidate
# wins when several are in range.
#
# the search itself used to also cap how far ahead it would look (only the
# next 8 scheduled stops past the anchor), on top of this radius, meant to
# stop a single bad gps ping from snapping the match onto the wrong pass
# of the route's own loop near Altrincham. dropped that cap after checking
# it against every real stop pair on this route: zero pairs 9+ stops apart
# in sequence are within match radius of each other in either direction,
# so it wasn't buying real safety - it was only ever causing a trip to get
# stuck unconfirmed the moment a real gap in polling let the bus travel
# further than the cap allowed (confirmed live, twice - once 24 stops
# behind, once 11 stops behind after just 7 minutes). the forward-only
# search (agent/matching.py) already does the real protective work here.

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
# real bug caught while building this: if a bus goes multiple cycles in a
# row without confirming a stop within STOP_MATCH_RADIUS_METERS (easy to
# happen with sparse polling - 49 stops over a ~45 min trip is roughly a
# stop a minute), last_matched_stop_sequence gets stuck
# pointing at a stop the bus left behind ages ago. the eta estimator was
# then happily projecting against that stale "next stop" and producing
# things like "6826s late" - not a real finding, just stale tracking state
# treated as current. 2km is generously more than any real gap between
# adjacent stops on this route (a few hundred metres, worst case) - past
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

# --- live feed freshness ----------------------------------------------------

MAX_VEHICLE_POSITION_AGE_SECONDS = 600  # 10 min
# real finding: bods's live feed doesnt drop a vehicle's entry when it
# stops actually reporting - it just leaves the last real position sitting
# there. checked directly in one snapshot: some vehicles were 11-21s old
# (genuinely live right now) sitting right next to ones 10.7h and 13.5h
# old (last night's positions, still present in "today's" feed). without
# this filter a stale echo gets matched and reported like a currently
# active bus, schedule window and all, from whatever trip it happened to
# be on the last time it actually moved. 10 min is comfortably past the
# real ~15 min poll cadence so it wont trip on a bus thats just between
# polls, but it catches anything thats genuinely stopped reporting.

CANCELLATION_MAX_LOOKBACK_SECONDS = 7200  # 2 hours
# the other half of the window - dont check trips that started MORE than
# 2h ago either. without this, a trip that ran and finished normally 12
# hours before we ever started running this script would show up as
# "never seen, therefore cancelled" forever, since we genuinely have no
# data either way for anything before we started watching. this bounds
# the check to "recently due and unaccounted for", not "the whole day's
# backlog".
