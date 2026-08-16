# tests for the phase 4 policy - who wins, anomaly vs data-quality
# framing, the suspect meta-trigger, and the chronic-conflict
# freeze/clear cycle. all in-memory, never touches disk (Ledger only
# reads/writes when you ask it to, and we never call .save()).

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, policy
from agent.ledger import Ledger

NOW = datetime(2026, 8, 15, 18, 0, 0, tzinfo=timezone.utc)


def fresh_ledger():
    return Ledger(path=Path("this/path/does/not/exist.json"))


def make_result(field, disagreement, detail="detail", live_value="live", warehouse_value="warehouse"):
    return {"field": field, "live_value": live_value, "warehouse_value": warehouse_value,
            "disagreement": disagreement, "detail": detail}


# --- basic verdict shape per field type ------------------------------------

def test_live_wins_field_resolves_to_live_value_on_disagreement():
    ledger = fresh_ledger()
    result = policy.resolve_field("T1", make_result("arrival_timing", True, live_value="+90s"), ledger, NOW)
    assert result["verdict"] == "LIVE_WINS"
    assert result["resolved_value"] == "+90s"


def test_live_wins_field_no_conflict_when_within_threshold():
    ledger = fresh_ledger()
    result = policy.resolve_field("T1", make_result("arrival_timing", False), ledger, NOW)
    assert result["verdict"] == "NO_CONFLICT"


def test_anomaly_field_flags_but_doesnt_pick_a_winner():
    ledger = fresh_ledger()
    result = policy.resolve_field("T1", make_result("per_stop_adherence", True), ledger, NOW)
    assert result["verdict"] == "ANOMALY"


def test_data_quality_field_flags_but_resolved_value_is_none():
    ledger = fresh_ledger()
    result = policy.resolve_field("T1", make_result("direction", True), ledger, NOW)
    assert result["verdict"] == "DATA_QUALITY_FLAG"
    assert result["resolved_value"] is None


def test_no_observation_with_no_prior_history():
    ledger = fresh_ledger()
    result = policy.resolve_field("T1", make_result("arrival_timing", None), ledger, NOW)
    assert result["verdict"] == "NO_OBSERVATION"


def test_no_observation_leaves_prior_state_untouched():
    ledger = fresh_ledger()
    policy.resolve_field("T1", make_result("arrival_timing", True), ledger, NOW)
    before = ledger.get_field("T1", "arrival_timing")
    result = policy.resolve_field("T1", make_result("arrival_timing", None), ledger, NOW)
    assert result == before


# --- chronic conflict streak / freeze / clear -----------------------------

def test_streak_increments_on_repeated_disagreement():
    ledger = fresh_ledger()
    for _ in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD - 1):
        result = policy.resolve_field("T1", make_result("arrival_timing", True), ledger, NOW)
    assert result["conflict_streak"] == config.CHRONIC_CONFLICT_STREAK_THRESHOLD - 1
    assert result["review_status"] == "NORMAL"


def test_streak_resets_immediately_on_a_clean_cycle():
    ledger = fresh_ledger()
    policy.resolve_field("T1", make_result("arrival_timing", True), ledger, NOW)
    policy.resolve_field("T1", make_result("arrival_timing", True), ledger, NOW)
    result = policy.resolve_field("T1", make_result("arrival_timing", False), ledger, NOW)
    assert result["conflict_streak"] == 0


def test_crossing_threshold_enters_manual_review():
    ledger = fresh_ledger()
    result = None
    for _ in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD):
        result = policy.resolve_field("T1", make_result("arrival_timing", True), ledger, NOW)
    assert result["review_status"] == "MANUAL_REVIEW_REQUIRED"


def test_withheld_under_review_freezes_the_resolved_value():
    ledger = fresh_ledger()
    for i in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD):
        policy.resolve_field("T1", make_result("arrival_timing", True, live_value=f"+{90+i}s"), ledger, NOW)
    frozen_value = ledger.get_field("T1", "arrival_timing")["resolved_value"]

    # keeps disagreeing with a DIFFERENT live value - should stay frozen at the old one
    result = policy.resolve_field("T1", make_result("arrival_timing", True, live_value="+999s"), ledger, NOW)
    assert result["verdict"] == "WITHHELD_UNDER_REVIEW"
    assert result["resolved_value"] == frozen_value
    assert result["resolved_value"] != "+999s"


def test_one_clean_cycle_clears_manual_review():
    ledger = fresh_ledger()
    for _ in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD):
        policy.resolve_field("T1", make_result("arrival_timing", True), ledger, NOW)
    assert ledger.get_field("T1", "arrival_timing")["review_status"] == "MANUAL_REVIEW_REQUIRED"

    # a single clean cycle - not two, not three - clears it per the brief's exact spec
    result = policy.resolve_field("T1", make_result("arrival_timing", False, live_value="+5s"), ledger, NOW)
    assert result["review_status"] == "NORMAL"
    assert result["conflict_streak"] == 0
    assert result["verdict"] == "NO_CONFLICT"
    assert result["resolved_value"] == "+5s"  # resumed auto-resolving immediately


# --- suspect meta-trigger --------------------------------------------------

def make_vehicle(trip_id="T1"):
    return {"trip_id": trip_id, "vehicle_id": "V1"}


def test_four_of_six_disagreeing_triggers_suspect():
    ledger = fresh_ledger()
    results = [
        make_result("arrival_timing", True), make_result("departure_timing", True),
        make_result("per_stop_adherence", True), make_result("trip_level_adherence", True),
        make_result("direction", False), make_result("start_time_date", False),
    ]
    outcome = policy.reconcile_vehicle(make_vehicle(), results, ledger, NOW)
    assert outcome["suspect"] is True
    assert outcome["resolutions"] == []


def test_three_of_six_disagreeing_does_not_trigger_suspect():
    ledger = fresh_ledger()
    results = [
        make_result("arrival_timing", True), make_result("departure_timing", True),
        make_result("per_stop_adherence", True), make_result("trip_level_adherence", False),
        make_result("direction", False), make_result("start_time_date", False),
    ]
    outcome = policy.reconcile_vehicle(make_vehicle(), results, ledger, NOW)
    assert outcome["suspect"] is False
    assert len(outcome["resolutions"]) == 6


def test_suspect_cycle_does_not_touch_the_ledger():
    ledger = fresh_ledger()
    results = [
        make_result("arrival_timing", True), make_result("departure_timing", True),
        make_result("per_stop_adherence", True), make_result("trip_level_adherence", True),
        make_result("direction", True), make_result("start_time_date", False),
    ]
    policy.reconcile_vehicle(make_vehicle(), results, ledger, NOW)
    assert ledger.get_field("T1", "arrival_timing") is None  # never written


def test_reconcile_vehicle_marks_trip_as_seen_even_when_suspect():
    ledger = fresh_ledger()
    results = [make_result(f, True) for f in ["arrival_timing", "departure_timing", "per_stop_adherence", "trip_level_adherence"]]
    results += [make_result("direction", False), make_result("start_time_date", False)]
    policy.reconcile_vehicle(make_vehicle(), results, ledger, NOW)
    assert ledger.get_trip_first_seen("T1") is not None


# --- cancellation detection -------------------------------------------------

CAL = {"SVC": {"weekdays": set(range(7)), "start_date": "20260101", "end_date": "20261231"}}
CAL_DATES = {}


def make_schedule_trip(departure_time):
    return {"service_id": "SVC", "stops": [{"departure_time": departure_time, "arrival_time": departure_time}]}


def test_cancellation_not_flagged_within_grace_period():
    # NOW is 18:00 utc = 19:00 bst local. trip due 18:55 local - only 5 min
    # overdue, still within the 20 min grace period.
    ledger = fresh_ledger()
    schedule = {"T1": make_schedule_trip("18:55:00")}
    results = policy.check_for_cancellations(schedule, CAL, CAL_DATES, ledger, NOW, "20260815")
    assert results == []


def test_cancellation_flagged_when_overdue_and_never_seen():
    # due 17:30 local - 90 min overdue, past grace, well within the 2h lookback
    ledger = fresh_ledger()
    schedule = {"T1": make_schedule_trip("17:30:00")}
    results = policy.check_for_cancellations(schedule, CAL, CAL_DATES, ledger, NOW, "20260815")
    assert len(results) == 1
    assert results[0][0] == "T1"


def test_cancellation_not_flagged_too_far_in_the_past():
    # due 15:00 local - 4 hours overdue, too long to tell, might've run
    # fine before we ever started watching
    ledger = fresh_ledger()
    schedule = {"T1": make_schedule_trip("15:00:00")}
    results = policy.check_for_cancellations(schedule, CAL, CAL_DATES, ledger, NOW, "20260815")
    assert results == []


# --- confirmed vs estimated source gating ----------------------------------

def test_estimated_disagreement_still_builds_the_streak():
    ledger = fresh_ledger()
    result = None
    for _ in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD):
        r = make_result("arrival_timing", True, live_value="~+90s")
        r["source"] = "estimated"
        result = policy.resolve_field("T1", r, ledger, NOW)
    assert result["review_status"] == "MANUAL_REVIEW_REQUIRED"


def test_clean_estimated_cycle_cannot_clear_manual_review():
    ledger = fresh_ledger()
    for _ in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD):
        r = make_result("arrival_timing", True, live_value="~+90s")
        r["source"] = "estimated"
        policy.resolve_field("T1", r, ledger, NOW)
    assert ledger.get_field("T1", "arrival_timing")["review_status"] == "MANUAL_REVIEW_REQUIRED"

    clean_estimate = make_result("arrival_timing", False, live_value="~+5s")
    clean_estimate["source"] = "estimated"
    result = policy.resolve_field("T1", clean_estimate, ledger, NOW)
    assert result["review_status"] == "MANUAL_REVIEW_REQUIRED"  # still stuck
    assert result["verdict"] == "WITHHELD_UNDER_REVIEW"


def test_clean_confirmed_cycle_clears_review_even_after_estimated_history():
    ledger = fresh_ledger()
    for _ in range(config.CHRONIC_CONFLICT_STREAK_THRESHOLD):
        r = make_result("arrival_timing", True, live_value="~+90s")
        r["source"] = "estimated"
        policy.resolve_field("T1", r, ledger, NOW)

    clean_confirmed = make_result("arrival_timing", False, live_value="+5s")
    clean_confirmed["source"] = "confirmed"
    result = policy.resolve_field("T1", clean_confirmed, ledger, NOW)
    assert result["review_status"] == "NORMAL"
    assert result["verdict"] == "NO_CONFLICT"


def test_clean_estimate_while_normal_does_not_reset_an_existing_streak():
    ledger = fresh_ledger()
    r = make_result("arrival_timing", True, live_value="~+90s")
    r["source"] = "estimated"
    policy.resolve_field("T1", r, ledger, NOW)  # streak -> 1

    clean_estimate = make_result("arrival_timing", False, live_value="~+5s")
    clean_estimate["source"] = "estimated"
    result = policy.resolve_field("T1", clean_estimate, ledger, NOW)
    assert result["conflict_streak"] == 1  # unchanged, not reset to 0
    assert result["verdict"] == "NO_CONFLICT"  # still resolves normally though


def test_fields_without_a_source_key_behave_as_before():
    # fields 3-6 dont set "source" at all - should default to confirmed
    # behaviour (a clean cycle resets the streak like always)
    ledger = fresh_ledger()
    policy.resolve_field("T1", make_result("direction", True), ledger, NOW)
    result = policy.resolve_field("T1", make_result("direction", False), ledger, NOW)
    assert result["conflict_streak"] == 0


def test_cancellation_not_flagged_if_already_seen():
    ledger = fresh_ledger()
    ledger.mark_trip_seen("T1", NOW.isoformat())
    schedule = {"T1": make_schedule_trip("17:30:00")}
    results = policy.check_for_cancellations(schedule, CAL, CAL_DATES, ledger, NOW, "20260815")
    assert results == []
