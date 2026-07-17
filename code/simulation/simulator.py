import argparse
import numpy as np
import pandas as pd
from code.common.paths import DATA_DIR, RESULTS_DIR
np.random.seed(42)

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Run the GLIMS last-mile logistics simulation."
    )

    parser.add_argument(
        "--city",
        choices=["madrid", "barcelona", "valencia"],
        default=None,
        help="City to simulate. If omitted, all cities are simulated.",
    )
    parser.add_argument(
        "--neighborhood",
        default=None,
        help="Neighborhood to simulate. If omitted, all neighborhoods are simulated.",
    )

    return parser.parse_args()


def load_city_data(city: str):
    city_folder = DATA_DIR / city

    points = pd.read_csv(city_folder / "puntos_b2c.csv")
    centers = pd.read_csv(city_folder / "centros_cc.csv")
    boundaries = pd.read_csv(city_folder / "limites_barrios.csv")
    parameters = pd.read_csv(DATA_DIR / "parametros_modelos.csv")

    return points, centers, boundaries, parameters


def get_parameters(parameters: pd.DataFrame) -> dict:
    parameters = parameters.set_index("modelo")
    return parameters.to_dict(orient="index")


def calculate_haversine(lat1, lon1, lat2, lon2):
    R = 6371.0

    lat1, lon1, lat2, lon2 = map(
        np.radians,
        [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    )

    return R * (2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


def simulate_tsp_kilometers(start_lat, start_lon, route_points_df):
    remaining_points = route_points_df.copy()
    accumulated_km = 0.0
    current_position = (start_lat, start_lon)

    while len(remaining_points) > 0:
        distances = remaining_points.apply(
            lambda r: calculate_haversine(
                current_position[0],
                current_position[1],
                r["Latitude"],
                r["Longitude"],
            ),
            axis=1,
        )

        nearest_index = distances.idxmin()
        accumulated_km += distances.min()

        current_position = (
            remaining_points.loc[nearest_index, "Latitude"],
            remaining_points.loc[nearest_index, "Longitude"],
        )

        remaining_points = remaining_points.drop(nearest_index)

    accumulated_km += calculate_haversine(
        current_position[0],
        current_position[1],
        start_lat,
        start_lon,
    )

    return accumulated_km


def filter_neighborhood_points(points: pd.DataFrame, neighborhood: pd.Series):
    return points[
        (points["Latitude"].between(neighborhood["lat_min"], neighborhood["lat_max"]))
        & (points["Longitude"].between(neighborhood["lon_min"], neighborhood["lon_max"]))
    ].copy()


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

    neighborhood_center_lat = neighborhood_points["Latitude"].mean()
    neighborhood_center_lon = neighborhood_points["Longitude"].mean()

    centers = centers.copy()
    centers["dist_al_barrio"] = centers.apply(
        lambda c: calculate_haversine(
            neighborhood_center_lat,
            neighborhood_center_lon,
            c["Latitude"],
            c["Longitude"],
        ),
        axis=1,
    )

    selected_cc = centers.loc[centers["dist_al_barrio"].idxmin()]
    trunk_distance = selected_cc["dist_al_barrio"]

    internal_km = simulate_tsp_kilometers(
        neighborhood_center_lat,
        neighborhood_center_lon,
        neighborhood_points,
    )

    results = []

    # -------------------------------------------------------------------------
    # M1: Combustion van
    # -------------------------------------------------------------------------

    m1 = parameters["FURGONETA_CONV"]
    trips_1 = int(np.ceil(package_count / m1["capacidad"]))
    total_km_1 = (trunk_distance * 2 * trips_1) + internal_km

    cost_1 = (
        total_km_1 * m1["costo_km"]
        + (total_km_1 / m1["v_media"]) * m1["costo_hora"]
    )

    co2_1 = (total_km_1 * m1["co2_km"]) / 1000

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M1: Combustion Van from CC",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": total_km_1,
        "numero_viajes": trips_1,
        "emisiones_co2_kg": co2_1,
        "costo_total_eur": cost_1,
    })

    # -------------------------------------------------------------------------
    # M2: Electric van
    # -------------------------------------------------------------------------

    m2 = parameters["FURGONETA_ELEC"]
    trips_2 = int(np.ceil(package_count / m2["capacidad"]))
    total_km_2 = (trunk_distance * 2 * trips_2) + internal_km

    cost_2 = (
        total_km_2 * m2["costo_km"]
        + (total_km_2 / m2["v_media"]) * m2["costo_hora"]
    )

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M2: Electric Van from CC",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": total_km_2,
        "numero_viajes": trips_2,
        "emisiones_co2_kg": 0.0,
        "costo_total_eur": cost_2,
    })

    # -------------------------------------------------------------------------
    # M3: Microhub + cargo bike
    # -------------------------------------------------------------------------

    m3 = parameters["BICICLETA_CARGO"]
    bike_trips = int(np.ceil(package_count / m3["capacidad"]))

    hub_supply_km = trunk_distance * 2

    hub_truck_cost = (
        hub_supply_km * parameters["FURGONETA_CONV"]["costo_km"]
    )

    hub_truck_co2 = (
        hub_supply_km * parameters["FURGONETA_CONV"]["co2_km"]
    ) / 1000

    internal_bike_km = internal_km * 1.15

    bike_cost = (
        internal_bike_km * m3["costo_km"]
        + (internal_bike_km / m3["v_media"]) * m3["costo_hora"]
    )

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M3: CC -> Microhub -> Cargo Bike",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": hub_supply_km + internal_bike_km,
        "numero_viajes": 1 + bike_trips,
        "emisiones_co2_kg": hub_truck_co2,
        "costo_total_eur": hub_truck_cost + bike_cost + m3["fijo_hub_dia"],
    })

    # -------------------------------------------------------------------------
    # M4: PUDO + walking delivery
    # -------------------------------------------------------------------------

    m4 = parameters["PUDO_A_PIE"]
    walking_trips = int(np.ceil(package_count / m4["capacidad"]))

    courier_walking_km = (
        neighborhood_points.apply(
            lambda r: calculate_haversine(
                neighborhood_center_lat,
                neighborhood_center_lon,
                r["Latitude"],
                r["Longitude"],
            ),
            axis=1,
        ).sum()
        * 2
    )

    walking_cost = (
        courier_walking_km / m4["v_media"]
    ) * m4["costo_hora"]

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M4: CC -> PUDO -> Walking Delivery",
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

    # -------------------------------------------------------------------------
    # M5: PUDO + customer pickup
    # -------------------------------------------------------------------------

    m5 = parameters["PUDO_CONSUMIDOR"]

    customer_radial_km = courier_walking_km

    customer_co2 = (
        customer_radial_km * m5["co2_km_estimado_cliente"]
    ) / 1000

    results.append({
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": "M5: CC -> PUDO -> Customer Pickup",
        "centro_logistico": selected_cc["Location"],
        "paquetes": package_count,
        "distancia_troncal_km": trunk_distance,
        "km_recorridos": hub_supply_km + customer_radial_km,
        "numero_viajes": 1 + package_count,
        "emisiones_co2_kg": hub_truck_co2 + customer_co2,
        "costo_total_eur": (
            hub_truck_cost
            + package_count * m5["comision_pudo"]
        ),
    })

    return results


def simulate_city(city: str, active_neighborhood: str | None = None):
    points, centers, boundaries, parameters_df = load_city_data(city)
    parameters = get_parameters(parameters_df)

    all_results = []

    if active_neighborhood is not None:
        boundaries = boundaries[
            boundaries["barrio"].str.lower() == active_neighborhood.lower()
        ]

        if boundaries.empty:
            raise ValueError(f"Neighborhood '{active_neighborhood}' was not found in {city}.")

    for _, neighborhood in boundaries.iterrows():
        neighborhood_name = neighborhood["barrio"]

        print(f"\nSimulating {city.upper()} - {neighborhood_name}")

        neighborhood_points = filter_neighborhood_points(points, neighborhood)

        if len(neighborhood_points) == 0:
            print(f"There are no points within the boundaries of {neighborhood_name}.")
            continue

        neighborhood_results = simulate_neighborhood(
            city=city,
            neighborhood_name=neighborhood_name,
            neighborhood_points=neighborhood_points,
            centers=centers,
            parameters=parameters,
        )

        all_results.extend(neighborhood_results)

        print(f"{neighborhood_name}: {len(neighborhood_points)} packages simulated")

    return pd.DataFrame(all_results)


# =============================================================================
# 7. EXECUTION
# =============================================================================

if __name__ == "__main__":
    args = parse_arguments()

    cities = (
        [args.city.lower()]
        if args.city is not None
        else ["madrid", "barcelona", "valencia"]
    )

    neighborhood = (
        args.neighborhood.lower()
        if args.neighborhood is not None
        else None
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_results = []

    for city in cities:
        print(f"\n{'=' * 60}")
        print(f"SIMULATING {city.upper()}")
        print(f"{'=' * 60}")

        city_results = simulate_city(
            city=city,
            active_neighborhood=neighborhood,
        )

        if not city_results.empty:
            all_results.append(city_results)

    if not all_results:
        print("\nNo results were generated.")
    else:
        results_df = pd.concat(all_results, ignore_index=True)
        results_df = results_df.round(2)

        print("\nRESULTS")
        print(results_df.to_string(index=False))

        if len(cities) > 1:
            output_filename = "resultados.csv"
        elif neighborhood is None:
            output_filename = f"resultados_{cities[0]}.csv"
        else:
            output_filename = f"resultados_{cities[0]}_{neighborhood}.csv"

        output_path = RESULTS_DIR / output_filename

        results_df.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"\nResults saved to: {output_path.resolve()}")