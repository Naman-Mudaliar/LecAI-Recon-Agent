# Route 263 Reconciliation Agent — Program Outline

One-sentence version: an agent that reconciles live bus positions for route 263
against the published timetable, decides who wins field by field using an
explicit policy, and freezes a field that stays wrong for too long until it
proves itself again.

This document walks through the actual workflow (what happens, in order, on
one real cycle) and then what every file in the repo is responsible for.

---

## The two sources, for real

- **Live** — `agent/sources/live_source.py`. One HTTP call to BODS's GTFS-RT
  feed (`/api/v1/gtfsrtdatafeed/`), filtered to a Manchester bounding box,
  then filtered again client-side to route 263's `route_id` (`10477`).
  Returns raw `VehiclePosition` data: vehicle id, trip id, start date/time,
  lat/lon, timestamp. Nothing else — checked early on that BODS's "GTFS-RT"
  is a converted view of their SIRI-VM location data, so there's no
  `TripUpdate`, no delay, no predicted arrival anywhere in it, for any route.

- **Warehouse** — `agent/sources/static_source.py`. BODS's static GTFS
  timetable for the whole North West region (a converted bulk download,
  not the per-operator TransXChange), containing the normal
  `trips.txt` / `stop_times.txt` / `stops.txt` / `calendar.txt` /
  `calendar_dates.txt` files. Built in advance by human schedulers,
  completely independent of the live feed's pipeline.

---

## The real workflow, one cycle (`run.py`)

1. **Load the schedule** — `static_source.load_route_schedule_cached()`.
   First run ever: streams the ~470MB regional `stop_times.txt` once,
   filtered down to route 263's ~231 trips, and caches the result to
   `data/route_263_schedule.json` so every later run is instant. Also loads
   `calendar.txt` / `calendar_dates.txt` for service-day validity.

2. **Fetch live** — `live_source.fetch_vehicle_positions()`. Whatever's
   actually running on route 263 right now, straight from BODS.

3. **For each live vehicle** (`fields.check_vehicle`):
   - **Match it to a stop** — `matching.match_vehicle_to_stop()`. Since the
     live feed has no `current_stop_sequence` and route 263 has no shape
     geometry (both checked directly, both absent), matching is nearest-stop
     by straight-line distance, searched forward-only from the last stop we
     confidently matched for that trip (persisted memory, not geometry).
     This is what stops the route's own loop near Altrincham Interchange
     from causing a wrong match.
   - **No match this cycle?** Try a speed-based estimate instead —
     `eta.estimate_next_stop_delay()`. Smoothed speed from the last two real
     GPS fixes, projected forward to the next scheduled stop. Only trusted
     above a speed floor (not stopped/queued) and a distance sanity bound
     (catches the matcher having fallen behind, see "real bugs" below).
   - **Run all six field checks** — `fields.check_arrival_timing`,
     `check_departure_timing`, `check_per_stop_adherence`,
     `check_trip_level_adherence`, `check_direction`, `check_start_time_date`.
     Each returns `{field, live_value, warehouse_value, disagreement, source, detail}`.
     `disagreement` is `True` / `False` / `None` — `None` means "nothing to
     compare this cycle," not "they agree."

4. **Apply the policy** — `policy.reconcile_vehicle()`:
   - Count how many of the six fields disagree. **More than 3 → `SUSPECT`**:
     the whole observation is quarantined and logged as its own case, no
     field gets individually resolved this cycle.
   - Otherwise, resolve each field — `policy.resolve_field()` — against the
     ledger's memory of that (trip, field) pair: streak count, review status,
     last resolved value.

5. **Check for cancellations** — `policy.check_for_cancellations()`. Separate
   from the per-vehicle loop, since a cancelled trip by definition never
   shows up live. Walks every trip scheduled for today; if it's 20 min–2h
   past its scheduled start with no live sighting ever, flags it — through
   the same `resolve_field` machinery, so a chronically-absent trip
   chronic-freezes too.

6. **Persist + log** — `ObservationState.save()`, `Ledger.save()`, and the
   full cycle appended to `data/cycle_history.jsonl`. Nothing lives only in
   memory; the next invocation picks up exactly where this one left off.

7. **Print the report** — `run.py:print_report()`.

There is deliberately no `--cycles` loop. State is real and persisted, so two
reconciliation cycles means running `python run.py` twice with real time in
between — not looping inside one process.

---

## The six fields and their policy

| # | Field | Live comes from | Warehouse comes from | Policy |
|---|---|---|---|---|
| 1 | `arrival_timing` | matched stop's real timestamp (or a speed-based estimate) | `stop_times.txt` `arrival_time` | **LIVE_WINS** by default, below 60s not even logged |
| 2 | `departure_timing` | same observation, compared to a different scheduled field | `stop_times.txt` `departure_time` | **LIVE_WINS**, same threshold |
| 3 | `per_stop_adherence` | did we ever get matched near a `timepoint` stop we've since passed | scheduled stop list | **ANOMALY** if a timepoint was skipped — not resolved as a value |
| 4 | `trip_level_adherence` | is this trip's `service_id` valid today; did a scheduled trip ever show up live at all | `calendar.txt` / `calendar_dates.txt` | **ANOMALY** (unscheduled extra, or cancellation) |
| 5 | `direction` | bearing between last two real GPS fixes | bearing between the two nearest scheduled stops | **DATA_QUALITY_FLAG** on mismatch (feed never gives `direction_id` directly — checked, 0/759 vehicles had it) |
| 6 | `start_time_date` | `TripDescriptor.start_time`/`start_date` | trip's first scheduled stop time + calendar validity | **DATA_QUALITY_FLAG** on mismatch |

Fields 1–2 pick a winner. Fields 3–6 never do — they flag, because a skip,
a cancellation, or a mismatched identity means something actually happened
or something's wrong with the match, not "two numbers disagree."

**Chronic-conflict freeze** (fields 1–2 only get this in a way that matters,
since fields 3–6 don't carry a "resolved value" to protect): 3 consecutive
cycles in disagreement → `MANUAL_REVIEW_REQUIRED`, value frozen at the last
resolved number, still logging what's actually observed underneath. Clears
after **one** clean cycle — but only if that clean cycle came from a
**confirmed** stop match, not an estimate. A run of agreeing estimates can
build evidence but can never clear a freeze on its own.

---

## File by file

**`agent/config.py`** — every tunable constant, each with the reasoning
behind the number: match radius, lookahead window, delay threshold, chronic
streak threshold, cancellation grace/lookback windows, eta trust floors.
Nothing hardcoded anywhere else in the codebase.

**`agent/sources/live_source.py`** — the live half. One function,
`fetch_vehicle_positions()`, protobuf in, plain dicts out.

**`agent/sources/static_source.py`** — the warehouse half. Downloads/caches
the regional GTFS zip, streams the huge `stop_times.txt` exactly once,
builds and caches the route's full schedule, and exposes `load_calendar()` /
`load_calendar_dates()` for service-day checks.

**`agent/matching.py`** — pure geometry: `haversine_meters`,
`bearing_degrees`, `bearing_difference`, and `match_vehicle_to_stop` (the
forward-only nearest-stop search).

**`agent/eta.py`** — the speed-projection fallback for when there's no
confirmed stop match this cycle. Entirely straight-line, no route shape —
route 263 has none. Every result it produces is explicitly an approximation,
never treated as ground truth downstream.

**`agent/time_utils.py`** — GTFS time strings (which allow `25:10:00` for
past-midnight trips, and are always local time) to real UTC epoch seconds,
correctly handling BST/GMT.

**`agent/fields.py`** — the six detection functions plus `check_vehicle()`,
which ties matching + estimation + all six checks together for one live
vehicle. Detection only — never decides a winner.

**`agent/state.py`** — `ObservationState`: the minimum cross-cycle memory
the *matching* needs to work at all (last matched stop per trip, last
position per vehicle). Not the same thing as the ledger.

**`agent/ledger.py`** — `Ledger`: the actual reconciled state the brief asks
for — one record per (trip, field) with resolved value, verdict, reason,
streak, review status. Queryable, explainable, persisted.

**`agent/policy.py`** — the phase 4 decision layer: `reconcile_vehicle()`
(the suspect meta-trigger), `resolve_field()` (the chronic-conflict state
machine), `check_for_cancellations()`.

**`run.py`** — the real entrypoint. One invocation, one real cycle, full
report printed and logged.

**`scripts/check_bods_connection.py`** — phase 1 artifact: proves API access
and confirms route 263 is real in both feeds.

**`scripts/match_live_to_schedule.py`** — phase 2 artifact: proves the
matching engine works against real data, before the ledger/policy existed.

**`scripts/check_disagreements.py`** — phase 3 artifact: detection only, no
policy, for inspecting raw field-by-field disagreement.

**`tests/`** — 57 tests, all synthetic/offline: `test_matching.py`,
`test_eta.py`, `test_time_utils.py`, `test_fields.py`, `test_policy.py`
(the chronic-freeze state machine, confirmed-vs-estimated gating, and the
suspect trigger are all pinned down here with edge cases, not just the
happy path).

---

## Real findings along the way (worth defending on camera)

- **BODS's GTFS-RT feed is position-only.** No `TripUpdate` anywhere,
  checked nationwide (24,665 entities, zero). The entire "read the ETA off
  the feed" plan from the original brief had to be replaced with the
  matching/estimation engine above.
- **`current_stop_sequence` and `direction_id` are never populated** either
  (checked against hundreds of real vehicles) — both fields 3 and 5 had to
  be rebuilt around what the feed actually gives.
- **Route 263 has no shape geometry** in the static feed — every trip's
  `shape_id` is empty, ruling out proper distance-along-route projection.
- **A real BST bug in BODS's own feed** — field 6 catches every active
  vehicle showing an exact `-3600s` start-time drift, consistent with the
  feed not applying the daylight-saving offset when serializing
  `start_time`. Verified it wasn't our bug (both sides of the comparison go
  through the identical conversion function).
- **A real bug in our own matcher**, caught before it shipped: if a bus
  moves further than the lookahead window between two polls, tracking gets
  stuck on a stop the bus already passed, and the eta estimator was happily
  projecting against it — producing numbers like "6826s late" that looked
  precise and meant nothing. Fixed with a distance sanity bound
  (`MAX_ESTIMATE_DISTANCE_METERS`).

---

## Running it

```bash
pip install -r requirements.txt
# .env with BODS_API_KEY set
python run.py                       # one real cycle
python run.py --verbose             # + full six-field dump per bus
python -m pytest tests/ -v          # 57 tests, offline
```
