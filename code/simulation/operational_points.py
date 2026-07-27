"""Selection of operational origins for neighborhood simulations.

The simulator currently uses a virtual point located at the arithmetic mean of
all delivery coordinates. Keeping that decision behind this module allows a
real PUDO, microhub, or another selection strategy to be introduced later
without changing the routing algorithms.
"""

from dataclasses import dataclass

import pandas as pd
from collections.abc import Collection
import numpy as np

from code.common.constants import (
    EARTH_RADIUS_METERS,
    ORIGIN_FACILITY_CODES,
)



@dataclass(frozen=True)
class OperationalPoint:
    """Point used as the local origin/depot of a neighborhood simulation."""

    name: str
    latitude: float
    longitude: float
    point_type: str
    strategy: str
    is_virtual: bool

def select_operational_point(
    strategy: str,
    neighborhood_records: pd.DataFrame,
    classified_records: pd.DataFrame | None = None,
    target_latitude: float | None = None,
    target_longitude: float | None = None,
) -> OperationalPoint:
    """Select the local operational point for a neighborhood.

    Parameters
    ----------
    strategy:
        Selection policy. Supported strategies are ``centroid`` and
        ``nearest_origin_facility``.
    neighborhood_records:
        Delivery points assigned to the neighborhood. ``Latitude`` and
        ``Longitude`` columns are required for the centroid strategy.
    classified_records:
        City-wide classified facilities. Required by the
        ``nearest_origin_facility`` strategy.
    target_latitude:
        Latitude used to locate the nearest origin facility.
    target_longitude:
        Longitude used to locate the nearest origin facility.

    Returns
    -------
    OperationalPoint
        The selected virtual or physical operational point.
    """

    if strategy == "nearest_origin_facility":
        if classified_records is None:
            raise ValueError(
                "classified_records is required for "
                "'nearest_origin_facility'."
            )

        if target_latitude is None or target_longitude is None:
            raise ValueError(
                "target coordinates are required for "
                "'nearest_origin_facility'."
            )

        return select_nearest_origin_facility(
            records=classified_records,
            target_latitude=target_latitude,
            target_longitude=target_longitude,
        )

    if strategy == "centroid":
        required_columns = {"Latitude", "Longitude"}
        missing_columns = required_columns.difference(
            neighborhood_records.columns
        )

        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(
                "Cannot calculate the operational point. "
                f"Missing columns: {missing}."
            )

        valid_coordinates = neighborhood_records[
            ["Latitude", "Longitude"]
        ].dropna()

        if valid_coordinates.empty:
            raise ValueError(
                "Cannot calculate the operational point because "
                "the neighborhood has no valid coordinates."
            )

        return OperationalPoint(
            name="Virtual neighborhood centroid",
            latitude=float(valid_coordinates["Latitude"].mean()),
            longitude=float(valid_coordinates["Longitude"].mean()),
            point_type="centroid",
            strategy=strategy,
            is_virtual=True,
        )

    raise ValueError(
        f"Unsupported operational-point strategy: '{strategy}'. "
        "Supported strategies: centroid, nearest_origin_facility."
    )

def get_facility_candidates(
    records: pd.DataFrame,
    allowed_service_codes: Collection[int],
) -> pd.DataFrame:
    """
    Return classified facilities matching the requested service codes.

    Records without valid coordinates are excluded.
    """
    required_columns = {
        "Record_Service_Type_Code",
        "Latitude",
        "Longitude",
    }

    missing_columns = required_columns - set(records.columns)

    if missing_columns:
        raise ValueError(
            "Facility data is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    candidates = records[
        records["Record_Service_Type_Code"].isin(
            allowed_service_codes
        )
    ].copy()

    candidates["Latitude"] = pd.to_numeric(
        candidates["Latitude"],
        errors="coerce",
    )

    candidates["Longitude"] = pd.to_numeric(
        candidates["Longitude"],
        errors="coerce",
    )

    return candidates.dropna(
        subset=["Latitude", "Longitude"]
    )
    
def select_nearest_origin_facility(
    records: pd.DataFrame,
    target_latitude: float,
    target_longitude: float,
) -> OperationalPoint:
    """
    Select the origin facility with the shortest radial distance
    to the target coordinates.

    Origin facilities include distribution centers and warehouses.
    """
    candidates = get_facility_candidates(
        records=records,
        allowed_service_codes=ORIGIN_FACILITY_CODES,
    )

    if candidates.empty:
        raise ValueError(
            "No valid origin facilities were found."
        )

    target_latitude_rad = np.radians(target_latitude)
    target_longitude_rad = np.radians(target_longitude)

    candidate_latitudes_rad = np.radians(
        candidates["Latitude"].to_numpy()
    )

    candidate_longitudes_rad = np.radians(
        candidates["Longitude"].to_numpy()
    )

    latitude_difference = (
        candidate_latitudes_rad - target_latitude_rad
    )

    longitude_difference = (
        candidate_longitudes_rad - target_longitude_rad
    )

    haversine_value = (
        np.sin(latitude_difference / 2) ** 2
        + np.cos(target_latitude_rad)
        * np.cos(candidate_latitudes_rad)
        * np.sin(longitude_difference / 2) ** 2
    )

    angular_distance = 2 * np.arcsin(
        np.sqrt(haversine_value)
    )

    distances_m = (
        EARTH_RADIUS_METERS * angular_distance
    )

    nearest_position = int(np.argmin(distances_m))
    nearest_record = candidates.iloc[nearest_position]

    facility_name = str(
        nearest_record.get(
            "Location",
            nearest_record.get(
                "Record_Service_Type_Name",
                "Origin facility",
            ),
        )
    ).strip()

    return OperationalPoint(
        name=facility_name or "Origin facility",
        latitude=float(nearest_record["Latitude"]),
        longitude=float(nearest_record["Longitude"]),
        point_type="origin_facility",
        strategy="nearest_origin_facility",
        is_virtual=False,
    )