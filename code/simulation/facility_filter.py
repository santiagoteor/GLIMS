from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from code.common.data_utils import validate_required_columns


@dataclass(frozen=True)
class FacilityFilterSettings:
    """Geographic candidate-filter settings for facility assignment."""

    enabled: bool = False
    initial_buffer_m: float = 500.0
    buffer_increment_m: float = 500.0
    maximum_buffer_m: float = 5000.0
    minimum_candidates: int = 5

    def validate(self) -> None:
        if self.initial_buffer_m < 0:
            raise ValueError("initial_buffer_m cannot be negative.")
        if self.buffer_increment_m <= 0:
            raise ValueError("buffer_increment_m must be greater than zero.")
        if self.maximum_buffer_m < self.initial_buffer_m:
            raise ValueError(
                "maximum_buffer_m must be greater than or equal to "
                "initial_buffer_m."
            )
        if self.minimum_candidates <= 0:
            raise ValueError("minimum_candidates must be greater than zero.")


def filter_facilities_for_zone(
    facilities: pd.DataFrame,
    neighborhood,
    *,
    settings: FacilityFilterSettings,
    zone_crs,
    facility_label: str,
) -> pd.DataFrame:
    """
    Select real facility candidates near one active simulation zone.

    The authoritative facility dataframe is never changed. A metric buffer is
    built around the zone polygon and expanded only until the configured
    minimum number of candidates is available or the maximum buffer is reached.
    No demand or facility-capacity assumption is used by this filter.
    """
    validate_required_columns(
        facilities,
        {"Latitude", "Longitude"},
        f"{facility_label} candidates",
    )
    settings.validate()

    if facilities.empty:
        raise ValueError(f"No {facility_label} candidates were provided.")

    if not settings.enabled:
        return facilities.copy().reset_index(drop=True)

    source_crs = zone_crs or "EPSG:4326"
    zone_gdf = gpd.GeoDataFrame(
        [{"geometry": neighborhood["geometry"]}],
        geometry="geometry",
        crs=source_crs,
    )
    facilities_gdf = gpd.GeoDataFrame(
        facilities.copy(),
        geometry=[
            Point(float(longitude), float(latitude))
            for longitude, latitude in zip(
                facilities["Longitude"],
                facilities["Latitude"],
            )
        ],
        crs="EPSG:4326",
    )

    metric_crs = zone_gdf.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError(
            f"Could not estimate a metric CRS for {facility_label} filtering."
        )

    zone_metric = zone_gdf.to_crs(metric_crs)
    facilities_metric = facilities_gdf.to_crs(metric_crs)
    zone_geometry = zone_metric.geometry.iloc[0]

    buffer_m = float(settings.initial_buffer_m)
    selected_mask = facilities_metric.geometry.intersects(
        zone_geometry.buffer(buffer_m)
    )

    while (
        int(selected_mask.sum()) < settings.minimum_candidates
        and buffer_m < settings.maximum_buffer_m
    ):
        buffer_m = min(
            buffer_m + settings.buffer_increment_m,
            settings.maximum_buffer_m,
        )
        selected_mask = facilities_metric.geometry.intersects(
            zone_geometry.buffer(buffer_m)
        )

    selected = facilities.loc[selected_mask.to_numpy()].copy()

    if selected.empty:
        raise ValueError(
            f"The geographic filter found no {facility_label} candidates "
            f"within {buffer_m:.0f} m of zone "
            f"{neighborhood.get('zona', '<unknown>')!r}."
        )

    if len(selected) < settings.minimum_candidates:
        print(
            f"Warning: only {len(selected)} {facility_label} candidates were "
            f"found within the maximum buffer of {buffer_m:.0f} m "
            f"(requested minimum: {settings.minimum_candidates})."
        )

    print(
        f"{facility_label} prefilter: "
        f"{len(facilities)} city candidates -> {len(selected)} candidates "
        f"for zone {neighborhood.get('zona', '<unknown>')!r} "
        f"(buffer={buffer_m:.0f} m)"
    )

    return selected.reset_index(drop=True)
