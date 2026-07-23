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

OSRM_HOST = "http://localhost:5000"
OSRM_PROFILE = "driving"

BASE_DIR = PROJECT_ROOT

ACTIVE_CITY = "madrid"              # "madrid", "barcelona", or "valencia"
ACTIVE_NEIGHBORHOOD = "Moratalaz"   # None = all neighborhoods / "Moratalaz" = one neighborhood only
RESULTS_FOLDER = BASE_DIR / "results"


def check_osrm_server(host: str = OSRM_HOST, profile: str = OSRM_PROFILE):
    """
    Fail fast with a clear message if no OSRM server is reachable,
    instead of letting every subsequent request time out one by one.
    """

    probe_url = f"{host}/table/v1/{profile}/13.388860,52.517037;13.397634,52.529407"

    try:
        response = requests.get(probe_url, timeout=5)
        response.raise_for_status()

    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Could not reach OSRM server at {host}. "
            "Make sure osrm-routed is running (see module docstring for setup). "
            f"Original error: {exc}"
        ) from exc


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
    neighborhood_lat: float,
    neighborhood_lon: float,
):
    """
    Select the logistics center closest to the neighborhood centroid
    using real driving distances from OSRM.
    """

    centers = centers.copy()

    coords = [(neighborhood_lon, neighborhood_lat)] + list(
        zip(centers["Longitude"], centers["Latitude"])
    )

    n_centers = len(centers)

    distances_km = osrm_table(
        coords,
        sources=[0],
        destinations=list(range(1, n_centers + 1)),
    )[0]

    centers["distancia_km"] = distances_km

    return centers.loc[centers["distancia_km"].idxmin()]


def get_neighborhood_matrix(
    centroid_lat: float,
    centroid_lon: float,
    neighborhood_points: pd.DataFrame,
):
    """
    Build the full distance matrix (centroid + clients) for the neighborhood
    in a single OSRM /table call. Index 0 is the centroid; indices 1..n
    follow the row order of neighborhood_points.
    """

    coords = [(centroid_lon, centroid_lat)] + list(
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


def simulate_neighborhood(
    city: str,
    neighborhood_name: str,
    neighborhood_points: pd.DataFrame,
    centers: pd.DataFrame,
    parameters: dict,
):

    package_count = len(neighborhood_points)

    if package_count == 0:
        return []

    # --------------------------------------------------
    # Neighborhood preparation
    # --------------------------------------------------

    centroid_lat = neighborhood_points["Latitude"].mean()
    centroid_lon = neighborhood_points["Longitude"].mean()

    print("Centroid:", centroid_lat, centroid_lon)

    # --------------------------------------------------
    # Logistics center selection + trunk distance
    # --------------------------------------------------

    selected_cc = select_logistics_center(
        centers,
        centroid_lat,
        centroid_lon,
    )

    trunk_distance = selected_cc["distancia_km"]

    print(f"Selected logistics center: {selected_cc['Location']}")
    print(f"Road distance from CC to neighborhood (OSRM): {trunk_distance:.2f} km")

    # --------------------------------------------------
    # Distance matrix construction
    # --------------------------------------------------

    drive_matrix = get_neighborhood_matrix(
        centroid_lat,
        centroid_lon,
        neighborhood_points,
    )

    # --------------------------------------------------
    # Internal kilometers (TSP) + radial distance (PUDO/walking)
    # --------------------------------------------------

    internal_km = nearest_neighbor_tsp(drive_matrix, package_count)
    courier_walking_km = calculate_radial_distance(drive_matrix, package_count)

    first_point = neighborhood_points.iloc[0]

    print(
        "OSRM:", drive_matrix[0, 1],
        "HAV:", calculate_haversine(
            centroid_lat, centroid_lon,
            first_point["Latitude"], first_point["Longitude"],
        ),
    )

    results = []

    # ==================================================
    # M1
    # ==================================================

    m1 = parameters["FURGONETA_CONV"]

    trips_1 = int(np.ceil(package_count / m1["capacidad"]))

    total_km_1 = trunk_distance * 2 * trips_1 + internal_km

    cost_1 = (
        total_km_1 * m1["costo_km"]
        + (total_km_1 / m1["v_media"]) * m1["costo_hora"]
    )

    co2_1 = (total_km_1 * m1["co2_km"]) / 1000

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M1: Furgoneta Combustión desde CC",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": total_km_1,
        "numero_viajes": trips_1,
        "emisiones_co2_kg": co2_1,
        "costo_total_eur": cost_1,
    })

    # ==================================================
    # M2
    # ==================================================

    m2 = parameters["FURGONETA_ELEC"]

    trips_2 = int(np.ceil(package_count / m2["capacidad"]))

    total_km_2 = trunk_distance * 2 * trips_2 + internal_km

    cost_2 = (
        total_km_2 * m2["costo_km"]
        + (total_km_2 / m2["v_media"]) * m2["costo_hora"]
    )

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M2: Furgoneta Eléctrica desde CC",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": total_km_2,
        "numero_viajes": trips_2,
        "emisiones_co2_kg": 0.0,
        "costo_total_eur": cost_2,
    })

    # ==================================================
    # M3
    # ==================================================

    m3 = parameters["BICICLETA_CARGO"]

    bike_trips = int(np.ceil(package_count / m3["capacidad"]))

    hub_supply_km = trunk_distance * 2

    hub_truck_cost = hub_supply_km * parameters["FURGONETA_CONV"]["costo_km"]
    hub_truck_co2 = (hub_supply_km * parameters["FURGONETA_CONV"]["co2_km"]) / 1000

    internal_bike_km = internal_km * 1.15

    bike_cost = (
        internal_bike_km * m3["costo_km"]
        + (internal_bike_km / m3["v_media"]) * m3["costo_hora"]
    )

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M3: CC -> Microhub -> Bicicleta",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": hub_supply_km + internal_bike_km,
        "numero_viajes": 1 + bike_trips,
        "emisiones_co2_kg": hub_truck_co2,
        "costo_total_eur": hub_truck_cost + bike_cost + m3["fijo_hub_dia"],
    })

    # ==================================================
    # M4
    # ==================================================

    m4 = parameters["PUDO_A_PIE"]

    walking_trips = int(np.ceil(package_count / m4["capacidad"]))

    walking_cost = (courier_walking_km / m4["v_media"]) * m4["costo_hora"]

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M4: CC -> PUDO -> Entrega a pie",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": hub_supply_km + courier_walking_km,
        "numero_viajes": 1 + walking_trips,
        "emisiones_co2_kg": hub_truck_co2,
        "costo_total_eur": (
            hub_truck_cost
            + walking_cost
            + package_count * m4["comision_pudo"]
        ),
    })

    # ==================================================
    # M5
    # ==================================================

    m5 = parameters["PUDO_CONSUMIDOR"]

    customer_co2 = (courier_walking_km * m5["co2_km_estimado_cliente"]) / 1000

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M5: CC -> PUDO -> Recogida Cliente",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": hub_supply_km + courier_walking_km,
        "numero_viajes": 1 + package_count,
        "emisiones_co2_kg": hub_truck_co2 + customer_co2,
        "costo_total_eur": (
            hub_truck_cost
            + package_count * m5["comision_pudo"]
        ),
    })

    return results


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

        results = simulate_neighborhood(
            city=city,
            neighborhood_name=neighborhood_name,
            neighborhood_points=neighborhood_points,
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