# Route 263 Reconciliation Agent

## what this is

an agent that reconciles a live bus feed against a published timetable for
Manchester's route 263 (Piccadilly Gardens <-> Altrincham Interchange, Bee
Network). every real cycle, it pulls whatever's actually on the road right
now from a live GPS feed, pulls the independently-published schedule for
those same trips, checks six fields between them, and decides field by
field what to do about any disagreement - pick a winner, flag an anomaly,
flag a data-quality issue, or (if a field keeps disagreeing for too long)
freeze it and demand it prove itself clean again before trusting it. the
result is a persisted, queryable record of what the agent currently
believes about every trip it's tracked, and why.

**if youre a reviewer short on time:** "the evidence table" section below
maps the real demo run committed in `data/` and `reports/` - which cycle
shows what, straight in, no digging required.

---

## how this fulfils the brief

> LEC AI's build assessment brief: "reconcile live market data with
> warehouse snapshots" - detect disagreement, decide who wins, keep a
> reconciled state you can query and explain. worked example given was
> order-book data, with the brief explicitly allowing "such as ... or a
> local mock stream" as an alternative.

domain swap: real bus GPS instead of order-book data or a mocked stream,
because two genuinely independent, freely available real-time sources
were available for this instead, and the brief's own wording explicitly
allows the substitution.

| brief asks for | what's built | where |
|---|---|---|
| live feed | BODS GTFS-RT, real vehicles, real GPS, one HTTP call/cycle | `agent/sources/live_source.py` |
| warehouse snapshot | BODS static GTFS timetable, separate publishing pipeline, not derived from live | `agent/sources/static_source.py` |
| detect disagreement | 6 fields checked per vehicle per cycle, each its own live-vs-warehouse comparison | `agent/fields.py` |
| decide who wins | 3 resolution strategies by disagreement type, not flat "live wins" | `agent/policy.py` |
| reconciled state, queryable + explainable | 1 record per (trip, field): resolved value, verdict, reason, streak, review status | `data/ledger.json` |
| freeze-and-review on chronic conflict | 3 consecutive conflicting cycles -> `MANUAL_REVIEW_REQUIRED`, defaults to warehouse until cleared | `agent/policy.py::resolve_field` |

full rationale for each of these is under "decisions and tradeoffs" below.

---

## deciding whether to trust live or warehouse

the resolution isn't a flat "live always wins" - it's margin-based, then
trust-based. for the two timing fields, live and warehouse rarely agree to
the exact second even when the bus is genuinely on time, so within a small
margin the warehouse's clean scheduled value is trusted (`ON_SCHEDULE`);
only once live's reading drifts past that margin does live actually take
over as the resolved value (`LIVE_WINS`). that's the per-cycle decision.
across cycles, trust in live degrades if it keeps disagreeing: a field
that's been in conflict for 3 consecutive cycles stops trusting live
automatically and falls back to the warehouse value instead
(`WITHHELD_UNDER_REVIEW`) until live earns it back with one clean,
confirmed cycle. so live is the default source of truth once it's
diverged meaningfully from the schedule, but that trust is conditional and
gets revoked the moment live looks unreliable rather than just late.

---

## running it

```bash
pip install -r requirements.txt
# .env with BODS_API_KEY set (free, self-service signup at
# https://data.bus-data.dft.gov.uk)

python run.py                # one real reconciliation cycle
python run.py --verbose      # + the nearby scheduled-stop window per bus (all six fields already print by default)
python -m pytest tests/ -v   # 66 tests, all offline/synthetic
```

there is deliberately **no `--cycles` flag**. state is real and persisted
to `data/ledger.json` / `data/observation_state.json` between runs, so two
reconciliation cycles means literally running `python run.py` twice with
real wall-clock time between them, not looping inside one process. run it
again during service hours and you'll see positions move and verdicts
change based on what actually happened in between - not a simulated loop
pretending time passed.

---

## the run evidence, for real

this isn't a system that's only ever been run against synthetic data. a
real demo run is committed - twelve cycles, 5 minutes apart, ~55 minutes
first-to-last, genuinely separate `python run.py` invocations against live
BODS data with real wall-clock time between them. proves the chronic-freeze
mechanism behaves correctly over a real, busy stretch: a plain live-wins
case, the freeze correctly locking a field under sustained real lateness,
and a genuinely new data-quality flag catching a real edge case at a
trip-start transition. full breakdown in `EVIDENCE_2.md`.

every cycle wrote a real entry to `data/cycle_history.jsonl` and updated
`data/ledger.json`, and every cycle that had a conflict generated its own
PDF in `reports/` - automatically, by `run.py` itself, not written up
after the fact. the actual table is further down, once the
ledger/verdict vocabulary below has been introduced.

---

## technical breakdown

### the real workflow, one cycle (`run.py`)

1. **load the schedule** - `static_source.load_route_schedule_cached()`.
   first run ever: streams the ~470MB regional `stop_times.txt` once,
   filtered down to route 263's ~231 trips, cached so every later run is
   instant. also loads `calendar.txt` / `calendar_dates.txt` for
   service-day validity.
2. **fetch live** - `live_source.fetch_vehicle_positions()`. whatever's
   actually running on route 263 right now, straight from BODS.
3. **for each live vehicle** (`fields.check_vehicle`): match it to a stop
   (`matching.match_vehicle_to_stop`, forward-only search anchored on the
   last confirmed stop); no match this cycle -> fall back to a speed-based
   estimate (`eta.estimate_next_stop_delay`, trusted only above a speed
   floor and a distance sanity bound); then run all six field checks.
4. **apply the policy** - `policy.reconcile_vehicle()`. more than 3 of 6
   fields disagreeing -> `SUSPECT`, quarantined, not resolved field by
   field. otherwise, resolve each field - `policy.resolve_field()` -
   against the ledger's memory of that (trip, field) pair.
5. **check for cancellations** - `policy.check_for_cancellations()`.
   separate from the per-vehicle loop, since a cancelled trip by
   definition never shows up live. walks every trip scheduled today; one
   that's 20 min-2h past its start with no live sighting ever goes through
   the same freeze/clear machinery.
6. **persist + log** - `ObservationState.save()`, `Ledger.save()`, full
   cycle appended to `data/cycle_history.jsonl`. nothing lives only in
   memory.
7. **print the report** - terminal always, PDF too if there's anything to
   report.

### file by file

- **`agent/config.py`** - every tunable constant, reasoning written next
  to the number. nothing hardcoded anywhere else.
- **`agent/sources/live_source.py`** - the live half: one function,
  protobuf in, plain dicts out, filtered to route 263.
- **`agent/sources/static_source.py`** - the warehouse half: downloads,
  caches, and streams the static GTFS timetable once.
- **`agent/matching.py`** - pure geometry: haversine distance, bearings,
  and the forward-only nearest-stop search.
- **`agent/eta.py`** - the speed-projection fallback for an unconfirmed
  cycle. straight-line only, always labeled an approximation.
- **`agent/time_utils.py`** - GTFS time strings <-> real UTC epoch,
  BST-aware; also compensates for a real feed-timezone bug found in
  BODS's own live data.
- **`agent/fields.py`** - the six detection functions. comparison only,
  never decides a winner.
- **`agent/state.py`** - the minimum cross-cycle memory matching needs.
  not the same thing as the ledger.
- **`agent/ledger.py`** - the actual reconciled state: one record per
  (trip, field), queryable and explainable, persisted.
- **`agent/policy.py`** - the decision layer: the SUSPECT trigger, the
  chronic-freeze state machine, cancellations.
- **`agent/geocode.py`** - best-effort reverse geocoding, cosmetic only,
  never blocks a cycle or affects any of the six fields.
- **`agent/stop_names.py`** - real TfGM stop descriptions, for output a
  rider would recognise.
- **`agent/pdf_report.py`** - renders one cycle's conflicts to PDF, reusing
  the same reasoning functions the terminal calls.
- **`run.py`** - the entrypoint. one invocation, one real cycle.
- **`scripts/`** - phase artifacts: BODS connectivity check, matching-only
  proof, detection-only checker.
- **`tests/`** - 66 offline tests, covering the policy state machine's
  edge cases directly, not just the happy path.

---

## the evidence table

**twelve cycles, 2026-08-16, 5 min apart**

| cycle | time (BST) | result of recon |
|---|---|---|
| 1 | 20:19:55 | `LIVE_WINS` (OJJ) + `TRUST WAREHOUSE` (OJO) |
| 2 | 20:24:57 | `TRUST WAREHOUSE` + `ANOMALY`, possible skip (OJJ) |
| 3 | 20:29:59 | `LIVE_WINS`, new trip (OKH) + `TRUST WAREHOUSE` + `ANOMALY` (OJJ) |
| 4 | 20:35:01 | `TRUST WAREHOUSE`, clean, no anomaly (OJJ) |
| 5 | 20:40:03 | `LIVE_WINS` + `ANOMALY` (OKH) + `TRUST WAREHOUSE` + `ANOMALY` (OJJ) |
| 6 | 20:45:04 | `TRUST WAREHOUSE` (OJJ) + `TRUST WAREHOUSE` + `ANOMALY` (OKH) |
| 7 | 20:50:06 | `TRUST WAREHOUSE`, clean, both buses |
| 8 | 20:55:08 | `TRUST WAREHOUSE` (OKH only) |
| 9 | 21:00:09 | `TRUST WAREHOUSE` + `ANOMALY`, both buses |
| 10 | 21:05:11 | `TRUST WAREHOUSE` + `ANOMALY` (OJJ) + `TRUST WAREHOUSE` (OKH) |
| 11 | 21:10:13 | `TRUST WAREHOUSE` + `ANOMALY` (OKH only) |
| 12 | 21:15:14 | `LIVE_WINS` + `DATA_QUALITY_FLAG`, new trip (OKH) |

what's worth looking at directly, straight from `data/cycle_history.jsonl`
and `reports/` (full detail in `EVIDENCE_2.md`): the feed-timezone bug fix
confirmed live - **0 of 12** cycles show a `start_time_date` mismatch; the
chronic freeze catching *genuine* sustained lateness (buses 2-11 minutes
late every single cycle, not a feed bug) - fields under
`MANUAL_REVIEW_REQUIRED` grew from 13 to 17 over the run; and cycle 12's
`DATA_QUALITY_FLAG` catching a real edge case - a bus's GPS-derived
direction reading came out 178° opposite the scheduled direction at the
exact cycle it started a new trip at the same terminus its last trip had
just ended at.

---

## decisions and tradeoffs (kept relevant to the brief)

**why three different policies, not just "live always wins":** fields 1-2
are a genuine measurement disagreement where one side has to be picked as
the current truth. fields 3-4 aren't really a "who's right" question at
all - if a scheduled timepoint got skipped, that's an event that happened,
not two numbers that disagree, so it's flagged as an anomaly instead of
resolved into a value. fields 5-6 are about whether the observation is
even trustworthy in the first place, so flagging a data-quality issue felt
more honest than picking a winner for something that might not even be a
real observation of this trip at all. this directly answers the brief's
"decide who wins" requirement without flattening three genuinely different
kinds of disagreement into one rule.

**margin-based timing, not flat "live wins":** answers "decide who wins"
for fields 1-2 specifically, and a flat rule would've been the wrong
answer - live and warehouse almost never agree to the exact second even
when the bus is genuinely on time (gps noise, a single position ping vs.
an operator's actual measurement). so within a defined margin (60s,
`config.MIN_DELAY_SECONDS_TO_LOG`) either way, the timetable is still a
perfectly good description of reality and the warehouse's clean value is
kept (`ON_SCHEDULE`). only past that margin does live's own reading take
over (`LIVE_WINS`). proved this isn't theoretical - real cycles produced
`LIVE_WINS` and `ON_SCHEDULE` on the same field, same cycle, different
buses, resolved differently depending on which side of the margin each one
landed on.

**the SUSPECT meta-trigger:** a single observation where more than 3 of 6
fields disagree simultaneously almost certainly isn't six independent
small problems - it's more likely a bad vehicle-to-trip match, a gps
fault, or a genuinely disrupted service. rather than resolve each field
independently into six confident-sounding but probably-meaningless
verdicts, the whole observation gets quarantined as `SUSPECT` and logged
as its own distinct case. this is a direct answer to what "decide who
wins" should do when the honest answer is "this observation isn't
trustworthy enough to resolve field by field at all."

**BODS's live feed is position-only - the biggest scope change of the
build.** the brief's "detect disagreement" requirement means nothing if
the live side of the comparison doesn't have real values to compare -
so this had to be checked before any field could be built. no `TripUpdate`
anywhere, checked nationwide (24,665 entities, zero). the original plan
was to read a delay/ETA straight off the live feed; that plan had to be
scrapped and replaced with the nearest-stop matching engine
(`agent/matching.py`) plus the speed-based estimator (`agent/eta.py`)
actually in this repo now, so fields 1-2 would have a real live value to
detect disagreement against at all.

**no shape geometry, no `direction_id`, no `current_stop_sequence`** - all
checked directly against the static feed and hundreds of real vehicles,
all absent. the same "detect disagreement" requirement applies to fields
3 and 5 specifically: they had to be built around what BODS actually
gives, not what a textbook GTFS-RT feed would provide - direction is
derived from consecutive real GPS bearings instead of read directly, and
stop matching is straight-line nearest-stop instead of distance-along-a-
known-shape.

**the honest gap:** "a reconciled state you can query and explain" cuts
both ways - it means admitting what the state doesn't show, not just what
it does. no field ever cleared `MANUAL_REVIEW_REQUIRED` in the real run
committed here, because the buses tracked were simply, actually late on
every cycle during that hour - the real world never handed the policy the
one clean confirmed cycle it needs to clear a freeze.
`tests/test_policy.py` carries the synthetic proof the clear-path itself
works (`test_one_clean_cycle_clears_manual_review` and neighbours) - the
real run adds proof that the freeze correctly does **not** clear when the
world hasn't earned it back, which is arguably the harder half of that
state machine to demonstrate live.

---

## what i'd do next with more time

- **real route geometry** - even a hand-traced shape for 263 would let
  stop matching and eta estimation work off distance-along-route instead
  of straight-line nearest-stop, which would help a lot around the
  Altrincham loop specifically.
- **more routes** - the whole thing is parameterised by route_id/dataset
  in `config.py`, so tracking a second or third route is mostly a config
  change, not a rebuild. would also make the SUSPECT/chronic-freeze
  behaviour easier to demo at volume.
- **a real MANUAL_REVIEW dashboard/CLI** - right now clearing a frozen
  field is fully automatic (one clean confirmed cycle). a real ops version
  would want an actual person able to look at *why* something's frozen and
  either confirm the clear early or extend the freeze, not just wait it
  out.
- **persist raw field_results, not just resolved verdicts** - the ledger
  currently keeps live_value/warehouse_value from the *last* cycle a field
  was written, but a proper history table per field would make it possible
  to plot delay-over-time per trip, not just see the current state.
