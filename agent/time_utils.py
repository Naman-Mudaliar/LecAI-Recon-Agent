"""
gtfs times are dumb in a specific, well known way: they're "seconds since
midnight of the service date, local time", and they're allowed to go past
24:00:00 for trips that start before midnight and run into the next day
(so a night bus timetabled at 25:10:00 just means 01:10 the next
morning). this module just turns that into a real timestamp we can
compare against a live feed's unix epoch timestamp.

everything here is Europe/London local time -> utc epoch, which matters
because of bst - "19:41:00" means a different utc instant in august than
it does in january.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def gtfs_time_to_epoch(service_date, gtfs_time):
    """service_date like '20260815', gtfs_time like '19:41:00' (can be
    >24:00:00). returns unix epoch seconds (int)."""
    hours, minutes, seconds = (int(x) for x in gtfs_time.split(":"))
    day_offset, hours = divmod(hours, 24)

    base_date = datetime.strptime(service_date, "%Y%m%d")
    midnight_local = base_date.replace(tzinfo=LONDON)

    local_dt = midnight_local + timedelta(
        days=day_offset, hours=hours, minutes=minutes, seconds=seconds
    )
    return int(local_dt.timestamp())
