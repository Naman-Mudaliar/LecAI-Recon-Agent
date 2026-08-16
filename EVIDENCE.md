# Reading the evidence

this is a map of the actual demo run committed in this repo, not a
description of the code (thats README.md) - if youre a reviewer trying to
find "did this really run more than once, for real, over real time" fast,
start here.

## how it was produced

six real, separately-invoked `python run.py` cycles, spaced ~10 minutes
apart, against live BODS data, on 2026-08-16:

| cycle | time (BST) |
|---|---|
| 1 | 14:37:40 |
| 2 | 14:47:40 |
| 3 | 14:57:41 |
| 4 | 15:07:41 |
| 5 | 15:17:42 |
| 6 | 15:27:42 |

no `--cycles` flag exists in this codebase on purpose - this is six
genuinely separate process invocations, ~50 real minutes apart to
first-to-last, not a loop. `data/observation_state.json`'s
`last_position` timestamps and `data/ledger.json`'s `updated_at` fields
independently confirm the same real gaps if you want to check.

## whats in each file

- **`data/cycle_history.jsonl`** - one JSON line per cycle, append-only.
  the rawest evidence - every field result, every verdict, every bus
  position, for all six cycles. this is what `run.py` itself writes every
  time it runs, not something built after the fact for this evidence set.
- **`data/ledger.json`** - the final reconciled state after cycle 6. one
  record per (trip, field): resolved value, verdict, reason, conflict
  streak, review status. this is the actual "queryable, explainable
  state" the brief asks for - open it and you can see exactly what the
  agent currently believes about every trip/field it's tracked, and why.
- **`data/observation_state.json`** - the matching engine's own memory
  (last confirmed stop + last gps fix per vehicle/trip). less interesting
  to read directly, included because its part of what genuinely
  persisted across the six real invocations.
- **`reports/conflicts_<timestamp>.pdf`** - one per cycle that actually
  had a conflict (all six did). the readable version: per bus, per
  conflicting field, live vs warehouse vs verdict vs the recon logic vs
  the underlying eta calc vs exactly what it'll take to clear. generated
  automatically by `run.py` itself each cycle, not written up separately.

## if you only look at four things, look at these

- **a plain live-wins conflict** - cycle 1
  (`reports/conflicts_20260816_133740.pdf`), bus `YX74OKA`: both timing
  fields came back `LIVE_WINS` on the very first cycle, before any
  chronic history existed to complicate it. the simplest case, first.

- **the chronic-conflict freeze actually triggering** - cycle 3
  (`reports/conflicts_20260816_135741.pdf`): `start_time_date` had been
  in conflict for 3 straight cycles by this point on most buses
  (`YX74OJE`, `YX74OJJ`, `YX74OKA` all flip from `DATA_QUALITY_FLAG` to
  `WITHHELD_UNDER_REVIEW` here) - this is the real BST bug (bods's live
  feed not applying the daylight-saving offset) being genuinely chronic,
  not transient, so it correctly stays frozen through cycle 6 and never
  clears.

- **the suspect meta-trigger firing** - cycle 4
  (`reports/conflicts_20260816_140741.pdf`), bus `YX74OJE`: 4 of 6 fields
  disagreed simultaneously (arrival, departure, direction, start time) -
  quarantined as `SUSPECT` instead of resolved field-by-field. the one
  cycle in this run where that happened.

- **the margin-of-error split favouring the warehouse** - cycle 5
  (`reports/conflicts_20260816_141742.pdf`) onward, bus `YX74OJE`:
  `ON_SCHEDULE` appears for the first time - live landed within the ±60s
  margin, so the resolved value is the warehouse's clean scheduled time,
  not live's slightly-jittery one. sits right next to `WITHHELD_UNDER_REVIEW`
  on the *same bus, same cycle* - two different fields, two different
  live-vs-warehouse outcomes, decided independently.

## what this run does *not* show

being honest about the gap: no field ever cleared out of
`MANUAL_REVIEW_REQUIRED` in this run, because the thing thats frozen
(the BST drift bug) is a permanent upstream issue, not a temporary one -
it will never produce the one clean confirmed cycle needed to auto-clear.
`tests/test_policy.py` proves the clear-path works
(`test_one_clean_cycle_clears_manual_review` and friends) with synthetic
data where a clean cycle is possible to construct; this real run just
never got to demonstrate it live, honestly, because the real world didn't
cooperate.

## reproducing / extending this

```bash
python run.py                # one more real cycle, appends to the same files
python run.py --verbose      # same, with the full six-field dump per bus
```

state keeps accumulating from here - running it again doesn't reset
anything, cycle 7 would read cycle 6's ledger as its starting point,
same as cycle 2 read cycle 1's.
