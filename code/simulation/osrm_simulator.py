# Variant of osmnx_simulator.py that replaces OSMnx/NetworkX shortest-path
# computation with calls to a running OSRM server (http://project-osrm.org).
#
# Prerequisites: an OSRM instance must be reachable at OSRM_HOST, serving the
# /table service for OSRM_PROFILE. Typical local setup with Docker:
#
#   osrm-extract -p /opt/car.lua /data/spain-latest.osm.pbf
#   osrm-contract /data/spain-latest.osrm
#   docker run -t -i -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend \
#       osrm-routed --algorithm mld /data/spain-latest.osrm
#
# Unlike osmnx_simulator.py, there is no in-process graph to download or
# snap points to: OSRM snaps coordinates to the network internally on every
# /table or /route request.

import numpy as np
import pandas as pd
from time import perf_counter
import geopandas as gpd
from shapely.geometry import Point

from code.common.paths import DATA_DIR, RESULTS_DIR
from code.common.cost_utils import load_cost_parameters
from code.routing.cws import clarke_wright_savings
from code.routing.route_plan import OsrmRoutePlan
from code.simulation.exporters import build_route_detail_rows
from code.simulation.models import simulate_m1, simulate_m2, simulate_m3, simulate_m4, simulate_m5
from code.simulation.operational_points import (
    OperationalPoint,
    get_facility_candidates,
    select_operational_point,
)

from code.common.constants import (
    CITIES,
    BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
    DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
    MAX_ROUTE_DURATION_MIN,
    MICROHUB_FACILITY_CODES,
    OSRM_PORTS,
    PUDO_FACILITY_CODES,
    SERVICE_TIME_PER_STOP_MIN,
    TRUCK_LOADING_TIME_PER_ROUTE_MIN,
    WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
)

import argparse

from code.common.routing_utils import (
    calculate_route_durations,
    calculate_route_loads,
    calculate_routes_matrix_cost,
)

from code.routing.config import RoutingAlgorithmConfig
from code.routing.ils import iterated_local_search
from code.simulation.traffic import (
    TrafficProfile,
    apply_traffic_profile,
    load_traffic_profile,
)

from code.routing.osrm_client import (
    check_osrm_server,
    get_osrm_host,
    osrm_distance_duration_table,
    osrm_table,
)

def load_city_data(
    city: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load logistics centers, neighborhood limits, and model parameters."""

    city_folder = DATA_DIR / city

    centers_path = city_folder / "centros_cc.csv"
    boundaries_path = city_folder / "limites_zonas.geojson"
    parameters_path = DATA_DIR / "model_parameters.csv"

    required_paths = {
        "logistics centers": centers_path,
        "neighborhood boundaries": boundaries_path,
        "model parameters": parameters_path,
    }

    missing_paths = [
        f"{description}: {path.resolve()}"
        for description, path in required_paths.items()
        if not path.exists()
    ]

    if missing_paths:
        raise FileNotFoundError(
            "Required simulation files were not found:\n- "
            + "\n- ".join(missing_paths)
        )

    centers = pd.read_csv(centers_path)
    boundaries = gpd.read_file(boundaries_path)
    if boundaries.crs is not None and boundaries.crs.to_epsg() != 4326:
        boundaries = boundaries.to_crs(epsg=4326)   
    parameters = pd.read_csv(parameters_path)

    validate_required_columns(
        centers,
        required_columns={"Location", "Latitude", "Longitude"},
        dataset_name=f"{city} logistics centers",
    )
    validate_required_columns(
        boundaries,
        required_columns={"zona", "tipo", "geometry"},
        dataset_name=f"{city} zones",
    )
    validate_required_columns(
        parameters,
        required_columns={"modelo"},
        dataset_name="model parameters",
    )

    return centers, boundaries, parameters


def load_demand_instance(
    city: str,
    scenario: str,
    instance_size: int,
) -> pd.DataFrame:
    """Load one immutable demand instance and normalize its routing schema."""

    demand_path = (
        RESULTS_DIR
        / city
        / "demand"
        / f"demand_{scenario}_{instance_size}.csv"
    )

    if not demand_path.exists():
        raise FileNotFoundError(
            "Demand instance was not found: "
            f"{demand_path.resolve()}"
        )

    demand = pd.read_csv(demand_path)
    validate_required_columns(
        demand,
        required_columns={"customer_id", "lat", "lon", "demand"},
        dataset_name=f"{city} demand instance",
    )

    demand = demand.rename(
        columns={"lat": "Latitude", "lon": "Longitude", "demand": "Demand"}
    ).copy()
    demand["Latitude"] = pd.to_numeric(demand["Latitude"], errors="coerce")
    demand["Longitude"] = pd.to_numeric(demand["Longitude"], errors="coerce")
    demand["Demand"] = pd.to_numeric(demand["Demand"], errors="coerce")
    demand = demand.dropna(subset=["Latitude", "Longitude", "Demand"])

    if demand.empty:
        raise ValueError(f"Demand instance is empty after validation: {demand_path}")
    if (demand["Demand"] <= 0).any():
        raise ValueError("Every demand record must contain a positive package demand.")

    return demand


def calculate_demand_weighted_centroid(records: pd.DataFrame) -> OperationalPoint:
    """Return the package-demand-weighted center of the delivery instance."""

    validate_required_columns(
        records,
        required_columns={"Latitude", "Longitude", "Demand"},
        dataset_name="demand records",
    )
    total_demand = float(records["Demand"].sum())
    if total_demand <= 0:
        raise ValueError("Total package demand must be greater than zero.")

    return OperationalPoint(
        name="Demand-weighted neighborhood centroid",
        latitude=float((records["Latitude"] * records["Demand"]).sum() / total_demand),
        longitude=float((records["Longitude"] * records["Demand"]).sum() / total_demand),
        point_type="demand_centroid",
        strategy="demand_weighted_centroid",
        is_virtual=True,
    )


## HERE IS USING ALL PUDOS FOR EACH CLIENT RIGHT NOW, MATRIZ TAKES A LONG TIME TO CALCULATE

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

def validate_required_columns(
    dataframe: pd.DataFrame,
    required_columns: set[str],
    dataset_name: str,
) -> None:
    """Raise a clear error when a dataframe does not have its expected schema."""

    missing_columns = required_columns.difference(dataframe.columns)

    if missing_columns:
        raise ValueError(
            f"{dataset_name} is missing required columns: "
            f"{sorted(missing_columns)}"
        )


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

def calculate_customer_collection_travel(
    city: str,
    assigned_pudos: pd.DataFrame,
    neighborhood_name: str,
) -> tuple[float, float, list[dict]]:
    """
    Calculate one customer round trip to the assigned PUDO.

    Customers assigned to the same PUDO are processed together so that
    only one OSRM table request is required per used facility.
    """

    validate_required_columns(
        assigned_pudos,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Latitude",
            "Longitude",
        },
        "customers assigned to PUDOs",
    )

    if assigned_pudos.empty:
        return 0.0, 0.0, []

    router = CapacityAwareOsrmRouter(city)

    total_distance_km = 0.0
    total_duration_min = 0.0
    detail_rows = []

    pudo_groups = group_customers_by_facility(assigned_pudos)

    for pudo_name, customers in pudo_groups.items():
        pudo_latitude = float(customers["facility_latitude"].iloc[0])
        pudo_longitude = float(customers["facility_longitude"].iloc[0])

        customer_locations = customers[
            ["Latitude", "Longitude"]
        ].reset_index(drop=True)

        distance_matrix, duration_matrix = router.get_matrices(
            depot_latitude=pudo_latitude,
            depot_longitude=pudo_longitude,
            clients=customer_locations,
            transport_mode="walking",
        )

        for customer_offset, customer in customers.reset_index(drop=True).iterrows():
            matrix_index = customer_offset + 1
            distance_km = float(
                distance_matrix[0, matrix_index]
                + distance_matrix[matrix_index, 0]
            )
            duration_min = float(
                duration_matrix[0, matrix_index]
                + duration_matrix[matrix_index, 0]
            )
            total_distance_km += distance_km
            total_duration_min += duration_min
            detail_rows.append(
                {
                    "city": city,
                    "neighborhood": neighborhood_name,
                    "model": "M5",
                    "leg": "customer_collection",
                    "route_id": (
                        f"M5_{neighborhood_name}_customer_collection_"
                        f"{len(detail_rows) + 1}"
                    ),
                    "vehicle_type": "customer_walking",
                    "depot": pudo_name,
                    "route_number": len(detail_rows) + 1,
                    "stop_count": 1,
                    "package_load": float(customer["Demand"]),
                    "vehicle_capacity": np.nan,
                    "distance_km": distance_km,
                    "duration_min": duration_min,
                    "start_handling_min": 0.0,
                    "stop_service_min": 0.0,
                    "stop_sequence": str(
                        customer.get("customer_id", customer_offset + 1)
                    ),
                }
            )

    return total_distance_km, total_duration_min, detail_rows

def calculate_microhub_last_mile(
    city: str,
    assigned_microhubs: pd.DataFrame,
    bike_capacity: float,
    neighborhood_name: str,
    routing_config: RoutingAlgorithmConfig,
) -> tuple[float, float, int, float, list[dict]]:
    """
    Simulate bicycle delivery independently from every used microhub.

    Returns
    -------
    total_distance_km
    total_duration_min
    total_routes
    """

    validate_required_columns(
        assigned_microhubs,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Latitude",
            "Longitude",
            "Demand",
        },
        "assigned microhubs",
    )

    if assigned_microhubs.empty:
        return 0.0, 0.0, 0, 0.0, []

    router = CapacityAwareOsrmRouter(city)

    total_distance = 0.0
    total_duration = 0.0
    total_routes = 0
    max_route_duration = 0.0
    detail_rows = []

    groups = group_customers_by_facility(
        assigned_microhubs
    )

    for microhub_name, customers in groups.items():

        plan = router.build_capacity_plan(
            depot_latitude=float(customers["facility_latitude"].iloc[0]),
            depot_longitude=float(customers["facility_longitude"].iloc[0]),
            clients=customers[["Latitude", "Longitude"]],
            transport_mode="cycling",
            vehicle_capacity=bike_capacity,
            client_demands=customers["Demand"].to_numpy(dtype=float),
            route_start_time_per_route_min=BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
            routing_algorithm=routing_config.algorithm,
            ils_max_iterations=routing_config.ils_max_iterations,
            ils_max_iterations_without_improvement=(
                routing_config.ils_max_iterations_without_improvement
            ),
            ils_perturbation_moves=routing_config.ils_perturbation_moves,
            ils_random_seed=routing_config.ils_random_seed,
        )

        total_distance += plan.total_distance_km
        total_duration += plan.total_duration_min
        total_routes += plan.route_count
        max_route_duration = max(
            max_route_duration,
            plan.max_route_duration_min,
        )
        detail_rows.extend(
            build_route_detail_rows(
                city=city,
                neighborhood_name=neighborhood_name,
                model_code="M3",
                leg="cycling_last_mile",
                vehicle_type="cargo_bike",
                depot_name=microhub_name,
                plan=plan,
                clients=customers,
            )
        )

    return (
        total_distance,
        total_duration,
        total_routes,
        max_route_duration,
        detail_rows,
    )

def calculate_pudo_last_mile(
    city: str,
    assigned_pudos: pd.DataFrame,
    walking_capacity: float,
    neighborhood_name: str,
    routing_config: RoutingAlgorithmConfig,
) -> tuple[float, float, int, float, int, list[dict]]:
    """
    Simulate walking delivery routes independently from every used PUDO.

    Returns
    -------
    total_distance_km
    total_duration_min
    total_routes
    max_route_duration_min
    """

    validate_required_columns(
        assigned_pudos,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Latitude",
            "Longitude",
            "Demand",
        },
        "assigned PUDOs",
    )

    if assigned_pudos.empty:
        return 0.0, 0.0, 0, 0.0, 0, []

    router = CapacityAwareOsrmRouter(city)

    total_distance = 0.0
    total_duration = 0.0
    total_routes = 0
    max_route_duration = 0.0
    detail_rows = []

    groups = group_customers_by_facility(assigned_pudos)
    used_pudo_count = len(groups)

    for pudo_name, customers in groups.items():
        plan = router.build_capacity_plan(
            depot_latitude=float(
                customers["facility_latitude"].iloc[0]
            ),
            depot_longitude=float(
                customers["facility_longitude"].iloc[0]
            ),
            clients=customers[["Latitude", "Longitude"]],
            transport_mode="walking",
            vehicle_capacity=walking_capacity,
            client_demands=customers["Demand"].to_numpy(dtype=float),
            route_start_time_per_route_min=WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
            routing_algorithm=routing_config.algorithm,
            ils_max_iterations=routing_config.ils_max_iterations,
            ils_max_iterations_without_improvement=(
                routing_config.ils_max_iterations_without_improvement
            ),
            ils_perturbation_moves=routing_config.ils_perturbation_moves,
            ils_random_seed=routing_config.ils_random_seed,
        )

        total_distance += plan.total_distance_km
        total_duration += plan.total_duration_min
        total_routes += plan.route_count
        max_route_duration = max(
            max_route_duration,
            plan.max_route_duration_min,
        )
        detail_rows.extend(
            build_route_detail_rows(
                city=city,
                neighborhood_name=neighborhood_name,
                model_code="M4",
                leg="walking_last_mile",
                vehicle_type="walking_courier",
                depot_name=pudo_name,
                plan=plan,
                clients=customers,
            )
        )

    return (
        total_distance,
        total_duration,
        total_routes,
        max_route_duration,
        used_pudo_count,
        detail_rows,
    )



def get_parameters(parameters: pd.DataFrame) -> dict:
    """Convert the model parameter table into a model-indexed dictionary."""

    return (
        parameters
        .set_index("modelo")
        .to_dict(orient="index")
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

def load_classified_locations(city: str) -> pd.DataFrame:
    classified_path = (
        RESULTS_DIR
        / city
        / "location_review"
        / "records_classified.csv"
    )

    if not classified_path.exists():
        raise FileNotFoundError(
            "Classified location data was not found: "
            f"{classified_path.resolve()}. "
            "Run code.analysis.review_b2c_locations first."
        )

    classified_locations = pd.read_csv(classified_path)

    required_columns = {
        "Record_Service_Type_Code",
        "Latitude",
        "Longitude",
    }

    missing_columns = required_columns.difference(
        classified_locations.columns
    )

    if missing_columns:
        raise ValueError(
            "Classified location data is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    return classified_locations

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

def select_logistics_center(
    centers: pd.DataFrame,
    last_mile_lat: float,
    last_mile_lon: float,
    *,
    osrm_host: str,
    osrm_profile: str,
):
    """
    Select the logistics center with the shortest bidirectional trunk route.

    Coordinate index 0 is the last-mile point. Indices 1..n are the
    logistics centers, so one OSRM table request provides both directions.
    """

    centers = centers.copy()

    coords = [(last_mile_lon, last_mile_lat)] + list(
        zip(centers["Longitude"], centers["Latitude"])
    )

    distance_matrix = osrm_table(
        coords,
        host=osrm_host,
        profile=osrm_profile,
    )
    center_indices = np.arange(1, len(centers) + 1)

    centers["distancia_troncal_ida_km"] = distance_matrix[center_indices, 0]
    centers["distancia_troncal_regreso_km"] = distance_matrix[0, center_indices]
    centers["distancia_troncal_total_km"] = (
        centers["distancia_troncal_ida_km"]
        + centers["distancia_troncal_regreso_km"]
    )

    reachable_centers = centers[
        np.isfinite(centers["distancia_troncal_total_km"])
    ]

    if reachable_centers.empty:
        raise RuntimeError(
            "OSRM could not find a bidirectional route between any logistics "
            "center and the last-mile point."
        )

    return reachable_centers.loc[
        reachable_centers["distancia_troncal_total_km"].idxmin()
    ]

class CapacityAwareOsrmRouter:
    """
    Generic OSRM-backed planner for driving, cycling, and walking routes.

    OSRM supplies mode-specific distance and duration matrices. Clarke-Wright
    then creates routes subject to the supplied vehicle or courier capacity.
    """

    def __init__(self, city: str):
        self.city = city
        self._last_matrix_key = None
        self._last_matrices = None

    def get_matrices(
        self,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        coords = [(depot_longitude, depot_latitude)] + list(
            zip(clients["Longitude"], clients["Latitude"])
        )

        print(
            f"\nQuerying OSRM /table for {len(coords)} points "
            f"using {transport_mode}..."
        )

        matrix_key = (
            transport_mode,
            tuple((round(float(lon), 6), round(float(lat), 6)) for lon, lat in coords),
        )

        if matrix_key == self._last_matrix_key and self._last_matrices is not None:
            print(
                "Reusing the previous OSRM distance and duration matrices "
                f"for {transport_mode}."
            )
            return self._last_matrices

        osrm_host = get_osrm_host(self.city, transport_mode)
        distance_matrix, duration_matrix = osrm_distance_duration_table(
            coords,
            host=osrm_host,
            profile=transport_mode,
        )

        self._last_matrix_key = matrix_key
        self._last_matrices = (distance_matrix, duration_matrix)

        print(
            "Distance and duration matrices received from OSRM "
            f"for {transport_mode}."
        )

        return distance_matrix, duration_matrix

    def build_capacity_plan(
        self,
        *,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
        vehicle_capacity: float,
        client_demands=None,
        max_route_duration_min: float | None = MAX_ROUTE_DURATION_MIN,
        route_start_time_per_route_min: float = 0.0,
        routing_algorithm: str = "cws",
        ils_max_iterations: int = 100,
        ils_max_iterations_without_improvement: int | None = 20,
        ils_perturbation_moves: int = 2,
        ils_random_seed: int | None = 42,
        traffic_profile: TrafficProfile | None = None,
    ) -> OsrmRoutePlan:
        distance_matrix, duration_matrix = self.get_matrices(
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            clients=clients,
            transport_mode=transport_mode,
        )
        if transport_mode == "driving":
            effective_traffic_profile = (
                traffic_profile
                if traffic_profile is not None
                else TrafficProfile(
                    name="baseline",
                    duration_multiplier=1.0,
                    source="baseline_osrm",
                )
            )
        else:
            effective_traffic_profile = TrafficProfile(
                name="not_applied",
                duration_multiplier=1.0,
                source="traffic_not_applied_to_non_driving_mode",
            )

        duration_matrix = apply_traffic_profile(
            duration_matrix,
            effective_traffic_profile,
        )

        if routing_algorithm == "cws":
            algorithm_start = perf_counter()
            total_distance_km, routes = clarke_wright_savings(
                matrix=distance_matrix,
                n_clients=len(clients),
                vehicle_capacity=vehicle_capacity,
                client_demands=client_demands,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
            )
            routing_runtime_seconds = perf_counter() - algorithm_start
            initial_distance_km = float(total_distance_km)

        elif routing_algorithm == "ils":
            # Build a separate CWS reference solution for an explicit and
            # reproducible comparison. This baseline calculation is excluded
            # from the reported ILS runtime; the ILS runtime itself includes
            # its own CWS initialization and all local-search iterations.
            initial_distance_km, _ = clarke_wright_savings(
                matrix=distance_matrix,
                n_clients=len(clients),
                vehicle_capacity=vehicle_capacity,
                client_demands=client_demands,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
            )

            algorithm_start = perf_counter()
            total_distance_km, routes = iterated_local_search(
                matrix=distance_matrix,
                n_clients=len(clients),
                vehicle_capacity=vehicle_capacity,
                client_demands=client_demands,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
                max_iterations=ils_max_iterations,
                max_iterations_without_improvement=(
                    ils_max_iterations_without_improvement
                ),
                perturbation_moves=ils_perturbation_moves,
                random_seed=ils_random_seed,
            )
            routing_runtime_seconds = perf_counter() - algorithm_start

        else:
            raise ValueError(
                "Unsupported routing algorithm: "
                f"{routing_algorithm!r}. Expected 'cws' or 'ils'."
            )

        initial_distance_km = float(initial_distance_km)
        total_distance_km = float(total_distance_km)
        improvement_distance_km = max(
            0.0,
            initial_distance_km - total_distance_km,
        )
        improvement_percent = (
            100.0 * improvement_distance_km / initial_distance_km
            if initial_distance_km > 0.0
            else 0.0
        )

        print(
            f"Routing metrics [{routing_algorithm.upper()} | {transport_mode}]: "
            f"initial={initial_distance_km:.3f} km | "
            f"final={total_distance_km:.3f} km | "
            f"improvement={improvement_distance_km:.3f} km "
            f"({improvement_percent:.2f}%) | "
            f"runtime={routing_runtime_seconds:.4f} s"
        )
            
        route_durations = calculate_route_durations(
            duration_matrix,
            routes,
            route_start_time_per_route_min=route_start_time_per_route_min,
        )

        total_duration_min = sum(route_durations)
        route_distances = [
            calculate_routes_matrix_cost(distance_matrix, [route])
            for route in routes
        ]
        demands = (
            np.ones(len(clients), dtype=float)
            if client_demands is None
            else np.asarray(client_demands, dtype=float)
        )
        route_loads = calculate_route_loads(routes, demands)

        service_time = (
            len(clients) * SERVICE_TIME_PER_STOP_MIN
            + len(routes) * float(route_start_time_per_route_min)
        )

        return OsrmRoutePlan(
            transport_mode=transport_mode,
            vehicle_capacity=float(vehicle_capacity),
            routes=routes,
            total_distance_km=total_distance_km,
            total_duration_min=total_duration_min,
            route_durations_min=route_durations,
            route_distances_km=route_distances,
            route_loads=route_loads,
            route_start_time_per_route_min=float(route_start_time_per_route_min),
            service_time_min=service_time,
            routing_algorithm=routing_algorithm,
            routing_runtime_seconds=float(routing_runtime_seconds),
            initial_distance_km=initial_distance_km,
            improvement_distance_km=improvement_distance_km,
            improvement_percent=improvement_percent,
            traffic_profile=effective_traffic_profile.name,
            traffic_duration_multiplier=(
                effective_traffic_profile.duration_multiplier
            ),
            traffic_source=effective_traffic_profile.source,
        )

    def build_independent_round_trips(
        self,
        *,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
        trip_weights=None,
    ) -> tuple[float, float]:
        distance_matrix, duration_matrix = self.get_matrices(
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            clients=clients,
            transport_mode=transport_mode,
        )

        client_count = len(clients)
        if trip_weights is None:
            weights = np.ones(client_count, dtype=float)
        else:
            weights = np.asarray(trip_weights, dtype=float)
            if weights.shape != (client_count,):
                raise ValueError("trip_weights must contain one value per client.")

        distance = sum(
            (distance_matrix[0, i] + distance_matrix[i, 0]) * weights[i - 1]
            for i in range(1, client_count + 1)
        )
        duration = sum(
            (duration_matrix[0, i] + duration_matrix[i, 0]) * weights[i - 1]
            for i in range(1, client_count + 1)
        )
        return float(distance), float(duration)



def expand_facility_supply_visits(
    facilities: pd.DataFrame,
    vehicle_capacity: float,
) -> pd.DataFrame:
    """
    Split each facility demand into vehicle-sized delivery visits.

    Every resulting row is one visit that can be assigned to one route.
    A route still leaves the logistics center once and returns once; repeated
    facility coordinates represent separate vehicles when demand exceeds
    capacity.
    """

    validate_required_columns(
        facilities,
        {"Location", "Latitude", "Longitude", "Demand"},
        "facility supply summary",
    )

    vehicle_capacity = float(vehicle_capacity)

    if vehicle_capacity <= 0:
        raise ValueError("Vehicle capacity must be greater than zero.")

    expanded_rows = []

    for facility in facilities.itertuples(index=False):
        remaining_demand = float(facility.Demand)
        visit_number = 1

        if remaining_demand <= 0:
            continue

        while remaining_demand > 0:
            visit_demand = min(remaining_demand, vehicle_capacity)

            expanded_rows.append(
                {
                    "Location": facility.Location,
                    "Latitude": float(facility.Latitude),
                    "Longitude": float(facility.Longitude),
                    "Demand": visit_demand,
                    "SupplyVisit": visit_number,
                }
            )

            remaining_demand -= visit_demand
            visit_number += 1

    return pd.DataFrame(
        expanded_rows,
        columns=[
            "Location",
            "Latitude",
            "Longitude",
            "Demand",
            "SupplyVisit",
        ],
    )


def _calculate_facility_supply_metrics(
    supply_distance_km: float,
    supply_duration_min: float,
    parameters: dict,
) -> tuple[float, float]:
    """Calculate conventional-van supply cost and emissions."""

    van = parameters["FURGONETA_CONV"]
    supply_cost = (
        supply_distance_km * van["costo_km"]
        + (supply_duration_min / 60.0) * van["costo_hora"]
    )
    supply_co2 = (supply_distance_km * van["co2_km"]) / 1000

    return supply_cost, supply_co2


def calculate_facility_supply_route(
    city: str,
    selected_cc: pd.Series,
    used_facilities: pd.DataFrame,
    truck_capacity: float,
    facility_label: str,
    max_route_duration_min: float = MAX_ROUTE_DURATION_MIN,
    *,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
) -> tuple[OsrmRoutePlan, pd.DataFrame]:
    """Route logistics-center supply to every used facility.

    Facility demand is split into visits no larger than truck capacity.
    Each CWS route represents a truck tour that leaves the logistics center
    once and returns once.
    """

    if used_facilities.empty:
        return OsrmRoutePlan(
            transport_mode="driving",
            vehicle_capacity=float(truck_capacity),
            routes=[],
            total_distance_km=0.0,
            total_duration_min=0.0,
            route_durations_min=[],
            route_distances_km=[],
            route_loads=[],
            route_start_time_per_route_min=TRUCK_LOADING_TIME_PER_ROUTE_MIN,
            service_time_min=0.0,
            routing_algorithm=routing_config.algorithm,
            routing_runtime_seconds=0.0,
            initial_distance_km=0.0,
            improvement_distance_km=0.0,
            improvement_percent=0.0,
            traffic_profile=traffic_profile.name,
            traffic_duration_multiplier=traffic_profile.duration_multiplier,
            traffic_source=traffic_profile.source,
        ), pd.DataFrame()

    supply_visits = expand_facility_supply_visits(
        used_facilities,
        truck_capacity,
    )

    print(
        f"{facility_label} supply: {len(used_facilities)} used facilities, "
        f"{len(supply_visits)} capacity-feasible visits"
    )

    router = CapacityAwareOsrmRouter(city)
    plan = router.build_capacity_plan(
        depot_latitude=float(selected_cc["Latitude"]),
        depot_longitude=float(selected_cc["Longitude"]),
        clients=supply_visits[["Latitude", "Longitude"]],
        transport_mode="driving",
        vehicle_capacity=truck_capacity,
        client_demands=supply_visits["Demand"].to_numpy(dtype=float),
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=TRUCK_LOADING_TIME_PER_ROUTE_MIN,
        routing_algorithm=routing_config.algorithm,
        ils_max_iterations=routing_config.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            routing_config.ils_max_iterations_without_improvement
        ),
        ils_perturbation_moves=routing_config.ils_perturbation_moves,
        ils_random_seed=routing_config.ils_random_seed,
        traffic_profile=traffic_profile,
    )

    print(
        f"{facility_label} supply plan: {plan.route_count} truck routes, "
        f"{plan.total_distance_km:.2f} km, "
        f"{plan.total_duration_min:.2f} total min"
    )

    return plan, supply_visits

def simulate_neighborhood(
    city: str,
    neighborhood_name: str,
    demand_points: pd.DataFrame,
    demand_centroid: OperationalPoint,
    microhub_point: OperationalPoint,
    pudo_point: OperationalPoint,
    assigned_pudos: pd.DataFrame,
    assigned_microhubs: pd.DataFrame,
    centers: pd.DataFrame,
    parameters: dict,
    cost_parameters: dict[str, float],
    *,
    osrm_host: str,
    osrm_profile: str,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
):
    """Run the five models using demand stops and classified facilities."""

    customer_count = len(demand_points)
    package_count = int(demand_points["Demand"].sum())
    client_demands = demand_points["Demand"].to_numpy(dtype=float)

    if customer_count == 0 or package_count == 0:
        return [], {model: [] for model in ("M1", "M2", "M3", "M4", "M5")}

    route_planner = CapacityAwareOsrmRouter(city)

    # M1/M2: direct routes from the selected warehouse to demand stops.
    direct_cc = select_logistics_center(
        centers,
        demand_centroid.latitude,
        demand_centroid.longitude,
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    direct_cc_lat = float(direct_cc["Latitude"])
    direct_cc_lon = float(direct_cc["Longitude"])

    m1_plan = route_planner.build_capacity_plan(
        depot_latitude=direct_cc_lat,
        depot_longitude=direct_cc_lon,
        clients=demand_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_CONV"]["capacidad"],
        client_demands=client_demands,
        route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
        routing_algorithm=routing_config.algorithm,
        ils_max_iterations=routing_config.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            routing_config.ils_max_iterations_without_improvement
        ),
        ils_perturbation_moves=routing_config.ils_perturbation_moves,
        ils_random_seed=routing_config.ils_random_seed,
        traffic_profile=traffic_profile,
    )
    m2_plan = route_planner.build_capacity_plan(
        depot_latitude=direct_cc_lat,
        depot_longitude=direct_cc_lon,
        clients=demand_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
        client_demands=client_demands,
        route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
        routing_algorithm=routing_config.algorithm,
        ils_max_iterations=routing_config.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            routing_config.ils_max_iterations_without_improvement
        ),
        ils_perturbation_moves=routing_config.ils_perturbation_moves,
        ils_random_seed=routing_config.ils_random_seed,
        traffic_profile=traffic_profile,
    )

    # Direct models have no separate trunk leg: CWS includes CC departures/returns.
    zero_trunk = 0.0

    # M3: supply every used microhub from one selected logistics center,
    # then perform bicycle routing independently from each microhub.
    used_microhubs = build_facility_summary(assigned_microhubs)

    m3_cc = select_logistics_center(
        centers,
        float(used_microhubs["Latitude"].mean()),
        float(used_microhubs["Longitude"].mean()),
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    

    m3_supply_plan, m3_supply_visits = calculate_facility_supply_route(
        city=city,
        selected_cc=m3_cc,
        used_facilities=used_microhubs,
        truck_capacity=parameters["FURGONETA_CONV"]["capacidad"],
        facility_label="M3",
        routing_config=routing_config,
        traffic_profile=traffic_profile,
    )

    (
        m3_bike_distance_km,
        m3_bike_duration_min,
        m3_bike_route_count,
        m3_bike_max_route_duration_min,
        m3_bike_detail_rows,
    ) = calculate_microhub_last_mile(
        city=city,
        assigned_microhubs=assigned_microhubs,
        bike_capacity=parameters["BICICLETA_CARGO"]["capacidad"],
        neighborhood_name=neighborhood_name,
        routing_config=routing_config,
    )

    used_microhub_count = len(used_microhubs)

    # M4/M5: supply every used PUDO from one selected logistics center.
    used_pudos = build_facility_summary(assigned_pudos)
    pudo_cc = select_logistics_center(
        centers,
        float(used_pudos["Latitude"].mean()),
        float(used_pudos["Longitude"].mean()),
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    pudo_supply_plan, pudo_supply_visits = calculate_facility_supply_route(
        city=city,
        selected_cc=pudo_cc,
        used_facilities=used_pudos,
        truck_capacity=parameters["FURGONETA_CONV"]["capacidad"],
        facility_label="M4/M5",
        routing_config=routing_config,
        traffic_profile=traffic_profile,
    )
    (
        m4_distance_km,
        m4_duration_min,
        m4_route_count,
        m4_max_route_duration_min,
        m4_used_pudo_count,
        m4_walking_detail_rows,
    ) = calculate_pudo_last_mile(
        city=city,
        assigned_pudos=assigned_pudos,
        walking_capacity=parameters["PUDO_A_PIE"]["capacidad"],
        neighborhood_name=neighborhood_name,
        routing_config=routing_config,
    )
    (
        customer_travel_km,
        customer_travel_min,
        m5_customer_detail_rows,
    ) = calculate_customer_collection_travel(
        city,
        assigned_pudos,
        neighborhood_name,
    )

    print(f"Demand: {customer_count} customers, {package_count} packages")
    print(
            f"M1 {routing_config.algorithm.upper()} driving: "
            f"{m1_plan.route_count} routes | "
            f"{m1_plan.total_distance_km:.2f} km | "
            f"total={m1_plan.total_duration_min:.1f} min | "
            f"max={m1_plan.max_route_duration_min:.1f} min"
        )
    print(
            f"M2 {routing_config.algorithm.upper()} driving: "
            f"{m2_plan.route_count} routes | "
            f"{m2_plan.total_distance_km:.2f} km | "
            f"total={m2_plan.total_duration_min:.1f} min | "
            f"max={m2_plan.max_route_duration_min:.1f} min"
        )
    print(
        f"M3 supply: "
        f"{m3_supply_plan.route_count} truck routes | "
        f"{m3_supply_plan.total_distance_km:.2f} km | "
        f"total={m3_supply_plan.total_duration_min:.1f} min | "
        f"max={m3_supply_plan.max_route_duration_min:.1f} min"
    )

    print(
        f"M3 cycling: "
        f"{used_microhub_count} used microhubs | "
        f"{m3_bike_route_count} routes | "
        f"{m3_bike_distance_km:.2f} km | "
        f"{m3_bike_duration_min:.1f} min"
    )
    print(
        f"M4/M5 supply: "
        f"{pudo_supply_plan.route_count} truck routes | "
        f"{pudo_supply_plan.total_distance_km:.2f} km | "
        f"total={pudo_supply_plan.total_duration_min:.1f} min | "
        f"max={pudo_supply_plan.max_route_duration_min:.1f} min"
    )
    print(
        f"M4 walking: "
        f"{m4_used_pudo_count} used PUDOs | "
        f"{m4_route_count} routes | "
        f"{m4_distance_km:.2f} km | "
        f"total={m4_duration_min:.1f} min | "
        f"max={m4_max_route_duration_min:.1f} min"
    )

    print(f"M5 collection: {len(used_pudos)} used PUDOs")
    print(
        "M5 customer round trips: "
        f"{customer_travel_km:.2f} km, {customer_travel_min:.2f} min"
    )

    m1_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M1",
        leg="direct_delivery",
        vehicle_type="conventional_van",
        depot_name=str(direct_cc["Location"]),
        plan=m1_plan,
        clients=demand_points,
    )
    m2_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M2",
        leg="direct_delivery",
        vehicle_type="electric_van",
        depot_name=str(direct_cc["Location"]),
        plan=m2_plan,
        clients=demand_points,
    )
    m3_supply_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M3",
        leg="facility_supply",
        vehicle_type="conventional_van",
        depot_name=str(m3_cc["Location"]),
        plan=m3_supply_plan,
        clients=m3_supply_visits,
        stop_label_column="Location",
    )
    m45_supply_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M4",
        leg="facility_supply",
        vehicle_type="conventional_van",
        depot_name=str(pudo_cc["Location"]),
        plan=pudo_supply_plan,
        clients=pudo_supply_visits,
        stop_label_column="Location",
    )
    m5_supply_detail_rows = [
        {**row, "model": "M5", "route_id": row["route_id"].replace("M4_", "M5_", 1)}
        for row in m45_supply_detail_rows
    ]

    model_details = {
        "M1": m1_detail_rows,
        "M2": m2_detail_rows,
        "M3": m3_supply_detail_rows + m3_bike_detail_rows,
        "M4": m45_supply_detail_rows + m4_walking_detail_rows,
        "M5": m5_supply_detail_rows + m5_customer_detail_rows,
    }

    summary_results = [
        simulate_m1(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=direct_cc,
            last_mile_point=demand_centroid,
            package_count=package_count,
            route_plan=m1_plan,
            parameters=parameters,
            cost_parameters=cost_parameters,
        ),
        simulate_m2(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=direct_cc,
            last_mile_point=demand_centroid,
            package_count=package_count,
            route_plan=m2_plan,
            parameters=parameters,
            cost_parameters=cost_parameters,
        ),
        simulate_m3(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=m3_cc,
            last_mile_point=microhub_point,
            package_count=package_count,
            used_microhub_count=used_microhub_count,
            supply_plan=m3_supply_plan,
            bike_distance_km=m3_bike_distance_km,
            bike_duration_min=m3_bike_duration_min,
            bike_route_count=m3_bike_route_count,
            parameters=parameters,
            cost_parameters=cost_parameters,
        ),
        simulate_m4(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=pudo_cc,
            last_mile_point=pudo_point,
            package_count=package_count,
            used_pudo_count=m4_used_pudo_count,
            supply_plan=pudo_supply_plan,
            walking_distance_km=m4_distance_km,
            walking_duration_min=m4_duration_min,
            walking_route_count=m4_route_count,
            parameters=parameters,
            cost_parameters=cost_parameters,
        ),
        simulate_m5(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=pudo_cc,
            last_mile_point=pudo_point,
            package_count=package_count,
            customer_count=customer_count,
            used_pudo_count=len(used_pudos),
            supply_plan=pudo_supply_plan,
            customer_travel_km=customer_travel_km,
            customer_travel_min=customer_travel_min,
            parameters=parameters,
            cost_parameters=cost_parameters,
        ),
    ]

    return summary_results, model_details


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
 
 
def simulate_city(
    city: str,
    demand_scenario: str,
    instance_size: int,
    active_zones: list[str] | None = None,
    *,
    osrm_host: str,
    osrm_profile: str,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
    cost_parameters: dict[str, float],
):
    centers, boundaries, parameters_df = load_city_data(city)
    demand_instance = load_demand_instance(city, demand_scenario, instance_size)
    classified_locations = load_classified_locations(city)
    microhubs, pudos = load_facility_candidates(classified_locations)
 
    print(
        f"Available facilities: "
        f"{len(microhubs)} microhubs, "
        f"{len(pudos)} PUDOs"
    )
    parameters = get_parameters(parameters_df)
 
    all_results = []
    all_model_details = {model: [] for model in ("M1", "M2", "M3", "M4", "M5")}
 
    if active_zones is not None:
        boundaries = select_zones(boundaries, active_zones)
        warn_zone_overlaps(boundaries)
 
    for _, neighborhood in boundaries.iterrows():
        neighborhood_name = neighborhood["zona"]
        zone_type = neighborhood["tipo"]
        print(f"\nSimulating {city.upper()} - {neighborhood_name} ({zone_type})")
 
        demand_points = filter_points_by_neighborhood(demand_instance, neighborhood)
        if demand_points.empty:
            print(f"No demand stops within {neighborhood_name}.")
            continue
 
        demand_centroid = calculate_demand_weighted_centroid(demand_points)
        microhub_point = select_operational_point(
            strategy="nearest_microhub_facility",
            neighborhood_records=demand_points,
            classified_records=classified_locations,
            target_latitude=demand_centroid.latitude,
            target_longitude=demand_centroid.longitude,
        )
        pudo_point = select_operational_point(
            strategy="nearest_pudo_facility",
            neighborhood_records=demand_points,
            classified_records=classified_locations,
            target_latitude=demand_centroid.latitude,
            target_longitude=demand_centroid.longitude,
        )
 
        neighborhood_microhubs = filter_points_by_neighborhood(
            microhubs,
            neighborhood,
        )

        microhub_capacity = float(
            parameters["MICROHUB"]["capacidad"]
        )

        pudo_capacity = float(
        parameters["PUDO"]["capacidad"]
    )
 
        if neighborhood_microhubs.empty:
            neighborhood_microhubs = microhubs
 
        assigned_microhubs = assign_customers_to_nearest_facility(
            customers=demand_points,
            facilities=neighborhood_microhubs,
            osrm_host=osrm_host,
            osrm_profile="cycling",
            facility_capacity=microhub_capacity,
        )
 
        print(
            f"Neighborhood microhubs: {len(neighborhood_microhubs)}, "
            f"used: {assigned_microhubs['assigned_facility'].nunique()}"
        )
 
        assigned_pudos = assign_customers_to_nearest_facility(
            customers=demand_points,
            facilities=pudos,
            osrm_host=osrm_host,
            osrm_profile="walking",
            facility_capacity=pudo_capacity,
        )
 
        results, model_details = simulate_neighborhood(
            city=city,
            neighborhood_name=neighborhood_name,
            demand_points=demand_points,
            demand_centroid=demand_centroid,
            microhub_point=microhub_point,
            pudo_point=pudo_point,
            assigned_pudos=assigned_pudos,
            assigned_microhubs=assigned_microhubs,
            centers=centers,
            parameters=parameters,
            cost_parameters=cost_parameters,
            osrm_host=osrm_host,
            osrm_profile=osrm_profile,
            routing_config=routing_config,
            traffic_profile=traffic_profile,
        )
 
        all_results.extend(results)
        for model_code, detail_rows in model_details.items():
            all_model_details[model_code].extend(detail_rows)
        print(
            f"{neighborhood_name} ({zone_type}): {len(demand_points)} customers, "
            f"{int(demand_points['Demand'].sum())} packages simulated"
        )
 
    return (
        pd.DataFrame(all_results),
        {
            model_code: pd.DataFrame(detail_rows)
            for model_code, detail_rows in all_model_details.items()
        },
    )

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the OSRM-based logistics simulator."
    )

    parser.add_argument(
        "--city",
        choices=CITIES + ["all"],
        default="madrid",
        help="City to simulate ('all' runs every city)",
    )
    
    parser.add_argument(
        "--zones",
        nargs="+",
        default=None,
        help=(
            "Names of zones to simulate"
            "If it is omitted, all the zones of the file are processed"
        ),
    )

    parser.add_argument(
        "--demand-scenario",
        choices=("low", "medium", "high"),
        default="medium",
        help="Demand scenario to load from results/<city>/demand.",
    )

    parser.add_argument(
        "--instance-size",
        type=int,
        default=100,
        help="Number encoded in demand_<scenario>_<size>.csv.",
    )

    parser.add_argument(
        "--profile",
        choices=tuple(OSRM_PORTS["madrid"]),
        default="driving",
        help=(
            "OSRM profile used for logistics-center trunk routing. "
            "Default: driving."
        ),
    )
    
    parser.add_argument(
        "--routing-algorithm",
        choices=["cws", "ils"],
        default="cws",
        help=(
            "Routing algorithm used to construct capacity-aware routes. "
            "Default: cws."
        ),
    )
    
    parser.add_argument(
        "--ils-max-iterations",
        type=int,
        default=100,
        help="Maximum number of ILS iterations. Default: 100.",
    )

    parser.add_argument(
        "--ils-max-no-improvement",
        type=int,
        default=20,
        help=(
            "Stop ILS after this number of iterations without improvement. "
            "Default: 20."
        ),
    )

    parser.add_argument(
        "--ils-perturbation-moves",
        type=int,
        default=2,
        help="Number of perturbation moves per ILS iteration. Default: 2.",
    )

    parser.add_argument(
        "--ils-random-seed",
        type=int,
        default=42,
        help="Random seed used by ILS. Default: 42.",
    )
    
    parser.add_argument(
        "--traffic-profile",
        default="baseline",
        help=(
            "Traffic profile loaded from data/traffic_profiles.csv. "
            "Default: baseline."
        ),
    )

    parser.add_argument(
        "--traffic-multiplier",
        type=float,
        default=None,
        help=(
            "Optional multiplier overriding the selected CSV traffic profile. "
            "Useful for sensitivity tests."
        ),
    )

    return parser.parse_args()

      
if __name__ == "__main__":
    args = parse_arguments()

    routing_config = RoutingAlgorithmConfig(
        algorithm=args.routing_algorithm,
        ils_max_iterations=args.ils_max_iterations,
        ils_max_iterations_without_improvement=args.ils_max_no_improvement,
        ils_perturbation_moves=args.ils_perturbation_moves,
        ils_random_seed=args.ils_random_seed,
    )

    if args.city == "all" and args.zones is not None:

        raise SystemExit(
            "Error: --zones cannot be used together with --city all."
        )

    active_profile = args.profile
    active_zones = args.zones

    cities = CITIES if args.city == "all" else [args.city]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cost_parameters = load_cost_parameters(
        DATA_DIR / "cost_parameters.csv"
    )

    for active_city in cities:
        active_host = get_osrm_host(active_city, active_profile)
        traffic_profile = load_traffic_profile(
            csv_path=DATA_DIR / "traffic_profiles.csv",
            profile_name=args.traffic_profile,
            city=active_city,
            multiplier_override=args.traffic_multiplier,
        )

        print("\n" + "=" * 60)
        print("OSRM-BASED LOGISTICS SIMULATION")
        print("=" * 60)
        print(f"City: {active_city}")
        print(f"Neighborhood: {active_zones}")
        print(f"OSRM host: {active_host}")
        print(f"OSRM profile: {active_profile}")
        print(f"Routing algorithm: {routing_config.algorithm.upper()}")
        print(
            "Traffic profile: "
            f"{traffic_profile.name} "
            f"(x{traffic_profile.duration_multiplier:.3f}, "
            f"source={traffic_profile.source})"
        )
        print("=" * 60)

        check_osrm_server(
            city=active_city,
            host=active_host,
            profile=active_profile,
        )

        print("Loading data...")

        results_df, model_detail_frames = simulate_city(
            city=active_city,
            demand_scenario=args.demand_scenario,
            instance_size=args.instance_size,
            active_zones=active_zones,
            osrm_host=active_host,
            osrm_profile=active_profile,
            routing_config=routing_config,
            traffic_profile=traffic_profile,
            cost_parameters=cost_parameters,
        )

        results_df["routing_algorithm"] = routing_config.algorithm
        results_df["traffic_profile"] = traffic_profile.name
        results_df["traffic_duration_multiplier"] = (
            traffic_profile.duration_multiplier
        )
        results_df["traffic_source"] = traffic_profile.source

        model_detail_frames = {
            model_code: detail_df.assign(
                routing_algorithm=routing_config.algorithm,
                selected_traffic_profile=traffic_profile.name,
            )
            for model_code, detail_df in model_detail_frames.items()
        }

        zone_suffix = ""

        if active_zones:
            normalized_zones = "_".join(
                str(zone).strip().replace(" ", "_")
                for zone in active_zones
            )
            zone_suffix = f"_{normalized_zones}"

        output_filename = (
            f"resultados_osrm_{active_city}{zone_suffix}_"
            f"{args.demand_scenario}_{args.instance_size}_"
            f"{routing_config.algorithm}_{traffic_profile.name}.csv"
        )

        output_path = RESULTS_DIR / output_filename

        results_df.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        detail_output_folder = (
            RESULTS_DIR
            / active_city
            / "simulation_details"
            / (
                f"{args.demand_scenario}_{args.instance_size}_"
                f"{routing_config.algorithm}_{traffic_profile.name}"
            )
        )
        detail_output_folder.mkdir(parents=True, exist_ok=True)

        for model_code, detail_df in model_detail_frames.items():
            detail_path = detail_output_folder / f"{model_code.lower()}_routes.csv"
            detail_df.to_csv(
                detail_path,
                index=False,
                encoding="utf-8-sig",
            )
            print(f"{model_code} route details saved to: {detail_path.resolve()}")

        print(f"\nResults saved to: {output_path.resolve()}")