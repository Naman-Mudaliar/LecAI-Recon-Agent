"""
tests for the gtfs time -> epoch conversion, including the annoying bits:
times past 24:00:00 for next-day trips, and bst vs gmt (uk clocks change
so the same "19:41:00" is a different utc instant depending on the date).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import time_utils


def test_simple_time_in_summer_bst():
    # 15th august is deep in bst (utc+1), so 19:41 local is 18:41 utc
    epoch = time_utils.gtfs_time_to_epoch("20260815", "19:41:00")
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert dt.hour == 18
    assert dt.minute == 41

def test_simple_time_in_winter_gmt():
    # january is gmt (utc+0), no offset to worry about
    epoch = time_utils.gtfs_time_to_epoch("20260115", "19:41:00")
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert dt.hour == 19
    assert dt.minute == 41


def test_past_midnight_time_rolls_to_next_day():
    # "25:10:00" on the 15th means 01:10 on the 16th, not some invalid time
    epoch = time_utils.gtfs_time_to_epoch("20260815", "25:10:00")
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert dt.day == 16
    assert dt.hour == 0  # 01:10 bst = 00:10 utc
    assert dt.minute == 10


def test_midnight_exactly():
    epoch = time_utils.gtfs_time_to_epoch("20260815", "00:00:00")
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert dt.day == 14  # bst means local midnight is still the previous utc day
    assert dt.hour == 23
