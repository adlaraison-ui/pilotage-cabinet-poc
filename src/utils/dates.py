from __future__ import annotations
from datetime import date, timedelta

def week_bounds(d: date):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end
