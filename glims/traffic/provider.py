from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
import re
import unicodedata

import pandas as pd


DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True)
class TrafficObservation:
    multiplier: float
    source: str
    profile: str
    zone: str
    day_of_week: str


class TimeTrafficProvider:
    """Return a traffic multiplier for one city, zone, day, and departure time."""

    def __init__(
        self,
        profiles: pd.DataFrame,
        *,
        city: str,
        profile_name: str,
        source_path: Path,
    ):
        self.profiles = profiles.copy()
        self.city = city.strip().lower()
        self.profile_name = profile_name.strip()
        self.source_path = source_path

    @classmethod
    def from_city_csv(
        cls,
        *,
        traffic_folder: Path,
        city: str,
        profile_name: str,
    ) -> "TimeTrafficProvider":
        city_normalized = city.strip().lower()
        path = traffic_folder / f"{city_normalized}.csv"

        if not path.exists():
            raise FileNotFoundError(
                f"Time-dependent traffic file was not found for "
                f"{city_normalized!r}: {path.resolve()}"
            )

        profiles = pd.read_csv(path)
        required_columns = {
            "profile",
            "zone",
            "day_of_week",
            "start_time",
            "end_time",
            "multiplier",
            "source",
        }
        missing = required_columns.difference(profiles.columns)
        if missing:
            raise ValueError(
                f"{path.name} is missing time-dependent traffic columns: "
                f"{sorted(missing)}"
            )

        profiles = profiles.copy()
        for column in (
            "profile",
            "zone",
            "day_of_week",
            "source",
        ):
            profiles[column] = profiles[column].astype(str).str.strip()

        profiles["_profile_key"] = profiles["profile"].str.casefold()
        profiles["_zone_key"] = profiles["zone"].map(_normalize_zone)
        profiles["_day_key"] = profiles["day_of_week"].str.casefold()

        allowed_days = set(DAY_NAMES) | {"weekday", "weekend", "all"}
        invalid_days = sorted(
            set(profiles["_day_key"]).difference(allowed_days)
        )
        if invalid_days:
            raise ValueError(
                f"{path.name} contains unsupported day_of_week values: "
                f"{invalid_days}. Expected Monday-Sunday, weekday, weekend, or all."
            )

        profiles["multiplier"] = pd.to_numeric(
            profiles["multiplier"],
            errors="raise",
        )
        if (profiles["multiplier"] <= 0).any():
            raise ValueError(
                "Every time-dependent traffic multiplier must be positive."
            )

        profiles["_start"] = profiles["start_time"].map(_parse_clock_time)
        profiles["_end"] = profiles["end_time"].map(_parse_clock_time)

        profile_key = profile_name.strip().casefold()
        if not profiles["_profile_key"].eq(profile_key).any():
            names = sorted(profiles["profile"].unique())
            raise ValueError(
                f"Time traffic profile {profile_name!r} was not found in "
                f"{path.name}. Available profiles: {names}"
            )

        return cls(
            profiles,
            city=city_normalized,
            profile_name=profile_name,
            source_path=path,
        )

    def get_multiplier(
        self,
        *,
        zone: str | None,
        departure_datetime: datetime,
    ) -> TrafficObservation:
        profile_key = self.profile_name.casefold()
        zone_key = _normalize_zone(zone or "all")
        weekday_name = DAY_NAMES[departure_datetime.weekday()]
        day_group = (
            "weekend"
            if departure_datetime.weekday() >= 5
            else "weekday"
        )
        current_time = departure_datetime.time().replace(tzinfo=None)

        rows = self.profiles[
            self.profiles["_profile_key"].eq(profile_key)
            & self.profiles["_zone_key"].isin({zone_key, "all"})
        ].copy()

        if rows.empty:
            return self._baseline_observation(
                zone=zone or "all",
                day_of_week=weekday_name,
            )

        rows["_zone_priority"] = (
            rows["_zone_key"].eq(zone_key).astype(int)
        )
        rows["_day_priority"] = rows["_day_key"].map(
            lambda value: (
                3
                if value == weekday_name
                else 2
                if value == day_group
                else 1
                if value == "all"
                else 0
            )
        )
        rows = rows[rows["_day_priority"] > 0]
        rows = rows[
            rows.apply(
                lambda row: _time_in_interval(
                    current_time,
                    row["_start"],
                    row["_end"],
                ),
                axis=1,
            )
        ]

        if rows.empty:
            return self._baseline_observation(
                zone=zone or "all",
                day_of_week=weekday_name,
            )

        selected = rows.sort_values(
            ["_zone_priority", "_day_priority"],
            ascending=False,
        ).iloc[0]

        return TrafficObservation(
            multiplier=float(selected["multiplier"]),
            source=str(selected["source"]),
            profile=str(selected["profile"]),
            zone=str(selected["zone"]),
            day_of_week=str(selected["day_of_week"]),
        )

    def _baseline_observation(
        self,
        *,
        zone: str,
        day_of_week: str,
    ) -> TrafficObservation:
        return TrafficObservation(
            multiplier=1.0,
            source="default_baseline",
            profile=self.profile_name,
            zone=zone,
            day_of_week=day_of_week,
        )


def _normalize_zone(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "all"


def _parse_clock_time(value: object) -> time:
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(
            f"Invalid HH:MM time in traffic profile: {text!r}"
        ) from exc


def _time_in_interval(
    current: time,
    start: time,
    end: time,
) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end
