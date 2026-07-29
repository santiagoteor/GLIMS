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

from code.common.paths import PROJECT_ROOT
from code.simulation.simulator import (
    load_city_data,
    get_parameters,
    filter_neighborhood_points,
    calculate_haversine,
)

from code.simulation.operational_points import (
    OperationalPoint,
    select_operational_point,
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

OSRM_PROBE_COORDS = {
    "madrid": [(-3.703790, 40.416775), (-3.693790, 40.426775)],
    "barcelona": [(2.173404, 41.385064), (2.183404, 41.395064)],
    "valencia": [(-0.376288, 39.469907), (-0.366288, 39.479907)],
}


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


def osrm_distance_duration_table(
    coords,
    *,
    host: str,
    profile: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return OSRM distance (km) and duration (min) matrices."""

    url = f"{host}/table/v1/{profile}/{_format_coords(coords)}"
    params = {"annotations": "distance,duration"}

    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM /table error: {payload.get('code')} - "
            f"{payload.get('message', '')}"
        )

    distance_matrix = np.array(payload["distances"], dtype=float) / 1000.0
    duration_matrix = np.array(payload["durations"], dtype=float) / 60.0

    return distance_matrix, duration_matrix


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


def get_neighborhood_matrix(
    city: str,
    last_mile_lat: float,
    last_mile_lon: float,
    neighborhood_points: pd.DataFrame,
    transport_mode: str,
):
    """
    Build the distance matrix (last-mile point + clients) for one transport
    mode. Index 0 is the operational point; indices 1..n follow the row order
    of neighborhood_points.
    """

    coords = [(last_mile_lon, last_mile_lat)] + list(
        zip(neighborhood_points["Longitude"], neighborhood_points["Latitude"])
    )

    print(
        f"\nQuerying OSRM /table for {len(coords)} points "
        f"using {transport_mode}..."
    )

    osrm_host = get_osrm_host(city, transport_mode)
    distance_matrix, _duration_matrix = osrm_distance_duration_table(
        coords,
        host=osrm_host,
        profile=transport_mode,
    )

    print(
        "Distance and duration matrices received from OSRM "
        f"for {transport_mode}."
    )

    return distance_matrix


def nearest_neighbor_tsp(matrix, n_clients: int):
    pending = list(range(1, n_clients + 1))
    current = 0
    km = 0.0

    while pending:
        next_idx = min(pending, key=lambda i: matrix[current, i])
        km += matrix[current, next_idx]
        pending.remove(next_idx)
        current = next_idx

    km += matrix[current, 0]

    return km

def clarke_wright_savings(
    matrix: np.ndarray,
    n_clients: int,
    vehicle_capacity: float,
    client_demands=None,
):
    """
    Build capacity-feasible delivery routes using parallel Clarke-Wright
    Savings.

    Matrix index 0 is the depot and client indices are 1..n_clients.
    ``client_demands`` may contain one demand value per client. When omitted,
    each client represents one package with demand 1.
    """

    if n_clients <= 0:
        return 0.0, []

    vehicle_capacity = float(vehicle_capacity)

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

def calculate_radial_distance(matrix, n_clients: int):
    """Sum independent depot-client-depot trips on an asymmetric matrix."""

    return sum(
        matrix[0, i] + matrix[i, 0]
        for i in range(1, n_clients + 1)
    )


@dataclass(frozen=True)
class OsrmRoutePlan:
    """Capacity-aware route plan calculated over an OSRM matrix."""

    transport_mode: str
    vehicle_capacity: float
    routes: list[list[int]]
    total_distance_km: float
    total_duration_min: float

    @property
    def route_count(self) -> int:
        return len(self.routes)


class CapacityAwareOsrmRouter:
    """
    Generic OSRM-backed planner for driving, cycling, and walking routes.

    OSRM supplies mode-specific distance and duration matrices. Clarke-Wright
    then creates routes subject to the supplied vehicle or courier capacity.
    """

    def __init__(self, city: str):
        self.city = city

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

        osrm_host = get_osrm_host(self.city, transport_mode)
        distance_matrix, duration_matrix = osrm_distance_duration_table(
            coords,
            host=osrm_host,
            profile=transport_mode,
        )

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
        )
        total_duration_min = calculate_routes_matrix_cost(
            duration_matrix,
            routes,
        )

        return OsrmRoutePlan(
            transport_mode=transport_mode,
            vehicle_capacity=float(vehicle_capacity),
            routes=routes,
            total_distance_km=total_distance_km,
            total_duration_min=total_duration_min,
        )

    def build_independent_round_trips(
        self,
        *,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
    ) -> tuple[float, float]:
        distance_matrix, duration_matrix = self.get_matrices(
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            clients=clients,
            transport_mode=transport_mode,
        )

        client_count = len(clients)

        return (
            calculate_radial_distance(distance_matrix, client_count),
            calculate_radial_distance(duration_matrix, client_count),
        )

def calculate_haversine_radial_distance(
    last_mile_lat: float,
    last_mile_lon: float,
    neighborhood_points: pd.DataFrame,
):
    one_way_km = sum(
        calculate_haversine(
            last_mile_lat,
            last_mile_lon,
            point["Latitude"],
            point["Longitude"],
        )
        for _, point in neighborhood_points.iterrows()
    )

    return one_way_km * 2


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
        "costo_total_eur": total_cost,
    }


def simulate_m1(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    round_trip_trunk_distance: float,
    internal_km: float,
    parameters: dict,
):
    """Simulate conventional-van home delivery from the logistics center."""

    model = parameters["FURGONETA_CONV"]
    trip_count = int(np.ceil(package_count / model["capacidad"]))
    total_km = round_trip_trunk_distance * trip_count + internal_km
    total_cost = (
        total_km * model["costo_km"]
        + (total_km / model["v_media"]) * model["costo_hora"]
    )
    co2_kg = (total_km * model["co2_km"]) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M1: Furgoneta Combustión desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=outbound_trunk_distance,
        return_trunk_distance=return_trunk_distance,
        total_km=total_km,
        trip_count=trip_count,
        co2_kg=co2_kg,
        total_cost=total_cost,
    )


def simulate_m2(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    round_trip_trunk_distance: float,
    internal_km: float,
    parameters: dict,
):
    """Simulate electric-van home delivery from the logistics center."""

    model = parameters["FURGONETA_ELEC"]
    trip_count = int(np.ceil(package_count / model["capacidad"]))
    total_km = round_trip_trunk_distance * trip_count + internal_km
    total_cost = (
        total_km * model["costo_km"]
        + (total_km / model["v_media"]) * model["costo_hora"]
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M2: Furgoneta Eléctrica desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=outbound_trunk_distance,
        return_trunk_distance=return_trunk_distance,
        total_km=total_km,
        trip_count=trip_count,
        co2_kg=0.0,
        total_cost=total_cost,
    )


def _calculate_hub_supply_metrics(
    round_trip_trunk_distance: float,
    parameters: dict,
):
    """Calculate conventional-van supply distance, cost, and emissions."""

    van = parameters["FURGONETA_CONV"]
    supply_cost = round_trip_trunk_distance * van["costo_km"]
    supply_co2 = (round_trip_trunk_distance * van["co2_km"]) / 1000

    return round_trip_trunk_distance, supply_cost, supply_co2


def simulate_m3(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    round_trip_trunk_distance: float,
    internal_km: float,
    parameters: dict,
):
    """Simulate logistics-center supply plus cargo-bike delivery."""

    model = parameters["BICICLETA_CARGO"]
    bike_trip_count = int(np.ceil(package_count / model["capacidad"]))
    supply_km, supply_cost, supply_co2 = _calculate_hub_supply_metrics(
        round_trip_trunk_distance,
        parameters,
    )

    internal_bike_km = internal_km
    bike_cost = (
        internal_bike_km * model["costo_km"]
        + (internal_bike_km / model["v_media"]) * model["costo_hora"]
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M3: CC -> Microhub -> Bicicleta",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=outbound_trunk_distance,
        return_trunk_distance=return_trunk_distance,
        total_km=supply_km + internal_bike_km,
        trip_count=1 + bike_trip_count,
        co2_kg=supply_co2,
        total_cost=supply_cost + bike_cost + model["fijo_hub_dia"],
    )


def simulate_m4(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    round_trip_trunk_distance: float,
    courier_walking_km: float,
    parameters: dict,
):
    """Simulate PUDO supply plus courier delivery on foot."""

    model = parameters["PUDO_A_PIE"]
    walking_trip_count = int(np.ceil(package_count / model["capacidad"]))
    supply_km, supply_cost, supply_co2 = _calculate_hub_supply_metrics(
        round_trip_trunk_distance,
        parameters,
    )
    walking_cost = (
        courier_walking_km / model["v_media"]
    ) * model["costo_hora"]

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M4: CC -> PUDO -> Entrega a pie",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=outbound_trunk_distance,
        return_trunk_distance=return_trunk_distance,
        total_km=supply_km + courier_walking_km,
        trip_count=1 + walking_trip_count,
        co2_kg=supply_co2,
        total_cost=(
            supply_cost
            + walking_cost
            + package_count * model["comision_pudo"]
        ),
    )


def simulate_m5(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    round_trip_trunk_distance: float,
    customer_travel_km: float,
    parameters: dict,
):
    """Simulate PUDO supply plus customer collection travel."""

    model = parameters["PUDO_CONSUMIDOR"]
    supply_km, supply_cost, supply_co2 = _calculate_hub_supply_metrics(
        round_trip_trunk_distance,
        parameters,
    )
    customer_co2 = (
        customer_travel_km * model["co2_km_estimado_cliente"]
    ) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M5: CC -> PUDO -> Recogida Cliente",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=outbound_trunk_distance,
        return_trunk_distance=return_trunk_distance,
        total_km=supply_km + customer_travel_km,
        trip_count=1 + package_count,
        co2_kg=supply_co2 + customer_co2,
        total_cost=supply_cost + package_count * model["comision_pudo"],
    )


def simulate_neighborhood(
    city: str,
    neighborhood_name: str,
    neighborhood_points: pd.DataFrame,
    last_mile_point: OperationalPoint,
    microhub_point: OperationalPoint,
    pudo_point: OperationalPoint,
    centers: pd.DataFrame,
    parameters: dict,
    *,
    osrm_host: str,
    osrm_profile: str,
):
    """Prepare mode-specific route metrics and run all five models."""

    package_count = len(neighborhood_points)

    if package_count == 0:
        return []

    last_mile_lat = last_mile_point.latitude
    last_mile_lon = last_mile_point.longitude

    print(
        f"Last-mile point: {last_mile_point.name} "
        f"({last_mile_lat}, {last_mile_lon})"
    )

    # M1/M2 trunk and internal delivery both use the driving network.
    selected_cc = select_logistics_center(
        centers,
        last_mile_lat,
        last_mile_lon,
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )

    outbound_trunk_distance = selected_cc["distancia_troncal_ida_km"]
    return_trunk_distance = selected_cc["distancia_troncal_regreso_km"]
    round_trip_trunk_distance = selected_cc["distancia_troncal_total_km"]

    print(f"Selected logistics center: {selected_cc['Location']}")
    print(f"CC -> neighborhood: {outbound_trunk_distance:.2f} km")
    print(f"Neighborhood -> CC: {return_trunk_distance:.2f} km")
    print(f"Round-trip trunk distance: {round_trip_trunk_distance:.2f} km")

    route_planner = CapacityAwareOsrmRouter(city)

    m1_plan = route_planner.build_capacity_plan(
        depot_latitude=last_mile_lat,
        depot_longitude=last_mile_lon,
        clients=neighborhood_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_CONV"]["capacidad"],
    )
    m2_plan = route_planner.build_capacity_plan(
        depot_latitude=last_mile_lat,
        depot_longitude=last_mile_lon,
        clients=neighborhood_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
    )

    print(
        f"M1 CWS driving: {m1_plan.route_count} routes, "
        f"{m1_plan.total_distance_km:.2f} internal km, "
        f"{m1_plan.total_duration_min:.2f} OSRM min"
    )
    print(
        f"M2 CWS driving: {m2_plan.route_count} routes, "
        f"{m2_plan.total_distance_km:.2f} internal km, "
        f"{m2_plan.total_duration_min:.2f} OSRM min"
    )

    # M3: the supply leg is still by van (driving), while only the delivery
    # routes from the microhub to customers use the cycling profile.
    print(
        f"M3 microhub: {microhub_point.name} "
        f"({microhub_point.latitude}, {microhub_point.longitude})"
    )

    microhub_cc = select_logistics_center(
        centers,
        microhub_point.latitude,
        microhub_point.longitude,
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    microhub_outbound_distance = microhub_cc["distancia_troncal_ida_km"]
    microhub_return_distance = microhub_cc["distancia_troncal_regreso_km"]
    microhub_round_trip_distance = microhub_cc["distancia_troncal_total_km"]

    m3_plan = route_planner.build_capacity_plan(
        depot_latitude=microhub_point.latitude,
        depot_longitude=microhub_point.longitude,
        clients=neighborhood_points,
        transport_mode="cycling",
        vehicle_capacity=parameters["BICICLETA_CARGO"]["capacidad"],
    )

    print(
        f"M3 CWS cycling: {m3_plan.route_count} routes, "
        f"{m3_plan.total_distance_km:.2f} internal km, "
        f"{m3_plan.total_duration_min:.2f} OSRM min"
    )

    # M4/M5: the CC-PUDO supply leg remains driving. Walking applies only
    # between the PUDO and customers.
    print(
        f"M4/M5 PUDO: {pudo_point.name} "
        f"({pudo_point.latitude}, {pudo_point.longitude})"
    )

    pudo_cc = select_logistics_center(
        centers,
        pudo_point.latitude,
        pudo_point.longitude,
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )

    pudo_outbound_distance = pudo_cc["distancia_troncal_ida_km"]
    pudo_return_distance = pudo_cc["distancia_troncal_regreso_km"]
    pudo_round_trip_distance = pudo_cc["distancia_troncal_total_km"]

    m4_plan = route_planner.build_capacity_plan(
        depot_latitude=pudo_point.latitude,
        depot_longitude=pudo_point.longitude,
        clients=neighborhood_points,
        transport_mode="walking",
        vehicle_capacity=parameters["PUDO_A_PIE"]["capacidad"],
    )
    customer_travel_km, customer_travel_min = (
        route_planner.build_independent_round_trips(
            depot_latitude=pudo_point.latitude,
            depot_longitude=pudo_point.longitude,
            clients=neighborhood_points,
            transport_mode="walking",
        )
    )

    print(
        f"M4 CWS walking: {m4_plan.route_count} routes, "
        f"{m4_plan.total_distance_km:.2f} internal km, "
        f"{m4_plan.total_duration_min:.2f} OSRM min"
    )
    print(
        f"M5 independent customer walking trips: "
        f"{customer_travel_km:.2f} km, "
        f"{customer_travel_min:.2f} OSRM min"
    )

    shared_arguments = {
        "city": city,
        "neighborhood_name": neighborhood_name,
        "selected_cc": selected_cc,
        "last_mile_point": last_mile_point,
        "package_count": package_count,
        "outbound_trunk_distance": outbound_trunk_distance,
        "return_trunk_distance": return_trunk_distance,
        "round_trip_trunk_distance": round_trip_trunk_distance,
        "parameters": parameters,
    }

    return [
        simulate_m1(
            **shared_arguments,
            internal_km=m1_plan.total_distance_km,
        ),
        simulate_m2(
            **shared_arguments,
            internal_km=m2_plan.total_distance_km,
        ),
        simulate_m3(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=microhub_cc,
            last_mile_point=microhub_point,
            package_count=package_count,
            outbound_trunk_distance=microhub_outbound_distance,
            return_trunk_distance=microhub_return_distance,
            round_trip_trunk_distance=microhub_round_trip_distance,
            internal_km=m3_plan.total_distance_km,
            parameters=parameters,
        ),
        simulate_m4(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=pudo_cc,
            last_mile_point=pudo_point,
            package_count=package_count,
            outbound_trunk_distance=pudo_outbound_distance,
            return_trunk_distance=pudo_return_distance,
            round_trip_trunk_distance=pudo_round_trip_distance,
            courier_walking_km=m4_plan.total_distance_km,
            parameters=parameters,
        ),
        simulate_m5(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=pudo_cc,
            last_mile_point=pudo_point,
            package_count=package_count,
            outbound_trunk_distance=pudo_outbound_distance,
            return_trunk_distance=pudo_return_distance,
            round_trip_trunk_distance=pudo_round_trip_distance,
            customer_travel_km=customer_travel_km,
            parameters=parameters,
        ),
    ]

def simulate_city(
    city: str,
    active_neighborhood: str | None = None,
    *,
    osrm_host: str,
    osrm_profile: str,
):

    points, centers, boundaries, parameters_df = load_city_data(city)
    classified_locations = load_classified_locations(city)
    parameters = get_parameters(parameters_df)

    all_results = []

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

        neighborhood_points = filter_neighborhood_points(points, neighborhood)

        if neighborhood_points.empty:
            print(f"⚠️ There are no points within the boundaries of {neighborhood_name}.")
            continue
        
        last_mile_point = select_operational_point(
            strategy="centroid",
            neighborhood_records=neighborhood_points,
        )

        microhub_point = select_operational_point(
            strategy="nearest_microhub_facility",
            neighborhood_records=neighborhood_points,
            classified_records=classified_locations,
            target_latitude=last_mile_point.latitude,
            target_longitude=last_mile_point.longitude,
        )
        
        pudo_point = select_operational_point(
            strategy="nearest_pudo_facility",
            neighborhood_records=neighborhood_points,
            classified_records=classified_locations,
            target_latitude=last_mile_point.latitude,
            target_longitude=last_mile_point.longitude,
        )

        results = simulate_neighborhood(
            city=city,
            neighborhood_name=neighborhood_name,
            neighborhood_points=neighborhood_points,
            last_mile_point=last_mile_point,
            microhub_point=microhub_point,
            pudo_point=pudo_point,
            centers=centers,
            parameters=parameters,
            osrm_host=osrm_host,
            osrm_profile=osrm_profile,
        )

        all_results.extend(results)

        print(f"✅ {neighborhood_name}: {len(neighborhood_points)} packages simulated")

    return pd.DataFrame(all_results)

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

        results_df = simulate_city(
            city=active_city,
            active_neighborhood=active_neighborhood,
            osrm_host=active_host,
            osrm_profile=active_profile,
        )

        output_filename = (
            f"resultados_osrm_{active_city}.csv"
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

        print(f"\nResults saved to: {output_path.resolve()}")