# Route 263 Reconciliation Agent

an agent that reconciles a live bus feed against a published timetable for
Manchester's route 263 (Piccadilly Gardens <-> Altrincham Interchange, Bee
Network), decides who wins field by field using a policy i actually wrote
down and can defend, and freezes a field that stays wrong for too long
until it proves itself again on a real, separately-invoked cycle.

this is my answer to LEC AI's build assessment brief ("reconcile live
market data with warehouse snapshots" - live feed + warehouse snapshot,
detect disagreement, decide who wins, keep a reconciled state you can
query and explain). the brief's own example was order-book data, but it
explicitly says "such as ... or a local mock stream" - i went with real
bus GPS data instead because i could get my hands on two genuinely
independent, freely available real-time sources for it (more on that
below), and honestly it felt like a more interesting problem than another
crypto price feed.

**if youre a reviewer short on time:** `EVIDENCE.md` maps the real,
six-cycle, ~50-minute demo run committed in `data/` and `reports/` -
which cycle shows what, straight in.

---

## the two sources, for real

- **live** - BODS's GTFS-RT feed. one HTTP call, filtered to a Manchester
  bounding box then to route 263's own route_id. turns out BODS's "GTFS-RT"
  is a converted view of their SIRI-VM vehicle location data, so its bare
  AVL - vehicle id, lat/lon, bearing, trip identity, timestamp. no
  TripUpdate, no delay field, no predicted arrival anywhere in it, for any
  route (checked nationwide, 24,665 entities, zero). the original plan was
  to just read a delay field off the feed - that plan died fast, see
  "real findings" below for what replaced it.

- **warehouse** - BODS's static GTFS timetable for the whole North West
  region, the normal `trips.txt` / `stop_times.txt` / `stops.txt` /
  `calendar.txt` / `calendar_dates.txt` shape. built in advance by human
  schedulers, on a completely separate publishing pipeline from the live
  feed. genuinely independent sources, not one derived from the other -
  which is the whole point of a reconciliation exercise like this.

---

## running it

```bash
pip install -r requirements.txt
# .env with BODS_API_KEY set (free, self-service signup at
# https://data.bus-data.dft.gov.uk)

python run.py                # one real reconciliation cycle
python run.py --verbose      # + the full six-field dump per bus, not just the notable ones
python -m pytest tests/ -v   # 57 tests, all offline/synthetic
```

there is deliberately **no `--cycles` flag**. state is real and persisted
to `data/ledger.json` / `data/observation_state.json` between runs, so two
reconciliation cycles means literally running `python run.py` twice with
real wall-clock time between them, not looping inside one process. run it
again ~10-15 min later during service hours and you'll see positions move
and verdicts change based on what actually happened in between - not a
simulated loop pretending time passed.

---

## the policy - who wins, and why

six fields get checked per live vehicle, each compared against the
matched trip's scheduled record. i split them into three policy
categories because they're not the same kind of disagreement - a bus
running late is a different problem than a bus skipping a stop, which is
different again from a bus's *identity* looking wrong.

| # | field | live comes from | warehouse comes from | policy |
|---|---|---|---|---|
| 1 | arrival timing | matched stop's real GPS timestamp (or a speed-based estimate between stops) | `stop_times.txt` `arrival_time` | **margin-based**, see below |
| 2 | departure timing | same observation, compared to a different scheduled field | `stop_times.txt` `departure_time` | **margin-based**, see below |
| 3 | per-stop adherence | did we ever get matched near a scheduled `timepoint` stop we've since passed | scheduled stop list | **ANOMALY** if a timepoint was skipped - flagged, not resolved as a value |
| 4 | trip-level adherence | is this trip's `service_id` valid today; did a scheduled trip ever show up live at all | `calendar.txt` / `calendar_dates.txt` | **ANOMALY** (unscheduled extra, or a possible cancellation) |
| 5 | direction | bearing between the vehicle's last two real GPS fixes | bearing implied by the trip's nearest scheduled stop pair | **DATA QUALITY FLAG** on mismatch |
| 6 | start time/date | the trip's reported start time/date on the live feed | trip's first scheduled stop time + calendar validity | **DATA QUALITY FLAG** on mismatch |

**why three different policies and not just "live always wins":** fields
1-2 are a genuine measurement disagreement where one side has to be
picked as the current truth. fields 3-4 aren't really a "who's right"
question at all - if a scheduled timepoint got skipped, that's an event
that happened, not two numbers that disagree, so it gets flagged as an
anomaly rather than resolved into a value. fields 5-6 are about whether
the observation is even trustworthy in the first place (wrong direction,
wrong start time = possible mismatch fault), so flagging it as a data
quality issue felt more honest than picking a winner for something that
might not even be a real observation of this trip at all.

**fields 1-2, margin-based (not flat "live wins"):** live and warehouse
almost never agree to the exact second even when the bus is genuinely on
time - gps noise, a single position ping vs an operator's actual
measurement. so within a defined margin (currently 60s,
`config.MIN_DELAY_SECONDS_TO_LOG`) either way, the timetable is still a
perfectly good description of reality and we keep the warehouse's clean
scheduled value (`ON_SCHEDULE`) rather than a jittery live one. only past
that margin does live's own reading actually take over (`LIVE_WINS`).
proved this isn't just theoretical - ran 4 real cycles 10 min apart on
2026-08-16 and got `LIVE_WINS` and `ON_SCHEDULE` on the *same field, same
cycle, different buses*, resolved differently depending on which side of
the margin each one actually landed on.

**the meta-trigger - more than 3 of 6 fields disagreeing at once:** a
single vehicle observation where more than half the fields disagree
simultaneously almost certainly isn't six independent small problems -
it's more likely a bad vehicle-to-trip match, a gps fault, or a
genuinely disrupted service. rather than resolve each field
independently and produce six confident-sounding but probably-meaningless
verdicts, the whole observation gets quarantined as `SUSPECT` and logged
as its own distinct case that cycle. see `policy.reconcile_vehicle()`.

**chronic-conflict freeze:** a field that disagrees for
`CHRONIC_CONFLICT_STREAK_THRESHOLD` (3) consecutive cycles stops getting
auto-resolved and flips to `MANUAL_REVIEW_REQUIRED` instead - the resolved
value freezes at its last known-good number while the reason it's frozen
keeps logging what's actually being observed underneath. it clears after
exactly **one** clean cycle, but only if that clean cycle came from a real
confirmed stop match, not a speed-based estimate - a run of shaky
estimates that happen to agree shouldn't be enough to clear a manual
review flag on its own. this is genuinely observation-driven, not a
scripted sequence: whether a field is frozen this cycle depends entirely
on what got written to `data/ledger.json` on *previous, separate*
invocations, which is exactly the "decide what to do next based on what
you observe" bit the brief asks for.

**cancellations** get checked separately from the per-vehicle loop, since
a cancelled trip by definition never shows up live at all - there's no
vehicle to hang a check off. instead the agent walks every trip scheduled
for today and flags any that are 20 min to 2 hours past their scheduled
start with no live sighting ever, through the same freeze/clear machinery,
so a trip that's chronically never seen also ends up in manual review.

---

## a live finding that shaped the code: gps positions go stale but don't disappear

not something the brief asked for, but worth calling out since it's a
real bug i found and fixed, not a hypothetical edge case. BODS's live feed
doesn't drop a vehicle's entry when it stops actually reporting - it just
leaves the last real position sitting there. checked one live snapshot
directly and found vehicles 11-21 seconds old (genuinely live) sitting
right next to ones **10.7 hours** and **13.5 hours** old (yesterday
evening's positions, still present in "today's" feed). without handling
this, a stale echo gets matched and reported like a currently active bus,
schedule window and all - which is exactly what it looked like when i
first ran this at 10:30am and saw a bus "currently" scheduled to depart at
23:11 that night. fixed with a straightforward freshness filter
(`config.MAX_VEHICLE_POSITION_AGE_SECONDS`, 10 min) that excludes stale
vehicles from the cycle entirely and reports them separately as ignored,
rather than silently dropping them or - worse - silently trusting them.

---

## other real findings (worth defending on camera)

- **BODS's GTFS-RT feed is position-only.** no `TripUpdate` anywhere,
  checked nationwide. the entire "read the eta off the feed" plan from my
  first pass had to be scrapped and replaced with a nearest-stop matching
  engine (`agent/matching.py`) plus a speed-based estimator
  (`agent/eta.py`) for when there's no confirmed match.
- **route 263 has no shape geometry** in the static feed - every trip's
  `shape_id` is empty. rules out projecting distance-along-route properly,
  so stop matching is nearest-stop by straight-line distance instead,
  anchored by memory of the last stop matched for that trip so it only
  ever searches forward (stops the route's own loop near Altrincham from
  causing a false match backwards).
- **`current_stop_sequence` and `direction_id` are never populated**
  either (checked against hundreds of real vehicles) - fields 3 and 5 had
  to be built around what the feed actually gives, not what a textbook
  GTFS-RT feed would.
- **a real BST bug in BODS's own feed** - field 6 catches every active
  vehicle showing an exact -3600s start-time drift, consistent with the
  live feed not applying the daylight-saving offset when it serializes
  `start_time`. checked it wasn't my own bug first - both sides of that
  comparison go through the identical time-conversion function.
- **a real bug in my own matcher**, caught before it shipped: if a bus
  moved further than the lookahead window between two polls, tracking got
  stuck on a stop the bus had already passed, and the eta estimator
  happily projected against it, producing numbers like "6826s late" that
  looked precise and meant absolutely nothing. fixed with a distance
  sanity bound (`MAX_ESTIMATE_DISTANCE_METERS`).

---

## project layout

- `agent/config.py` - every tunable constant, each with the reasoning
  behind the actual number, not just the number.
- `agent/sources/live_source.py` / `static_source.py` - the two
  independent sources.
- `agent/matching.py` - nearest-stop matching, pure geometry.
- `agent/eta.py` - the speed-projection fallback for when there's no
  confirmed match this cycle.
- `agent/time_utils.py` - gtfs time strings <-> real utc epoch, BST-aware.
- `agent/fields.py` - the six detection functions. detection only, never
  decides a winner.
- `agent/state.py` - the minimum cross-cycle memory the matching needs to
  work at all.
- `agent/ledger.py` - the actual reconciled state: one record per (trip,
  field), resolved value, verdict, reason, streak, review status.
  queryable and explainable, persisted to `data/ledger.json`.
- `agent/policy.py` - the decision layer described above.
- `run.py` - the real entrypoint, one invocation = one real cycle.
- `scripts/` - phase artifacts kept for the walkthrough (bods connectivity
  check, matching-only proof, detection-only disagreement checker).
- `tests/` - 57 offline tests, no network, covering the policy state
  machine's edge cases directly (chronic freeze/clear, confirmed vs
  estimated gating, the suspect trigger), not just the happy path.

---

## what i'd do next with more time

- **real route geometry** - even a hand-traced shape for 263 would let
  stop matching (and eta estimation) work off distance-along-route instead
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
