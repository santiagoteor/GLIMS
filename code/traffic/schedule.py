from __future__ import annotations

from datetime import date, datetime, time, timedelta


def build_shift(
    *,
    simulation_date: date,
    start_time: time,
    duration_min: float,
) -> tuple[datetime, datetime]:
    if duration_min <= 0:
        raise ValueError("Shift duration must be greater than zero.")
    shift_start = datetime.combine(simulation_date, start_time)
    shift_end = shift_start + timedelta(minutes=float(duration_min))
    return shift_start, shift_end
