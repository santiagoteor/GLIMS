from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TrafficProfile:
    """Traffic adjustment applied to baseline OSRM driving durations."""

    name: str
    duration_multiplier: float
    source: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Traffic profile name cannot be empty.")
        if self.duration_multiplier <= 0:
            raise ValueError(
                "Traffic duration multiplier must be greater than zero."
            )
        if not self.source.strip():
            raise ValueError("Traffic profile source cannot be empty.")


def apply_traffic_profile(
    duration_matrix: np.ndarray,
    traffic_profile: TrafficProfile,
) -> np.ndarray:
    """Return a copy of an OSRM duration matrix adjusted for traffic."""

    adjusted_matrix = np.asarray(duration_matrix, dtype=float).copy()
    adjusted_matrix *= float(traffic_profile.duration_multiplier)
    np.fill_diagonal(adjusted_matrix, 0.0)
    return adjusted_matrix


def load_traffic_profile(
    *,
    csv_path: Path,
    profile_name: str,
    city: str,
    multiplier_override: float | None = None,
) -> TrafficProfile:
    """
    Load a traffic profile from CSV.

    A city-specific row is preferred over a row whose city value is ``all``.
    The optional CLI override keeps the profile metadata while replacing its
    multiplier for sensitivity analysis.
    """

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Traffic profile file was not found: {csv_path.resolve()}"
        )

    profiles = pd.read_csv(csv_path)

    required_columns = {
        "profile",
        "city",
        "duration_multiplier",
        "source",
    }
    missing_columns = required_columns.difference(profiles.columns)
    if missing_columns:
        raise ValueError(
            "Traffic profile CSV is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    normalized_profile = profile_name.strip().lower()
    normalized_city = city.strip().lower()

    profile_values = profiles["profile"].astype(str).str.strip().str.lower()
    city_values = profiles["city"].astype(str).str.strip().str.lower()

    candidates = profiles[
        profile_values.eq(normalized_profile)
        & city_values.isin({normalized_city, "all"})
    ].copy()

    if candidates.empty:
        raise ValueError(
            f"Traffic profile {profile_name!r} is not defined for "
            f"city {city!r} or for city='all'."
        )

    candidates["_city_priority"] = (
        candidates["city"].astype(str).str.strip().str.lower().eq(normalized_city)
    )

    selected = candidates.sort_values(
        "_city_priority",
        ascending=False,
    ).iloc[0]

    multiplier = float(selected["duration_multiplier"])
    source = str(selected["source"]).strip()

    if multiplier_override is not None:
        if multiplier_override <= 0:
            raise ValueError(
                "--traffic-multiplier must be greater than zero."
            )
        multiplier = float(multiplier_override)
        source = f"cli_override_of:{source}"

    return TrafficProfile(
        name=str(selected["profile"]).strip(),
        duration_multiplier=multiplier,
        source=source,
    )
