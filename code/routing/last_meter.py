from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from code.routing.osrm_client import get_osrm_host, get_osrm_snap_records


@dataclass(frozen=True)
class LastMeterAccessMetrics:
    """Per-customer last-meter access values aligned with a client DataFrame."""

    snap_distance_m: np.ndarray
    access_distance_m: np.ndarray
    access_time_min: np.ndarray

    @property
    def total_access_distance_km(self) -> float:
        return float(np.nansum(self.access_distance_m) / 1000.0)

    @property
    def total_access_time_min(self) -> float:
        return float(np.nansum(self.access_time_min))


def build_last_meter_access_metrics(
    *,
    city: str,
    transport_mode: str,
    clients: pd.DataFrame,
    enabled: bool,
    walking_speed_m_s: float,
    round_trip: bool,
) -> LastMeterAccessMetrics:
    """Build last-meter access metrics from OSRM snapping metadata.

    The OSRM table request must already have been executed for ``clients`` so
    the snap registry contains one waypoint record for every routable point.
    This function performs no additional OSRM HTTP requests.
    """

    client_count = len(clients)
    zeros = np.zeros(client_count, dtype=float)
    if not enabled or client_count == 0:
        return LastMeterAccessMetrics(
            snap_distance_m=zeros.copy(),
            access_distance_m=zeros.copy(),
            access_time_min=zeros.copy(),
        )

    speed = float(walking_speed_m_s)
    if speed <= 0:
        raise ValueError("walking_speed_m_s must be greater than zero.")

    coords = list(
        zip(
            clients["Longitude"].astype(float),
            clients["Latitude"].astype(float),
        )
    )
    records = get_osrm_snap_records(
        coords,
        host=get_osrm_host(city, transport_mode),
        profile=transport_mode,
        include_missing=True,
    )

    missing = [index for index, record in enumerate(records) if record is None]
    if missing:
        preview = ", ".join(str(index) for index in missing[:10])
        raise RuntimeError(
            "Last-meter access is enabled, but OSRM snapping metadata is "
            f"missing for {len(missing)} routable customer(s) using profile "
            f"{transport_mode!r}. Missing zero-based positions: {preview}. "
            "Rebuild the OSRM matrix cache with the current snap-aware cache "
            "format and rerun the experiment."
        )

    snap_distance_m = np.asarray(
        [float(record["snap_distance_m"]) for record in records],
        dtype=float,
    )
    if np.any(~np.isfinite(snap_distance_m)):
        raise RuntimeError(
            "Last-meter access is enabled, but at least one OSRM snap distance "
            "is not finite."
        )
    if np.any(snap_distance_m < 0):
        raise RuntimeError("OSRM snap distances cannot be negative.")

    factor = 2.0 if round_trip else 1.0
    access_distance_m = snap_distance_m * factor
    access_time_min = access_distance_m / speed / 60.0

    return LastMeterAccessMetrics(
        snap_distance_m=snap_distance_m,
        access_distance_m=access_distance_m,
        access_time_min=access_time_min,
    )


def add_last_meter_time_to_duration_matrix(
    duration_matrix: np.ndarray,
    access_time_min: np.ndarray,
) -> np.ndarray:
    """Add each customer's access time once on every incoming matrix arc.

    Client matrix indices are ``1..n`` and the depot is index ``0``. Adding a
    customer's access time to its destination column makes every feasible route
    incur that time exactly once when the customer is visited. The depot column
    is unchanged, so the return-to-depot arc receives no access penalty.

    This representation affects CWS/ILS temporal feasibility without changing
    the distance objective or the OSRM network-distance matrix.
    """

    adjusted = np.asarray(duration_matrix, dtype=float).copy()
    access = np.asarray(access_time_min, dtype=float)
    if adjusted.shape[0] != adjusted.shape[1]:
        raise ValueError("Last-meter access requires a square duration matrix.")
    if adjusted.shape[0] != len(access) + 1:
        raise ValueError(
            "access_time_min must contain exactly one value per client."
        )
    if len(access):
        adjusted[:, 1:] += access[np.newaxis, :]
    return adjusted
