import numpy as np
import pandas as pd
import geopandas as gpd

from code.common.constants import MICROHUB_FACILITY_CODES, PUDO_FACILITY_CODES
from code.common.data_utils import validate_required_columns
from shapely.geometry import Point

from code.routing.osrm_client import osrm_distance_duration_table
from code.simulation.operational_points import get_facility_candidates

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
    
    print(
        microhubs[
            ["Location", "Latitude", "Longitude"]
        ]
        .drop_duplicates(
            subset=["Latitude", "Longitude"]
        )
    )

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
    facility_capacity: float,
    facility_name_column: str = "Location",
) -> pd.DataFrame:
    """
    Assign each customer to the nearest facility using OSRM road distances.

    Facilities are considered in order of increasing road distance. If the
    nearest facility has reached its capacity, the next closest facility is
    considered.

    Facility capacity is measured as the cumulative assigned demand.
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

    coords = (
        list(zip(customers["Longitude"], customers["Latitude"]))
        + list(zip(facilities["Longitude"], facilities["Latitude"]))
    )

    distance_matrix, _ = osrm_distance_duration_table(
        coords,
        host=osrm_host,
        profile=osrm_profile,
    )

    customer_count = len(customers)

    facility_distances = distance_matrix[
        :customer_count,
        customer_count:,
    ]

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

    fallback_names = pd.Series(
        [
            f"facility_{facility_index}"
            for facility_index in facilities.index
        ],
        index=facilities.index,
        dtype="string",
    )

    facility_names = facility_names.mask(
        missing_names,
        fallback_names,
    )

    # ------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------

    facility_load = np.zeros(
        len(facilities),
        dtype=float,
    )

    assigned_facility = np.empty(
        customer_count,
        dtype=object,
    )

    assigned_latitude = np.empty(
        customer_count,
        dtype=float,
    )

    assigned_longitude = np.empty(
        customer_count,
        dtype=float,
    )

    for customer_idx in range(customer_count):

        demand = float(
            customers.iloc[customer_idx]["Demand"]
        )

        assigned = False

        for facility_idx in ordered_facilities[customer_idx]:

            if (
                facility_load[facility_idx] + demand
                <= facility_capacity
            ):

                facility = facilities.iloc[facility_idx]

                assigned_facility[customer_idx] = (
                    facility_names.iloc[facility_idx]
                )

                assigned_latitude[customer_idx] = (
                    facility["Latitude"]
                )

                assigned_longitude[customer_idx] = (
                    facility["Longitude"]
                )

                facility_load[facility_idx] += demand

                assigned = True
                break

        if not assigned:

            raise ValueError(
                f"No facility with enough remaining capacity "
                f"for customer {customer_idx}."
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