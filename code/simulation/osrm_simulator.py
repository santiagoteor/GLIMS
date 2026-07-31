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

from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests

from code.common.paths import DATA_DIR, PROJECT_ROOT
from code.simulation.operational_points import (
    OperationalPoint,
    get_facility_candidates,
    select_operational_point,
)

from code.common.constants import (
    MICROHUB_FACILITY_CODES,
    PUDO_FACILITY_CODES,
)

import argparse


OSRM_PORTS = {
    "madrid": {
        "driving": 5000,
        "cycling": 5001,
        "walking": 5002,
    },
    "barcelona": {
        "driving": 5010,
        "cycling": 5011,
        "walking": 5012,
    },
    "valencia": {
        "driving": 5020,
        "cycling": 5021,
        "walking": 5022,
    },
}

CITIES = ["madrid", "barcelona", "valencia"]

MAX_ROUTE_DURATION_MIN = 480.0
SERVICE_TIME_PER_STOP_MIN = 5.0
DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN = 15.0
TRUCK_LOADING_TIME_PER_ROUTE_MIN = 15.0
BIKE_PREPARATION_TIME_PER_ROUTE_MIN = 10.0
WALKING_PREPARATION_TIME_PER_ROUTE_MIN = 5.0

OSRM_PROBE_COORDS = {
    "madrid": [(-3.703790, 40.416775), (-3.693790, 40.426775)],
    "barcelona": [(2.173404, 41.385064), (2.183404, 41.395064)],
    "valencia": [(-0.376288, 39.469907), (-0.366288, 39.479907)],
}

def load_city_data(
    city: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load logistics centers, neighborhood limits, and model parameters."""

    city_folder = DATA_DIR / city

    centers_path = city_folder / "centros_cc.csv"
    boundaries_path = city_folder / "limites_barrios.csv"
    parameters_path = DATA_DIR / "parametros_modelos.csv"

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
    boundaries = pd.read_csv(boundaries_path)
    parameters = pd.read_csv(parameters_path)

    validate_required_columns(
        centers,
        required_columns={"Location", "Latitude", "Longitude"},
        dataset_name=f"{city} logistics centers",
    )
    validate_required_columns(
        boundaries,
        required_columns={
            "barrio", "lat_min", "lat_max", "lon_min", "lon_max"
        },
        dataset_name=f"{city} neighborhood boundaries",
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
        RESULTS_FOLDER
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


def assign_customers_to_nearest_facility(
    customers: pd.DataFrame,
    facilities: pd.DataFrame,
    facility_name_column: str = "Location",
) -> pd.DataFrame:
    """
    Assign every customer independently to the nearest facility.

    Capacity constraints are ignored.
    If two facilities are equally distant, the first one is selected.
    """

    validate_required_columns(
        customers,
        {"Latitude", "Longitude"},
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

    customer_coords = customers[
        ["Longitude", "Latitude"]
    ].to_numpy(dtype=float)

    facility_coords = facilities[
        ["Longitude", "Latitude"]
    ].to_numpy(dtype=float)

    diff = (
        customer_coords[:, None, :]
        - facility_coords[None, :, :]
    )

    squared_distance = np.sum(diff ** 2, axis=2)

    nearest = np.argmin(squared_distance, axis=1)

    assigned = customers.copy()

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

    assigned["assigned_facility"] = (
        facility_names.iloc[nearest]
        .to_numpy()
    )

    assigned["facility_latitude"] = (
        facilities.iloc[nearest]["Latitude"]
        .to_numpy()
    )

    assigned["facility_longitude"] = (
        facilities.iloc[nearest]["Longitude"]
        .to_numpy()
    )

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


def filter_points_by_neighborhood(
    points: pd.DataFrame,
    neighborhood: pd.Series,
) -> pd.DataFrame:
    """Filter geographical points using a neighborhood bounding box."""

    validate_required_columns(
        points,
        required_columns={"Latitude", "Longitude"},
        dataset_name="geographical points",
    )

    return points[
        points["Latitude"].between(
            neighborhood["lat_min"],
            neighborhood["lat_max"],
        )
        & points["Longitude"].between(
            neighborhood["lon_min"],
            neighborhood["lon_max"],
        )
    ].copy()


def get_osrm_host(city: str, profile: str) -> str:
    """Return the city- and profile-specific local OSRM endpoint."""

    return f"http://localhost:{OSRM_PORTS[city][profile]}"

BASE_DIR = PROJECT_ROOT

RESULTS_FOLDER = BASE_DIR / "results"

def load_classified_locations(city: str) -> pd.DataFrame:
    classified_path = (
        RESULTS_FOLDER
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


def check_osrm_server(city: str, host: str, profile: str) -> None:
    """
    Fail fast with a clear message if no OSRM server is reachable.
    """

    probe_coords = _format_coords(OSRM_PROBE_COORDS[city])
    probe_url = f"{host}/table/v1/{profile}/{probe_coords}"

    try:
        response = requests.get(probe_url, timeout=5)
        response.raise_for_status()
        payload = response.json()

    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Could not reach OSRM server at {host}. "
            "Make sure osrm-routed is running. "
            f"Original error: {exc}"
        ) from exc

    if payload.get("code") != "Ok":
        raise RuntimeError(
            "OSRM server responded, but the test route failed: "
            f"{payload.get('code')} - {payload.get('message', '')}"
        )


def _format_coords(coords):
    return ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)


def osrm_table(
    coords,
    sources=None,
    destinations=None,
    *,
    host: str,
    profile: str,
):
    """
    Query the OSRM /table service for a set of (lon, lat) coordinates
    and return the driving-distance matrix in kilometers.
    """

    url = f"{host}/table/v1/{profile}/{_format_coords(coords)}"

    params = {"annotations": "distance"}

    if sources is not None:
        params["sources"] = ";".join(str(i) for i in sources)

    if destinations is not None:
        params["destinations"] = ";".join(str(i) for i in destinations)

    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM /table error: {payload.get('code')} - {payload.get('message', '')}"
        )

    distances_m = np.array(payload["distances"], dtype=float)

    return distances_m / 1000.0


OSRM_TABLE_BLOCK_SIZE = 200
OSRM_TABLE_MIN_BLOCK_SIZE = 25


def _request_osrm_distance_duration_table(
    coords,
    *,
    host: str,
    profile: str,
    sources=None,
    destinations=None,
) -> tuple[np.ndarray, np.ndarray]:
    """Request one OSRM table, optionally as a rectangular submatrix."""

    url = f"{host}/table/v1/{profile}/{_format_coords(coords)}"
    params = {"annotations": "distance,duration"}

    if sources is not None:
        params["sources"] = ";".join(str(index) for index in sources)
    if destinations is not None:
        params["destinations"] = ";".join(
            str(index) for index in destinations
        )

    response = requests.get(url, params=params, timeout=180)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        details = response.text[:500].strip()
        raise requests.exceptions.HTTPError(
            f"{exc}. OSRM response: {details}",
            response=response,
        ) from exc

    payload = response.json()

    if payload.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM /table error: {payload.get('code')} - "
            f"{payload.get('message', '')}"
        )

    distance_matrix = np.array(payload["distances"], dtype=float) / 1000.0
    duration_matrix = np.array(payload["durations"], dtype=float) / 60.0

    return distance_matrix, duration_matrix


def _build_chunked_osrm_table(
    coords,
    *,
    host: str,
    profile: str,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Assemble a complete matrix from rectangular OSRM table blocks."""

    point_count = len(coords)
    distance_matrix = np.full((point_count, point_count), np.nan, dtype=float)
    duration_matrix = np.full((point_count, point_count), np.nan, dtype=float)

    blocks = [
        (start, min(start + block_size, point_count))
        for start in range(0, point_count, block_size)
    ]
    request_count = len(blocks) ** 2
    completed = 0

    print(
        f"OSRM table is too large for one request; using "
        f"{len(blocks)}x{len(blocks)} blocks ({request_count} requests, "
        f"block size {block_size})."
    )

    for source_start, source_end in blocks:
        source_coords = coords[source_start:source_end]
        source_count = source_end - source_start

        for destination_start, destination_end in blocks:
            destination_coords = coords[destination_start:destination_end]
            destination_count = destination_end - destination_start

            request_coords = source_coords + destination_coords
            sources = range(source_count)
            destinations = range(
                source_count,
                source_count + destination_count,
            )

            block_distances, block_durations = (
                _request_osrm_distance_duration_table(
                    request_coords,
                    host=host,
                    profile=profile,
                    sources=sources,
                    destinations=destinations,
                )
            )

            distance_matrix[
                source_start:source_end,
                destination_start:destination_end,
            ] = block_distances
            duration_matrix[
                source_start:source_end,
                destination_start:destination_end,
            ] = block_durations

            completed += 1
            if completed == request_count or completed % 10 == 0:
                print(
                    f"  OSRM matrix blocks: {completed}/{request_count}",
                    end="\r" if completed < request_count else "\n",
                )

    return distance_matrix, duration_matrix


def osrm_distance_duration_table(
    coords,
    *,
    host: str,
    profile: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return complete OSRM matrices, chunking large requests automatically."""

    try:
        return _request_osrm_distance_duration_table(
            coords,
            host=host,
            profile=profile,
        )
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code not in {400, 414}:
            raise

        block_size = min(OSRM_TABLE_BLOCK_SIZE, max(1, len(coords)))

        while block_size >= OSRM_TABLE_MIN_BLOCK_SIZE:
            try:
                return _build_chunked_osrm_table(
                    coords,
                    host=host,
                    profile=profile,
                    block_size=block_size,
                )
            except requests.exceptions.HTTPError as chunk_exc:
                chunk_status = getattr(chunk_exc.response, "status_code", None)
                if chunk_status not in {400, 414}:
                    raise

                next_block_size = block_size // 2
                if next_block_size < OSRM_TABLE_MIN_BLOCK_SIZE:
                    raise RuntimeError(
                        "OSRM rejected the table request even after chunking. "
                        "Increase the OSRM --max-table-size setting or reduce "
                        "OSRM_TABLE_MIN_BLOCK_SIZE. "
                        f"Last error: {chunk_exc}"
                    ) from chunk_exc

                print(
                    f"OSRM rejected block size {block_size}; retrying with "
                    f"{next_block_size}."
                )
                block_size = next_block_size

        raise RuntimeError(
            "Could not build the OSRM table with the configured block sizes."
        ) from exc


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
def clarke_wright_savings(
    matrix: np.ndarray,
    n_clients: int,
    vehicle_capacity: float,
    client_demands=None,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
):
    """
    Build capacity- and duration-feasible routes using parallel Clarke-Wright.

    Matrix index 0 is the depot and client indices are 1..n_clients.
    Each returned route represents one vehicle that leaves the depot once
    and returns once. Multiple routes therefore mean multiple vehicles.

    When ``duration_matrix`` and ``max_route_duration_min`` are supplied,
    every complete depot-route-depot tour must fit within the duration limit.
    """

    if n_clients <= 0:
        return 0.0, []

    vehicle_capacity = float(vehicle_capacity)
    route_start_time_per_route_min = float(route_start_time_per_route_min)

    if route_start_time_per_route_min < 0:
        raise ValueError("route_start_time_per_route_min cannot be negative.")

    if vehicle_capacity <= 0:
        raise ValueError("Vehicle capacity must be greater than zero.")

    if client_demands is None:
        demands = np.ones(n_clients, dtype=float)
    else:
        demands = np.asarray(client_demands, dtype=float)

        if demands.shape != (n_clients,):
            raise ValueError(
                "client_demands must contain exactly one value per client."
            )

        if np.any(demands < 0):
            raise ValueError("Client demands cannot be negative.")

    if np.any(demands > vehicle_capacity):
        raise ValueError(
            "At least one client demand exceeds the vehicle capacity."
        )

    duration_limit_enabled = (
        duration_matrix is not None
        and max_route_duration_min is not None
    )

    if duration_limit_enabled:
        duration_matrix = np.asarray(duration_matrix, dtype=float)
        expected_shape = (n_clients + 1, n_clients + 1)

        if duration_matrix.shape != expected_shape:
            raise ValueError(
                "duration_matrix must have shape "
                f"{expected_shape}, got {duration_matrix.shape}."
            )

        max_route_duration_min = float(max_route_duration_min)

        if max_route_duration_min <= 0:
            raise ValueError(
                "max_route_duration_min must be greater than zero."
            )

        infeasible_clients = [
            client
            for client in range(1, n_clients + 1)
            if (
                route_start_time_per_route_min
                + duration_matrix[0, client]
                + duration_matrix[client, 0]
                + SERVICE_TIME_PER_STOP_MIN
                > max_route_duration_min
            )
        ]

        if infeasible_clients:
            raise ValueError(
                "At least one client cannot be served within the route "
                f"duration limit of {max_route_duration_min:.0f} minutes, "
                "even in an independent depot-client-depot route. "
                f"Client indices: {infeasible_clients}"
            )

    routes = {
        client: [client]
        for client in range(1, n_clients + 1)
    }
    route_of = {
        client: client
        for client in range(1, n_clients + 1)
    }
    route_loads = {
        client: float(demands[client - 1])
        for client in range(1, n_clients + 1)
    }

    savings = []

    # Directed savings support asymmetric OSRM matrices.
    for i in range(1, n_clients + 1):
        for j in range(1, n_clients + 1):
            if i == j:
                continue

            saving = (
                matrix[i, 0]
                + matrix[0, j]
                - matrix[i, j]
            )

            if np.isfinite(saving):
                savings.append((saving, i, j))

    savings.sort(reverse=True, key=lambda item: item[0])

    for _saving, i, j in savings:
        route_i_id = route_of[i]
        route_j_id = route_of[j]

        if route_i_id == route_j_id:
            continue

        route_i = routes[route_i_id]
        route_j = routes[route_j_id]

        if route_i[-1] != i or route_j[0] != j:
            continue

        merged_load = route_loads[route_i_id] + route_loads[route_j_id]

        if merged_load > vehicle_capacity:
            continue

        merged_route = route_i + route_j

        if duration_limit_enabled:
            merged_duration = calculate_route_durations(
                duration_matrix,
                [merged_route],
                route_start_time_per_route_min=route_start_time_per_route_min,
            )[0]

            if merged_duration > max_route_duration_min:
                continue

        routes[route_i_id] = merged_route
        route_loads[route_i_id] = merged_load

        del routes[route_j_id]
        del route_loads[route_j_id]

        for client in merged_route:
            route_of[client] = route_i_id

    final_routes = list(routes.values())
    total_cost = calculate_routes_matrix_cost(matrix, final_routes)

    return total_cost, final_routes


def calculate_routes_matrix_cost(
    matrix: np.ndarray,
    routes: list[list[int]],
) -> float:
    """Calculate the total matrix cost of depot-based routes."""

    total_cost = 0.0

    for route in routes:
        if not route:
            continue

        total_cost += matrix[0, route[0]]

        for current_client, next_client in zip(route, route[1:]):
            total_cost += matrix[current_client, next_client]

        total_cost += matrix[route[-1], 0]

    return float(total_cost)

def calculate_route_durations(
    duration_matrix,
    routes,
    service_time_per_stop=SERVICE_TIME_PER_STOP_MIN,
    route_start_time_per_route_min=0.0,
):

    durations = []

    for route in routes:

        if not route:
            continue

        driving = duration_matrix[0, route[0]]

        for a, b in zip(route, route[1:]):
            driving += duration_matrix[a, b]

        driving += duration_matrix[route[-1], 0]

        service = len(route) * service_time_per_stop
        route_start = float(route_start_time_per_route_min)

        durations.append(float(route_start + driving + service))

    return durations

def calculate_radial_distance(matrix, n_clients: int):
    """Sum independent depot-client-depot trips on an asymmetric matrix."""

    return sum(
        matrix[0, i] + matrix[i, 0]
        for i in range(1, n_clients + 1)
    )


@dataclass(frozen=True)
class OsrmRoutePlan:
    transport_mode: str
    vehicle_capacity: float
    routes: list[list[int]]
    total_distance_km: float
    total_duration_min: float
    route_durations_min: list[float]
    route_distances_km: list[float]
    route_loads: list[float]
    route_start_time_per_route_min: float
    service_time_min: float

    @property
    def route_count(self):
        return len(self.routes)

    @property
    def max_route_duration_min(self):
        if not self.route_durations_min:
            return 0.0
        return max(self.route_durations_min)

    @property
    def average_route_duration_min(self):
        if not self.route_durations_min:
            return 0.0
        return sum(self.route_durations_min)/len(self.route_durations_min)


def calculate_route_loads(
    routes: list[list[int]],
    client_demands,
) -> list[float]:
    """Return the package load carried by each route."""

    demands = np.asarray(client_demands, dtype=float)
    return [
        float(sum(demands[client_index - 1] for client_index in route))
        for route in routes
    ]


def build_route_detail_rows(
    *,
    city: str,
    neighborhood_name: str,
    model_code: str,
    leg: str,
    vehicle_type: str,
    depot_name: str,
    plan: "OsrmRoutePlan",
    clients: pd.DataFrame,
    stop_label_column: str | None = None,
) -> list[dict]:
    """Convert a route plan into one auditable CSV record per route."""

    rows = []
    normalized_clients = clients.reset_index(drop=True)

    for route_number, route in enumerate(plan.routes, start=1):
        client_rows = normalized_clients.iloc[
            [client_index - 1 for client_index in route]
        ]

        if stop_label_column and stop_label_column in client_rows.columns:
            stop_labels = client_rows[stop_label_column].astype(str).tolist()
        elif "customer_id" in client_rows.columns:
            stop_labels = client_rows["customer_id"].astype(str).tolist()
        else:
            stop_labels = [str(client_index) for client_index in route]

        rows.append(
            {
                "city": city,
                "neighborhood": neighborhood_name,
                "model": model_code,
                "leg": leg,
                "route_id": (
                    f"{model_code}_{neighborhood_name}_{leg}_"
                    f"{depot_name}_{route_number}"
                ),
                "vehicle_type": vehicle_type,
                "depot": depot_name,
                "route_number": route_number,
                "stop_count": len(route),
                "package_load": plan.route_loads[route_number - 1],
                "vehicle_capacity": plan.vehicle_capacity,
                "distance_km": plan.route_distances_km[route_number - 1],
                "duration_min": plan.route_durations_min[route_number - 1],
                "start_handling_min": plan.route_start_time_per_route_min,
                "stop_service_min": len(route) * SERVICE_TIME_PER_STOP_MIN,
                "stop_sequence": " -> ".join(stop_labels),
            }
        )

    return rows


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
    ) -> OsrmRoutePlan:
        distance_matrix, duration_matrix = self.get_matrices(
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            clients=clients,
            transport_mode=transport_mode,
        )

        total_distance_km, routes = clarke_wright_savings(
            matrix=distance_matrix,
            n_clients=len(clients),
            vehicle_capacity=vehicle_capacity,
            client_demands=client_demands,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
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

def calculate_direct_route_operating_cost(
    *,
    distance_km: float,
    total_duration_min: float,
    route_count: int,
    route_start_time_per_route_min: float,
    cost_per_km: float,
    labor_cost_per_hour: float,
) -> tuple[float, float, float]:
    """Return distance, labour, and total direct route operating costs.

    The economic boundary starts when each route departs its origin. Route
    preparation/loading time is therefore excluded, while OSRM travel time and
    stop service time remain included.
    """

    distance_km = float(distance_km)
    total_duration_min = float(total_duration_min)
    route_count = int(route_count)
    route_start_time_per_route_min = float(route_start_time_per_route_min)

    in_route_duration_min = max(
        0.0,
        total_duration_min
        - route_count * route_start_time_per_route_min,
    )
    distance_cost = distance_km * float(cost_per_km)
    labor_cost = (in_route_duration_min / 60.0) * float(labor_cost_per_hour)

    return distance_cost, labor_cost, distance_cost + labor_cost


def _build_result(
    *,
    city: str,
    neighborhood_name: str,
    model_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    total_km: float,
    trip_count: int,
    co2_kg: float,
    route_distance_cost: float,
    route_labor_cost: float,
    facility_service_cost: float,
    total_cost: float,
):
    """Build a result row using the common output schema."""

    return {
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": model_name,
        "centro_logistico": selected_cc["Location"],
        "punto_ultima_milla": last_mile_point.name,
        "latitud_punto_ultima_milla": last_mile_point.latitude,
        "longitud_punto_ultima_milla": last_mile_point.longitude,
        "tipo_punto_ultima_milla": last_mile_point.point_type,
        "estrategia_punto_ultima_milla": last_mile_point.strategy,
        "paquetes": package_count,
        "distancia_troncal_ida_km": outbound_trunk_distance,
        "distancia_troncal_regreso_km": return_trunk_distance,
        "km_recorridos": total_km,
        "numero_viajes": trip_count,
        "emisiones_co2_kg": co2_kg,
        "costo_distancia_ruta_eur": route_distance_cost,
        "costo_laboral_ruta_eur": route_labor_cost,
        "costo_servicio_facility_eur": facility_service_cost,
        "costo_operacion_ruta_eur": route_distance_cost + route_labor_cost,
        "costo_total_eur": total_cost,
    }


def simulate_m1(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    route_plan: OsrmRoutePlan,
    parameters: dict,
):
    """Simulate conventional-van home delivery from the logistics center."""

    model = parameters["FURGONETA_CONV"]
    distance_cost, labor_cost, route_cost = calculate_direct_route_operating_cost(
        distance_km=route_plan.total_distance_km,
        total_duration_min=route_plan.total_duration_min,
        route_count=route_plan.route_count,
        route_start_time_per_route_min=route_plan.route_start_time_per_route_min,
        cost_per_km=model["costo_km"],
        labor_cost_per_hour=model["costo_hora"],
    )
    co2_kg = (route_plan.total_distance_km * model["co2_km"]) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M1: Furgoneta Combustión desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=route_plan.total_distance_km,
        trip_count=route_plan.route_count,
        co2_kg=co2_kg,
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        facility_service_cost=0.0,
        total_cost=route_cost,
    )

def simulate_m2(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    route_plan: OsrmRoutePlan,
    parameters: dict,
):
    """Simulate electric-van home delivery from the logistics center."""

    model = parameters["FURGONETA_ELEC"]
    distance_cost, labor_cost, route_cost = calculate_direct_route_operating_cost(
        distance_km=route_plan.total_distance_km,
        total_duration_min=route_plan.total_duration_min,
        route_count=route_plan.route_count,
        route_start_time_per_route_min=route_plan.route_start_time_per_route_min,
        cost_per_km=model["costo_km"],
        labor_cost_per_hour=model["costo_hora"],
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M2: Furgoneta Eléctrica desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=route_plan.total_distance_km,
        trip_count=route_plan.route_count,
        co2_kg=0.0,
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        facility_service_cost=0.0,
        total_cost=route_cost,
    )

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
    )

    print(
        f"{facility_label} supply plan: {plan.route_count} truck routes, "
        f"{plan.total_distance_km:.2f} km, "
        f"{plan.total_duration_min:.2f} total min"
    )

    return plan, supply_visits


def simulate_m3(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    supply_plan: OsrmRoutePlan,
    bike_distance_km: float,
    bike_duration_min: float,
    bike_route_count: int,
    parameters: dict,
):
    """Simulate warehouse supply plus multi-microhub cargo-bike delivery."""

    van = parameters["FURGONETA_CONV"]
    bike = parameters["BICICLETA_CARGO"]

    supply_distance_cost, supply_labor_cost, supply_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=supply_plan.total_distance_km,
            total_duration_min=supply_plan.total_duration_min,
            route_count=supply_plan.route_count,
            route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
            cost_per_km=van["costo_km"],
            labor_cost_per_hour=van["costo_hora"],
        )
    )
    bike_distance_cost, bike_labor_cost, bike_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=bike_distance_km,
            total_duration_min=bike_duration_min,
            route_count=bike_route_count,
            route_start_time_per_route_min=BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
            cost_per_km=bike["costo_km"],
            labor_cost_per_hour=bike["costo_hora"],
        )
    )
    facility_service_cost = package_count * float(bike["comision_microhub"])
    supply_co2 = (supply_plan.total_distance_km * van["co2_km"]) / 1000

    route_distance_cost = supply_distance_cost + bike_distance_cost
    route_labor_cost = supply_labor_cost + bike_labor_cost
    total_cost = supply_route_cost + bike_route_cost + facility_service_cost

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M3: CC -> Microhubs -> Bicicleta",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=supply_plan.total_distance_km + bike_distance_km,
        trip_count=supply_plan.route_count + bike_route_count,
        co2_kg=supply_co2,
        route_distance_cost=route_distance_cost,
        route_labor_cost=route_labor_cost,
        facility_service_cost=facility_service_cost,
        total_cost=total_cost,
    )

def simulate_m4(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    supply_plan: OsrmRoutePlan,
    walking_distance_km: float,
    walking_duration_min: float,
    walking_route_count: int,
    parameters: dict,
):
    """Simulate multi-PUDO supply plus courier delivery on foot."""

    van = parameters["FURGONETA_CONV"]
    walking = parameters["PUDO_A_PIE"]

    supply_distance_cost, supply_labor_cost, supply_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=supply_plan.total_distance_km,
            total_duration_min=supply_plan.total_duration_min,
            route_count=supply_plan.route_count,
            route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
            cost_per_km=van["costo_km"],
            labor_cost_per_hour=van["costo_hora"],
        )
    )
    walking_distance_cost, walking_labor_cost, walking_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=walking_distance_km,
            total_duration_min=walking_duration_min,
            route_count=walking_route_count,
            route_start_time_per_route_min=WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
            cost_per_km=walking["costo_km"],
            labor_cost_per_hour=walking["costo_hora"],
        )
    )
    facility_service_cost = package_count * float(walking["comision_pudo"])

    route_distance_cost = supply_distance_cost + walking_distance_cost
    route_labor_cost = supply_labor_cost + walking_labor_cost
    total_cost = supply_route_cost + walking_route_cost + facility_service_cost
    supply_co2 = (supply_plan.total_distance_km * van["co2_km"]) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M4: CC -> PUDOs -> Entrega a pie",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=supply_plan.total_distance_km + walking_distance_km,
        trip_count=supply_plan.route_count + walking_route_count,
        co2_kg=supply_co2,
        route_distance_cost=route_distance_cost,
        route_labor_cost=route_labor_cost,
        facility_service_cost=facility_service_cost,
        total_cost=total_cost,
    )

def simulate_m5(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    customer_count: int,
    supply_plan: OsrmRoutePlan,
    customer_travel_km: float,
    parameters: dict,
):
    """Simulate multi-PUDO supply plus customer collection travel."""

    van = parameters["FURGONETA_CONV"]
    model = parameters["PUDO_CONSUMIDOR"]

    distance_cost, labor_cost, route_cost = calculate_direct_route_operating_cost(
        distance_km=supply_plan.total_distance_km,
        total_duration_min=supply_plan.total_duration_min,
        route_count=supply_plan.route_count,
        route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
        cost_per_km=van["costo_km"],
        labor_cost_per_hour=van["costo_hora"],
    )
    facility_service_cost = package_count * float(model["comision_pudo"])
    supply_co2 = (supply_plan.total_distance_km * van["co2_km"]) / 1000
    customer_co2 = (
        customer_travel_km * model["co2_km_estimado_cliente"]
    ) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M5: CC -> PUDOs -> Recogida Cliente",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=supply_plan.total_distance_km + customer_travel_km,
        trip_count=supply_plan.route_count + customer_count,
        co2_kg=supply_co2 + customer_co2,
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        facility_service_cost=facility_service_cost,
        total_cost=route_cost + facility_service_cost,
    )

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
    *,
    osrm_host: str,
    osrm_profile: str,
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
    )
    m2_plan = route_planner.build_capacity_plan(
        depot_latitude=direct_cc_lat,
        depot_longitude=direct_cc_lon,
        clients=demand_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
        client_demands=client_demands,
        route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
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
            f"M1 CWS driving: "
            f"{m1_plan.route_count} routes | "
            f"{m1_plan.total_distance_km:.2f} km | "
            f"total={m1_plan.total_duration_min:.1f} min | "
            f"max={m1_plan.max_route_duration_min:.1f} min"
        )
    print(
            f"M2 CWS driving: "
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
        ),
        simulate_m2(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=direct_cc,
            last_mile_point=demand_centroid,
            package_count=package_count,
            route_plan=m2_plan,
            parameters=parameters,
        ),
        simulate_m3(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=m3_cc,
            last_mile_point=microhub_point,
            package_count=package_count,
            supply_plan=m3_supply_plan,
            bike_distance_km=m3_bike_distance_km,
            bike_duration_min=m3_bike_duration_min,
            bike_route_count=m3_bike_route_count,
            parameters=parameters,
        ),
        simulate_m4(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=pudo_cc,
            last_mile_point=pudo_point,
            package_count=package_count,
            supply_plan=pudo_supply_plan,
            walking_distance_km=m4_distance_km,
            walking_duration_min=m4_duration_min,
            walking_route_count=m4_route_count,
            parameters=parameters,
        ),
        simulate_m5(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=pudo_cc,
            last_mile_point=pudo_point,
            package_count=package_count,
            customer_count=customer_count,
            supply_plan=pudo_supply_plan,
            customer_travel_km=customer_travel_km,
            parameters=parameters,
        ),
    ]

    return summary_results, model_details


def simulate_city(
    city: str,
    demand_scenario: str,
    instance_size: int,
    active_neighborhood: str | None = None,
    *,
    osrm_host: str,
    osrm_profile: str,
):
    centers, boundaries, parameters_df = load_city_data(city)
    demand_instance = load_demand_instance(city, demand_scenario, instance_size)
    classified_locations = load_classified_locations(city)
    microhubs, pudos = load_facility_candidates(
            classified_locations
        )

    print(
        f"Available facilities: "
        f"{len(microhubs)} microhubs, "
        f"{len(pudos)} PUDOs"
    )
    parameters = get_parameters(parameters_df)

    all_results = []
    all_model_details = {model: [] for model in ("M1", "M2", "M3", "M4", "M5")}
    if active_neighborhood is not None:
        boundaries = boundaries[
            boundaries["barrio"].str.lower() == active_neighborhood.lower()
        ]
        if boundaries.empty:
            raise ValueError(
                f"Neighborhood '{active_neighborhood}' was not found in {city}."
            )

    for _, neighborhood in boundaries.iterrows():
        neighborhood_name = neighborhood["barrio"]
        print(f"\n📍 Simulating {city.upper()} - {neighborhood_name}")

        demand_points = filter_points_by_neighborhood(demand_instance, neighborhood)
        if demand_points.empty:
            print(f"⚠️ No demand stops within {neighborhood_name}.")
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

        if neighborhood_microhubs.empty:
            neighborhood_microhubs = microhubs

        assigned_microhubs = assign_customers_to_nearest_facility(
            demand_points,
            neighborhood_microhubs,
        )
        
        print(
            f"Neighborhood microhubs: {len(neighborhood_microhubs)}, "
            f"used: {assigned_microhubs['assigned_facility'].nunique()}"
        )

        assigned_pudos = assign_customers_to_nearest_facility(
            demand_points,
            pudos,
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
            osrm_host=osrm_host,
            osrm_profile=osrm_profile,
        )
        
        assigned_pudos.groupby("assigned_facility").size().value_counts().sort_index()
        all_results.extend(results)
        for model_code, detail_rows in model_details.items():
            all_model_details[model_code].extend(detail_rows)
        print(
            f"✅ {neighborhood_name}: {len(demand_points)} customers, "
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
        "--neighborhood",
        default=None,
        help=(
            "Neighborhood to simulate. "
            "If omitted, all neighborhoods are processed."
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

    return parser.parse_args()

      
if __name__ == "__main__":
    args = parse_arguments()

    if args.city == "all" and args.neighborhood is not None:
        raise SystemExit(
            "Error: --neighborhood cannot be used together with --city all."
        )

    active_profile = args.profile
    active_neighborhood = args.neighborhood

    cities = CITIES if args.city == "all" else [args.city]

    RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)

    for active_city in cities:
        active_host = get_osrm_host(active_city, active_profile)

        print("\n" + "=" * 60)
        print("OSRM-BASED LOGISTICS SIMULATION")
        print("=" * 60)
        print(f"City: {active_city}")
        print(f"Neighborhood: {active_neighborhood}")
        print(f"OSRM host: {active_host}")
        print(f"OSRM profile: {active_profile}")
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
            active_neighborhood=active_neighborhood,
            osrm_host=active_host,
            osrm_profile=active_profile,
        )

        output_filename = (
            f"resultados_osrm_{active_city}_{args.demand_scenario}_{args.instance_size}.csv"
            if active_neighborhood is None
            else (
                f"resultados_osrm_"
                f"{active_city}_{active_neighborhood}.csv"
            )
        )

        output_path = RESULTS_FOLDER / output_filename

        results_df.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        detail_output_folder = (
            RESULTS_FOLDER
            / active_city
            / "simulation_details"
            / f"{args.demand_scenario}_{args.instance_size}"
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