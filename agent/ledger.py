# the reconciled state ledger - this is the actual "what do we currently
# believe is true, and why" store the brief asks for (a state that can be
# queried and explained). different from agent/state.py's ObservationState,
# which only tracks what the matching logic needs to make correct
# observations in the first place. this is downstream of that - it holds
# policy OUTCOMES: resolved values, verdicts, conflict streaks,
# manual-review flags.
#
# one record per (trip_id, field_name), plus a small trip_first_seen map
# used for field 4's cancellation check (has this scheduled trip ever
# actually shown up live).
#
# persists to data/ledger.json between runs - same reasoning as everywhere
# else, state has to survive across separate real invocations for the
# chronic-conflict streaks to mean anything.

import json

from agent import config

LEDGER_PATH = config.DATA_DIR / "ledger.json"


class Ledger:
    def __init__(self, path=LEDGER_PATH):
        self.path = path
        self._data = self._load()

    def _load(self):
        if not self.path.exists():
            return {"fields": {}, "trip_first_seen": {}}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    @staticmethod
    def _key(trip_id, field_name):
        return f"{trip_id}|{field_name}"

    def get_field(self, trip_id, field_name):
        return self._data["fields"].get(self._key(trip_id, field_name))

    def set_field(self, trip_id, field_name, record):
        self._data["fields"][self._key(trip_id, field_name)] = record

    def get_trip_first_seen(self, trip_id):
        return self._data["trip_first_seen"].get(trip_id)

    def mark_trip_seen(self, trip_id, timestamp_iso):
        if trip_id not in self._data["trip_first_seen"]:
            self._data["trip_first_seen"][trip_id] = timestamp_iso

    def all_fields_for_trip(self, trip_id):
        prefix = f"{trip_id}|"
        return {k[len(prefix):]: v for k, v in self._data["fields"].items() if k.startswith(prefix)}
