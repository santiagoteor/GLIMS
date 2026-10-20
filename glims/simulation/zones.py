import numpy as np
import pandas as pd
import geopandas as gpd

from glims.common.constants import MICROHUB_FACILITY_CODES, PUDO_FACILITY_CODES
from glims.common.data_utils import validate_required_columns
from shapely.geometry import Point
from tqdm.auto import tqdm

from glims.routing.osrm_client import osrm_distance_duration_table_rectangular

from glims.simulation.operational_points import (
    build_facility_name,
    get_facility_candidates,
)

def filter_points_by_neighborhood(points: pd.DataFrame, neighborhood) -> pd.DataFrame:
    """Filter points that fall inside the zone polygon."""
    validate_required_columns(
        points, {"Latitude", "Longitude"}, "geographical points",
    )
    polygon = neighborhood["geometry"]
    inside = points.apply(
        lambda row: polygon.contains(Point(row["Longitude"], row["Latitude"])),
        axis=1,
    )
    return points[inside].copy()

def load_facility_candidates(
    classified_locations: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return valid microhub and PUDO candidates using the project's
    authoritative service-code definitions.
    """

    microhubs = get_facility_candidates(
        records=classified_locations,
        allowed_service_codes=MICROHUB_FACILITY_CODES,
    ).reset_index(drop=True)

    pudos = get_facility_candidates(
        records=classified_locations,
        allowed_service_codes=PUDO_FACILITY_CODES,
    ).reset_index(drop=True)

    if microhubs.empty:
        raise ValueError(
            "No valid microhub facilities were found in records_classified.csv."
        )

    if pudos.empty:
        raise ValueError(
            "No valid PUDO facilities were found in records_classified.csv."
        )

    return microhubs, pudos

# TODO: prefilter candidate facilities before building the OSRM assignment matrix.

def assign_customers_to_nearest_facility(
    customers: pd.DataFrame,
    facilities: pd.DataFrame,
    *,
    osrm_host: str,
    osrm_profile: str,
    facility_capacity: float | None,
    facility_name_column: str = "Location",
    show_progress: bool = False,
    progress_label: str = "Assigning customers to facilities",
    max_haversine_distance_m: float | None = None,
    allow_unassigned: bool = False,
    priority_nearest_first: bool = False,
    priority_audit_rows: list[dict] | None = None,
) -> pd.DataFrame:
    """
    Assign each customer to the nearest facility using OSRM road distances.

    Only the customer -> facility rectangular OSRM matrix is calculated.
    Customer-to-customer and facility-to-facility distances are not needed
    for this assignment.

    Facilities are considered in order of increasing road distance. If
    facility capacity is limited and the nearest facility has reached its
    capacity, the next closest facility is considered.

    Passing ``facility_capacity=None`` disables the facility-capacity
    constraint and represents theoretically unlimited capacity.
    """

    validate_required_columns(
        customers,
        {"Latitude", "Longitude", "Demand"},
        "customers",
    )

    validate_required_columns(
        facilities,
        {"Latitude", "Longitude", facility_name_column},
        "facilities",
    )

    if facilities.empty:
        raise ValueError(
            "No facilities were provided for customer assignment."
        )

    # ------------------------------------------------------------
    # Compute OSRM customer -> facility distance matrix
    # ------------------------------------------------------------

    print(
        f"Customers: {len(customers)}, "
        f"Facilities: {len(facilities)}"
    )

    customer_coords = list(
        zip(
            customers["Longitude"],
            customers["Latitude"],
        )
    )

    facility_coords = list(
        zip(
            facilities["Longitude"],
            facilities["Latitude"],
        )
    )

    facility_distances, _ = (
        osrm_distance_duration_table_rectangular(
            customer_coords,
            facility_coords,
            host=osrm_host,
            profile=osrm_profile,
        )
    )

    if max_haversine_distance_m is not None:
        from glims.simulation.m3_eligibility import _distances
        eligible_distances = _distances(customers, facilities)
        facility_distances = np.where(eligible_distances <= max_haversine_distance_m,
                                      facility_distances, np.inf)

    customer_count = len(customers)

    ordered_facilities = np.argsort(
        facility_distances,
        axis=1,
    )

    # ------------------------------------------------------------
    # Facility names
    # ------------------------------------------------------------

    facility_names = (
        facilities[facility_name_column]
        .astype("string")
        .str.strip()
    )

    missing_names = (
        facility_names.isna()
        | facility_names.eq("")
    )

    descriptive_fallback_names = pd.Series(
        [
            build_facility_name(
                row,
                fallback_name=f"facility_{facility_index}",
            )
            for facility_index, row in facilities.iterrows()
        ],
        index=facilities.index,
        dtype="string",
    )

    facility_names = facility_names.mask(
        missing_names,
        descriptive_fallback_names,
    )

    # Keep generated names unique without changing existing Location values.
    # This prevents two facilities with identical descriptive metadata from
    # being merged downstream when facility names are used as dictionary keys.
    duplicate_generated_names = (
        missing_names
        & facility_names.duplicated(keep=False)
    )
    if duplicate_generated_names.any():
        facility_names.loc[duplicate_generated_names] = [
            f"{facility_names.loc[facility_index]} "
            f"[facility_{facility_index}]"
            for facility_index in facilities.index[
                duplicate_generated_names
            ]
        ]

    # ------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------

    facility_load = np.zeros(
        len(facilities),
        dtype=float,
    )

    capacity_limited = facility_capacity is not None

    if capacity_limited:
        facility_capacity = float(facility_capacity)

        if facility_capacity <= 0:
            raise ValueError(
                "facility_capacity must be greater than zero."
            )

    assigned_facility = np.full(customer_count, None, dtype=object)

    assigned_latitude = np.full(customer_count, np.nan, dtype=float)

    assigned_longitude = np.full(customer_count, np.nan, dtype=float)

    # For capacity-limited M3, process customers by nearest eligible
    # Haversine distance. Stable sorting preserves original registration order.
    processing_order = np.arange(customer_count)
    if priority_nearest_first and capacity_limited:
        from glims.simulation.m3_eligibility import _distances
        geo_distances = _distances(customers, facilities)
        if max_haversine_distance_m is not None:
            geo_distances = np.where(
                geo_distances <= max_haversine_distance_m, geo_distances, np.inf
            )
        processing_order = np.argsort(
            np.min(geo_distances, axis=1), kind="stable"
        )

    customer_indices = tqdm(
        processing_order,
        desc=progress_label,
        unit="customer",
        disable=not show_progress,
        mininterval=0.5,
        leave=False,
    )

    for priority_rank, customer_idx in enumerate(customer_indices, start=1):

        eligible_facilities = [
            int(i) for i in ordered_facilities[customer_idx]
            if np.isfinite(facility_distances[customer_idx, i])
        ]
        nearest_idx = eligible_facilities[0] if eligible_facilities else None
        nearest_geo_distance = None
        if priority_audit_rows is not None:
            from glims.simulation.m3_eligibility import _distances
            # Computed once per customer; does not change routing decisions.
            if nearest_idx is not None:
                nearest_geo_distance = float(np.min(
                    _distances(customers.iloc[[customer_idx]], facilities.iloc[eligible_facilities])
                ))
        capacity_before = (
            float(facility_capacity - facility_load[nearest_idx])
            if capacity_limited and nearest_idx is not None else None
        )

        demand = float(
            customers.iloc[customer_idx]["Demand"]
        )

        assigned = False

        for facility_idx in ordered_facilities[customer_idx]:
            if not np.isfinite(facility_distances[customer_idx, facility_idx]):
                continue

            capacity_available = (
                not capacity_limited
                or facility_load[facility_idx] + demand
                <= facility_capacity
            )

            if not capacity_available:
                continue

            facility = facilities.iloc[facility_idx]

            assigned_facility[customer_idx] = (
                facility_names.iloc[facility_idx]
            )

            assigned_latitude[customer_idx] = float(
                facility["Latitude"]
            )

            assigned_longitude[customer_idx] = float(
                facility["Longitude"]
            )

            facility_load[facility_idx] += demand

            assigned = True
            break

        if priority_audit_rows is not None:
            selected = assigned_facility[customer_idx]
            selected_idx = next(
                (int(i) for i in eligible_facilities
                 if str(facility_names.iloc[i]) == str(selected)), None
            ) if assigned else None
            priority_audit_rows.append({
                "customer_row_position": int(customer_idx),
                "customer_id": str(customers.iloc[customer_idx].get("customer_id",
                    customers.iloc[customer_idx].get("Customer_ID",
                    customers.iloc[customer_idx].get("ID", customer_idx)))),
                "priority_rank": int(priority_rank),
                "packages": float(demand),
                "microhub_id": str(facility_names.iloc[selected_idx]) if selected_idx is not None else (str(facility_names.iloc[nearest_idx]) if nearest_idx is not None else ""),
                "nearest_eligible_microhub": str(facility_names.iloc[nearest_idx]) if nearest_idx is not None else "",
                "distance_m": nearest_geo_distance,
                "assigned_microhub": str(selected) if assigned else "",
                "capacity_before": capacity_before,
                "capacity_after": (float(facility_capacity - facility_load[selected_idx]) if capacity_limited and selected_idx is not None else capacity_before),
                "decision": "accepted" if assigned else "rejected",
                "reason": "accepted" if assigned else ("capacity_insufficient" if eligible_facilities else "outside_microhub_coverage"),
            })

        if not assigned and not allow_unassigned:
            raise ValueError(
                "No facility with enough remaining capacity "
                f"for customer {customer_idx}. "
                "Use facility_capacity=None for unlimited capacity."
            )

    # ------------------------------------------------------------
    # Output
    # ------------------------------------------------------------

    assigned = customers.copy()

    assigned["assigned_facility"] = assigned_facility
    assigned["facility_latitude"] = assigned_latitude
    assigned["facility_longitude"] = assigned_longitude
    assigned["assigned_demand"] = assigned["Demand"]

    return assigned

def group_customers_by_facility(
    assigned_customers: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """
    Group customers by their assigned facility.

    Returns
    -------
    dict
        {
            facility_name: dataframe_of_customers
        }
    """

    validate_required_columns(
        assigned_customers,
        {
            "assigned_facility",
            "Latitude",
            "Longitude",
            "Demand",
        },
        "assigned customers",
    )

    return {
        facility: group.reset_index(drop=True)
        for facility, group in assigned_customers.groupby(
            "assigned_facility",
            sort=False,
        )
    }

def build_facility_summary(
    assigned_customers: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build one record per used facility.

    The resulting dataframe contains one row for every facility that
    has at least one assigned customer.
    """

    validate_required_columns(
        assigned_customers,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Demand",
        },
        "assigned customers",
    )

    summary = (
        assigned_customers
        .groupby("assigned_facility", as_index=False)
        .agg(
            Latitude=("facility_latitude", "first"),
            Longitude=("facility_longitude", "first"),
            Demand=("Demand", "sum"),
            Customers=("Demand", "count"),
        )
    )

    summary = summary.rename(
        columns={
            "assigned_facility": "Location",
        }
    )

    return summary


def select_zones(boundaries, requested):
    """
    Select simulation zones by name, level-agnostic.
    """
 
    available_names = sorted(boundaries["zona"].tolist())
    selected = []
 
    for token in requested:
        if ":" in token:
            raw_name, raw_tipo = token.rsplit(":", 1)
            name = raw_name.strip().lower()
            tipo = raw_tipo.strip().lower()
            match = boundaries[
                (boundaries["zona"].str.lower() == name)
                & (boundaries["tipo"].str.lower() == tipo)
            ]
            label = token
        else:
            name = token.strip().lower()
            match = boundaries[boundaries["zona"].str.lower() == name]
            label = token
 
        if match.empty:
            raise ValueError(
                f"Zone not found: '{label}'. "
                f"Availables: {available_names}"
            )
 
        if len(match) > 1:
            tipos = ", ".join(sorted(match["tipo"].str.lower().unique()))
            raise ValueError(
                f"'{label}' is ambiguous (exists as: {tipos}). "
                f"Specify it as '{token}:district' o '{token}:neighborhood'."
            )
 
        selected.append(match)
 
    return gpd.GeoDataFrame(
        pd.concat(selected, ignore_index=True),
        crs=boundaries.crs,
    )
    
def warn_zone_overlaps(zones):
    """Warn when one selected zone contains another (double-counted demand)."""
 
    for _, outer in zones.iterrows():
        for _, inner in zones.iterrows():
            if outer["zona"] == inner["zona"] and outer["tipo"] == inner["tipo"]:
                continue
            if outer.geometry.contains(inner.geometry.centroid):
                print(
                    f"'{inner['zona']}' ({inner['tipo']}) is inside of "
                    f"'{outer['zona']}' ({outer['tipo']}): the demand "
                    f"is counted two times."
                )