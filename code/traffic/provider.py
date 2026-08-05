from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class TrafficObservation:
    multiplier: float
    source: str
    profile: str


class TimeTrafficProvider:
    """Return a traffic multiplier for a city, day, and departure time."""

    def __init__(self, profiles: pd.DataFrame, profile_name: str):
        self.profiles = profiles.copy()
        self.profile_name = profile_name.strip()

    @classmethod
    def from_csv(cls, path: Path, profile_name: str) -> "TimeTrafficProvider":
        if not path.exists():
            raise FileNotFoundError(
                f"Time-dependent traffic file was not found: {path.resolve()}"
            )

        profiles = pd.read_csv(path)
        required_columns = {
            "profile",
            "city",
            "day_of_week",
            "start_time",
            "end_time",
            "multiplier",
            "source",
        }
        missing = required_columns.difference(profiles.columns)
        if missing:
            raise ValueError(
                "Time-dependent traffic CSV is missing columns: "
                f"{sorted(missing)}"
            )

        profiles = profiles.copy()
        for column in ("profile", "city", "day_of_week", "source"):
            profiles[column] = profiles[column].astype(str).str.strip()
        profiles["multiplier"] = pd.to_numeric(
            profiles["multiplier"], errors="raise"
        )
        if (profiles["multiplier"] <= 0).any():
            raise ValueError("Every time-dependent traffic multiplier must be positive.")

        profiles["_start"] = profiles["start_time"].map(_parse_clock_time)
        profiles["_end"] = profiles["end_time"].map(_parse_clock_time)

        normalized = profile_name.strip().lower()
        available = profiles["profile"].str.lower()
        if not available.eq(normalized).any():
            names = sorted(profiles["profile"].unique())
            raise ValueError(
                f"Time traffic profile {profile_name!r} was not found. "
                f"Available profiles: {names}"
            )

        return cls(profiles, profile_name)

    def get_multiplier(
        self,
        *,
        city: str,
        departure_datetime: datetime,
    ) -> TrafficObservation:
        city_normalized = city.strip().lower()
        profile_normalized = self.profile_name.lower()
        weekday_name = departure_datetime.strftime("%A").lower()
        day_group = "weekend" if departure_datetime.weekday() >= 5 else "weekday"
        current_time = departure_datetime.time().replace(tzinfo=None)

        rows = self.profiles[
            self.profiles["profile"].str.lower().eq(profile_normalized)
            & self.profiles["city"].str.lower().isin({city_normalized, "all"})
        ].copy()

        if rows.empty:
            return TrafficObservation(1.0, "default_baseline", self.profile_name)

        rows["_city_priority"] = rows["city"].str.lower().eq(city_normalized).astype(int)
        rows["_day_priority"] = rows["day_of_week"].str.lower().map(
            lambda value: 3 if value == weekday_name else 2 if value == day_group else 1 if value == "all" else 0
        )
        rows = rows[rows["_day_priority"] > 0]
        rows = rows[rows.apply(
            lambda row: _time_in_interval(current_time, row["_start"], row["_end"]),
            axis=1,
        )]

        if rows.empty:
            return TrafficObservation(1.0, "default_baseline", self.profile_name)

        selected = rows.sort_values(
            ["_city_priority", "_day_priority"], ascending=False
        ).iloc[0]
        return TrafficObservation(
            multiplier=float(selected["multiplier"]),
            source=str(selected["source"]),
            profile=str(selected["profile"]),
        )


def _parse_clock_time(value: object) -> time:
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"Invalid HH:MM time in traffic profile: {text!r}") from exc


def _time_in_interval(current: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end
