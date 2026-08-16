# observation state - the small bit of cross-cycle memory the field
# checks actually need to make correct observations. NOT the
# reconciliation ledger (streaks, frozen/manual-review flags,
# whos-winning-per-field) - thats a phase 4 thing built on top of this.
# this is just "what did we last see" so a single cycle isn't working
# blind.
#
# two things get remembered:
#   - last_matched_stop_sequence per trip: how far along the trip we've
#     confidently matched so far. used by the matching search (only look
#     ahead of here), and by the skip checker in agent/fields.py - any
#     timepoint stop strictly between the old and new value that we
#     didn't just match is a stop we went past without ever being
#     detected near.
#   - last_position per vehicle: lat/lon/timestamp, so field 5
#     (direction) has a previous point to compare the current one
#     against.
#
# persists to data/observation_state.json between runs, same reasoning as
# everywhere else in this project - state has to survive across separate
# real invocations, not just live in memory for one script run.

import json

from agent import config

STATE_PATH = config.DATA_DIR / "observation_state.json"


class ObservationState:
    def __init__(self, path=STATE_PATH):
        self.path = path
        self._data = self._load()

    def _load(self):
        if not self.path.exists():
            return {"trips": {}, "vehicles": {}}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    # --- per trip -----------------------------------------------------

    def get_trip_state(self, trip_id):
        return self._data["trips"].get(trip_id, {"last_matched_stop_sequence": -1})

    def update_trip_last_matched(self, trip_id, last_matched_stop_sequence):
        trip_state = self._data["trips"].setdefault(trip_id, {"last_matched_stop_sequence": -1})
        trip_state["last_matched_stop_sequence"] = max(
            trip_state["last_matched_stop_sequence"], last_matched_stop_sequence
        )

    # --- per vehicle ----------------------------------------------------

    def get_vehicle_last_position(self, vehicle_id):
        return self._data["vehicles"].get(vehicle_id)

    def update_vehicle_position(self, vehicle_id, lat, lon, timestamp):
        self._data["vehicles"][vehicle_id] = {"lat": lat, "lon": lon, "timestamp": timestamp}
