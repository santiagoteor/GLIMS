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


OSRM_HOST = "http://localhost:5000"
OSRM_PROFILE = "driving"

BASE_DIR = PROJECT_ROOT

ACTIVE_CITY = "madrid"              # "madrid", "barcelona", or "valencia"
ACTIVE_NEIGHBORHOOD = "El Pardo"   # None = all neighborhoods / "Moratalaz" = one neighborhood only
RESULTS_FOLDER = BASE_DIR / "results"


def check_osrm_server(host: str = OSRM_HOST, profile: str = OSRM_PROFILE):
    """
    Fail fast with a clear message if no OSRM server is reachable.
    """

    probe_url = (
        f"{host}/table/v1/{profile}/"
        "-3.703790,40.416775;-3.693790,40.426775"
    )

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


def osrm_table(coords, sources=None, destinations=None, host=OSRM_HOST, profile=OSRM_PROFILE):
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


def select_logistics_center(
    centers: pd.DataFrame,
    last_mile_lat: float,
    last_mile_lon: float,
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

    distance_matrix = osrm_table(coords)
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
    last_mile_lat: float,
    last_mile_lon: float,
    neighborhood_points: pd.DataFrame,
):
    """
    Build the full distance matrix (last-mile point + clients) for the
    neighborhood in a single OSRM /table call. Index 0 is the last-mile point; indices 1..n
    follow the row order of neighborhood_points.
    """

    coords = [(last_mile_lon, last_mile_lat)] + list(
        zip(neighborhood_points["Longitude"], neighborhood_points["Latitude"])
    )

    print(f"\nQuerying OSRM /table for {len(coords)} points...")

    matrix = osrm_table(coords)

    print("Distance matrix received from OSRM.")

    return matrix


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


def calculate_radial_distance(matrix, n_clients: int):
    return sum(matrix[0, i] for i in range(1, n_clients + 1)) * 2

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

    internal_bike_km = internal_km * 1.15
    bike_cost = (
        internal_bike_km * model["costo_km"]
        + (internal_bike_km / model["v_media"]) * model["costo_hora"]
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M3: CC -> Microhub -> Bicicleta",
        selected_cc=selected_cc,
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
    centers: pd.DataFrame,
    parameters: dict,
):
    """Prepare shared route metrics and run all five logistics models."""

    package_count = len(neighborhood_points)

    if package_count == 0:
        return []

    last_mile_lat = last_mile_point.latitude
    last_mile_lon = last_mile_point.longitude

    print(
        f"Last-mile point: {last_mile_point.name} "
        f"({last_mile_lat}, {last_mile_lon})"
    )

    selected_cc = select_logistics_center(
        centers,
        last_mile_lat,
        last_mile_lon,
    )

    outbound_trunk_distance = selected_cc["distancia_troncal_ida_km"]
    return_trunk_distance = selected_cc["distancia_troncal_regreso_km"]
    round_trip_trunk_distance = selected_cc["distancia_troncal_total_km"]

    print(f"Selected logistics center: {selected_cc['Location']}")
    print(f"CC -> neighborhood: {outbound_trunk_distance:.2f} km")
    print(f"Neighborhood -> CC: {return_trunk_distance:.2f} km")
    print(f"Round-trip trunk distance: {round_trip_trunk_distance:.2f} km")

    drive_matrix = get_neighborhood_matrix(
        last_mile_lat,
        last_mile_lon,
        neighborhood_points,
    )

    internal_km = nearest_neighbor_tsp(drive_matrix, package_count)
    radial_km = calculate_haversine_radial_distance(
        last_mile_lat,
        last_mile_lon,
        neighborhood_points,
    )

    shared_arguments = {
        "city": city,
        "neighborhood_name": neighborhood_name,
        "selected_cc": selected_cc,
        "package_count": package_count,
        "outbound_trunk_distance": outbound_trunk_distance,
        "return_trunk_distance": return_trunk_distance,
        "round_trip_trunk_distance": round_trip_trunk_distance,
        "parameters": parameters,
    }

    return [
        simulate_m1(
            **shared_arguments,
            internal_km=internal_km,
        ),
        simulate_m2(
            **shared_arguments,
            internal_km=internal_km,
        ),
        simulate_m3(
            **shared_arguments,
            internal_km=internal_km,
        ),
        simulate_m4(
            **shared_arguments,
            courier_walking_km=radial_km,
        ),
        simulate_m5(
            **shared_arguments,
            customer_travel_km=radial_km,
        ),
    ]

def simulate_city(
    city: str,
    active_neighborhood: str | None = None
):

    points, centers, boundaries, parameters_df = load_city_data(city)
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

        results = simulate_neighborhood(
            city=city,
            neighborhood_name=neighborhood_name,
            neighborhood_points=neighborhood_points,
            last_mile_point=last_mile_point,
            centers=centers,
            parameters=parameters,
        )

        all_results.extend(results)

        print(f"✅ {neighborhood_name}: {len(neighborhood_points)} packages simulated")

    return pd.DataFrame(all_results)


# =============================================================================
# 7. EXECUTION
# =============================================================================

if __name__ == "__main__":

    print("=" * 60)
    print("OSRM-BASED LOGISTICS SIMULATION")
    print("=" * 60)
    print(f"City: {ACTIVE_CITY}")
    print(f"Neighborhood: {ACTIVE_NEIGHBORHOOD}")
    print(f"OSRM host: {OSRM_HOST}")
    print(f"OSRM profile: {OSRM_PROFILE}")
    print("=" * 60)

    check_osrm_server()

    RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)

    print("Loading data...")

    results_df = simulate_city(
        city=ACTIVE_CITY,
        active_neighborhood=ACTIVE_NEIGHBORHOOD,
    )

    if results_df.empty:
        print("\nNo results were generated.")

    else:

        results_df = results_df.round(2)

        print("\nRESULTS")
        print(results_df.to_string(index=False))

        output_filename = (
            f"resultados_osrm_{ACTIVE_CITY}.csv"
            if ACTIVE_NEIGHBORHOOD is None
            else f"resultados_osrm_{ACTIVE_CITY}_{ACTIVE_NEIGHBORHOOD}.csv"
        )

        output_path = RESULTS_FOLDER / output_filename

        results_df.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"\nResults saved to: {output_path.resolve()}")