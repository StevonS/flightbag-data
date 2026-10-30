"""Aeronautical publication cycles.

AIRAC data (airspace, airports) changes every 28 days. FAA VFR charts are
reissued every 56 days, on every other AIRAC date. Both cycles share the
anchor below, which is a published FAA VFR chart edition date.
"""

from __future__ import annotations

import datetime as dt

ANCHOR = dt.date(2026, 10, 29)
AIRAC_DAYS = 28
CHART_DAYS = 56


def _current(today: dt.date, period_days: int) -> dt.date:
    """Latest cycle start on or before ``today``."""
    elapsed = (today - ANCHOR).days
    return ANCHOR + dt.timedelta(days=(elapsed // period_days) * period_days)


def current_airac(today: dt.date) -> dt.date:
    return _current(today, AIRAC_DAYS)


def current_chart_edition(today: dt.date) -> dt.date:
    return _current(today, CHART_DAYS)


def next_airac(today: dt.date) -> dt.date:
    return current_airac(today) + dt.timedelta(days=AIRAC_DAYS)


def next_chart_edition(today: dt.date) -> dt.date:
    return current_chart_edition(today) + dt.timedelta(days=CHART_DAYS)


def faa_date(day: dt.date) -> str:
    """Date as used in FAA download URLs: ``MM-DD-YYYY``."""
    return day.strftime("%m-%d-%Y")
