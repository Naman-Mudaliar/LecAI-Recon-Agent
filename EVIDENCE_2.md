# Reading the evidence, round two

this is the second real run, done later the same day as `EVIDENCE.md`'s
six-cycle demo, after some real changes landed in the policy/matching code
in between. if `EVIDENCE.md` is "did this really run more than once, for
real", this file is "does it hold up over a longer stretch, and what
changed since the first run."

## how it was produced

twelve real, separately-invoked `python run.py --verbose` cycles, spaced
5 minutes apart, against live BODS data, on 2026-08-16, picking up
mid-flight from whatever state the ledger was already in that day (not a
reset - state persisting across unrelated invocations is the whole point):

| cycle | time (BST) | report |
|---|---|---|
| 1  | 20:19:55 | `reports/conflicts_20260816_191955.pdf` |
| 2  | 20:24:57 | `reports/conflicts_20260816_192457.pdf` |
| 3  | 20:29:59 | `reports/conflicts_20260816_192959.pdf` |
| 4  | 20:35:01 | `reports/conflicts_20260816_193501.pdf` |
| 5  | 20:40:03 | `reports/conflicts_20260816_194003.pdf` |
| 6  | 20:45:04 | `reports/conflicts_20260816_194504.pdf` |
| 7  | 20:50:06 | `reports/conflicts_20260816_195006.pdf` |
| 8  | 20:55:08 | `reports/conflicts_20260816_195508.pdf` |
| 9  | 21:00:09 | `reports/conflicts_20260816_200009.pdf` |
| 10 | 21:05:11 | `reports/conflicts_20260816_200511.pdf` |
| 11 | 21:10:13 | `reports/conflicts_20260816_201013.pdf` |
| 12 | 21:15:14 | `reports/conflicts_20260816_201514.pdf` |

first-to-last real span: ~55 minutes. same rule as the first run - every
cycle is its own `python run.py` process, no `--cycles` flag, no loop
inside one process. this run appended to the same `data/cycle_history.jsonl`
and `data/ledger.json` the first run and the rest of that day's activity
already wrote to - lines 16-27 of `cycle_history.jsonl` are this run,
specifically.

## what changed in the code between the two runs

worth calling out because it explains why this run's numbers look
different from `EVIDENCE.md`'s, not because the live data changed:

- **the BST bug got compensated for, not just detected.** the first run's
  big finding was BODS's live feed not applying the daylight-saving offset
  to `start_time`, causing an exact -3600s drift on every vehicle, every
  cycle, forever - that's the freeze that never cleared in `EVIDENCE.md`.
  `agent/time_utils.py` now has `gtfs_time_to_epoch_fixed_gmt`, used only
  for that one live field, which cancels the bug out exactly (and is a
  no-op outside BST, since GMT and Europe/London agree in winter). see
  `agent/fields.py`'s `check_start_time_date` for the full reasoning
  written inline.
- **a frozen field now resolves to the warehouse's value, not "whatever
  live last said before it stopped being trusted."** `agent/policy.py`'s
  `resolve_field` used to keep serving `prior["resolved_value"]` while
  under review; now it explicitly defaults to `field_result["warehouse_value"]`.
  this is the actual behavior behind the terminal/PDF wording change from
  "FROZEN" to "TRUST WAREHOUSE" made earlier in this session - the display
  text now matches what the code has genuinely been doing since this
  change landed, not just a relabel.
- **a trip's first-ever stop match no longer gets flagged as having
  skipped every timepoint before it.** `agent/fields.py`'s
  `check_per_stop_adherence` had a real gap: `prior_last_matched == -1`
  (never-seen-before) was being treated the same as "we've been watching
  and missed some stops," producing a false ANOMALY on every trip's first
  sighting. fixed with an explicit `prior_last_matched == -1` guard.

## what this run shows that the first one didn't

- **the BST fix, confirmed live, not just in code.** zero of the twelve
  cycles show a `start_time_date` mismatch, across every vehicle sighted
  (`YX74 OJJ`, `YX74 OJO`, `YX74 OKH`). in the first run this field was
  wrong on effectively every observation. this is the actual before/after
  proof that the fix works, not just that it compiles.
- **the chronic freeze catching something real, not a bug this time.**
  `arrival_timing`/`departure_timing` stayed under `MANUAL_REVIEW_REQUIRED`
  for `YX74 OKH` (streak climbed to 9) and `YX74 OJO` (streak 7) across
  this run - not because of a feed quirk, but because those buses were
  genuinely, consistently late: 2 to nearly 11 minutes late, every single
  cycle, all real GPS-observed lateness during evening traffic. the freeze
  is doing exactly what it's for - refusing to keep reporting a live
  number that's stopped being trustworthy as *reconciled state*, even
  though the underlying live delay readings are individually accurate.
  17 (trip, field) pairs are under review by the end of this run, up
  from 13 at the start of it.
- **`trip_level_adherence` entering chronic review for the first time.**
  two trips (streak 8 each) now sit in `MANUAL_REVIEW_REQUIRED` for
  whether they're running at all, not just for timing - a side effect of
  `resolve_field`'s chronic-freeze path being generic across all six
  fields rather than hardcoded to fields 1-2. wasn't observed in the
  first run.
- **a new, genuinely different `DATA_QUALITY_FLAG` case.** cycle 12,
  bus `YX74 OKH`, brand new trip just starting at Piccadilly Gardens
  (stop sequence 0, its very first match): GPS-derived direction read
  14°, schedule expected 191° - 178° apart, essentially opposite.
  reading the log, this looks like exactly the edge case you'd expect
  from a direction check built on "last two real GPS fixes": at the
  instant a vehicle flips from finishing one trip to starting the next
  at the same terminus, its two most recent real positions still belong
  to the *previous* trip's approach, not the new trip's departure. the
  field caught a real, explainable data-quality wrinkle it was never
  actually shown before in six shorter cycles.
- **`per_stop_adherence` flagging a different "possible skip" almost
  every cycle it has data.** `YX74 OJJ` alone: skip: Royal Northern
  College of Music (cycle 2), skip: Trafford Bar (cycle 3), skip:
  Stretford Mall (cycle 5), skip: Marks and Spencer (cycle 6), skip:
  George Richards Way (cycle 9). each is a genuinely different stop, not
  the same flag persisting - meaning at 5-minute polling on a route with
  closely-spaced timepoints, the bus is routinely covering more than one
  timepoint between polls without a GPS fix landing near it. this is a
  real limitation worth being honest about: the flag is correctly doing
  what it's specified to do (catch an unconfirmed timepoint), but at this
  polling cadence on this route, "unconfirmed" is going to fire often
  enough that a reviewer should read it as "we didn't get a fix near this
  stop," not "the bus definitely skipped it."

## what this run still doesn't show

same honest gap as the first run: no field cleared out of
`MANUAL_REVIEW_REQUIRED` here either. this time it's not an unfixable
upstream bug like the BST case - it's that the buses on this route were
simply, actually late by a few minutes on every single cycle during this
window, so the one clean **confirmed** cycle the policy requires to clear
a freeze never came up in real traffic during this hour. `test_policy.py`
still carries the synthetic proof that the clear path itself works
(`test_one_clean_cycle_clears_manual_review` and friends) - this run adds
more real evidence of the freeze correctly *not* clearing when the world
genuinely hasn't earned it back, which is arguably the more interesting
half of that state machine to see live.

## reproducing / extending this

```bash
python run.py --verbose      # one more real cycle, appends to the same files
```

state keeps accumulating - nothing about this run reset anything from the
first one, and a future run picks up from cycle 12 here the same way this
run picked up from wherever the day's cycles had already gotten to.
