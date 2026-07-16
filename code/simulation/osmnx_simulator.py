
# PROBLEM: The logic is not encapsulated in a reusable and testable manner.
# TODO: Refactor the code to create a modular simulator with single-responsibility functions that can be easily tested and reused.

# PROBLEM: The conclusions drawn from the simulation results depend on parameters that are not well-documented or validated.
# TODO: Conduct a sensitivity analysis on key parameters such as fuel cost, CO2 emission factors, vehicle capacity, PUDO commission, and hub costs to assess the robustness of the conclusions.

# PROBLEM: It is not contrasted the reliability of OSM distances with an independent source.
# TODO: Empirically validate the OSM distances by comparing them with real-world measurements or alternative mapping services (e.g., Google Maps, HERE Maps) to ensure accuracy and reliability of the simulation results.

# PROBLEM: The memory requests 10 indicators broken down (km by mode, trips by mode, emissions, cost); the code only aggregates some of them.
# TODO: Report the 10 indicators for each neighborhood and city, including total kilometers traveled by mode, number of trips by mode, CO2 emissions, and total cost. 
# Ensure that the simulation outputs are comprehensive and meet the requirements of the memory.

# PROBLEM: It doesn't cross results with the density of demand/population in the neighborhood.
# TODO: Analyze how cost and emissions vary according to density (e.g., Eixample dense vs. El Pardo dispersed) by incorporating population and demand density data into the simulation and comparing the results across different neighborhoods.

# PROBLEM: only 5 base references; no structured review.
# TODO: A systematic review (PRISMA, WoS/Scopus) on microhubs, PUDO, cargo-bike and LRP in the last mile.

# PROBLEM: missing LRP formalization, objective function and algorithms; today the code does not implement the described methodology.
# TODO: Methodology section with mathematical model, objective function with penalties and description of CWS+ILS.

# PROBLEM: It does not a description of the dataset (size by neighborhood, cleaning, representativeness).
# TODO: Include a data section with descriptive statistics and the data cleaning process to provide transparency and context for the simulation results.

# PROBLEM: There is a comparative run, but without a design (repetitions, sensitivity, validation, significance).
# TODO: Develop a replicable experimental protocol with multiple runs, sensitivity analysis, and statistical validation to ensure the robustness and significance of the simulation results.

# PROBLEM: exploratory graphs are scattered, without final figures/tables or significance.
# TODO: Create comprehensive figures and tables that summarize the results by indicator and neighborhood, including statistical significance tests to support the conclusions drawn from the simulation.

# PROBLEM: There is no interpretation of which model performs best, where, and why, nor of the cost-emissions trade-off.
# TODO: Include a discussion section that interprets the results, identifies the best-performing models in different contexts, and analyzes the trade-offs between cost and emissions to provide actionable insights for urban logistics planning.

# PROBLEM: There is no recognition of the simplifications (radial, fixed capacity, no time windows, no traffic).
# TODO: Include a section on limitations that honestly acknowledges these simplifications.

# PROBLEM: Conclusions written in the memory but not in a paper section
# TODO: Conclusions and implications for PMUS/E-DUM and recomendations 

from pathlib import Path
import numpy as np
import pandas as pd
import osmnx as ox
import networkx as nx
from code.simulation.simulator import calculate_haversine
from code.common.paths import PROJECT_ROOT
ox.settings.log_console = True
ox.settings.use_cache = True


#np.random.seed(42)

# PROBLEM: Commented seeds and unfixed versions, configuration not saved with results.
# TODO: reproducible results bit by bit.


BASE_DIR = PROJECT_ROOT

ACTIVE_CITY = "madrid"      # "madrid", "barcelona", or "valencia"
ACTIVE_NEIGHBORHOOD = "Moratalaz"          # None = all neighborhoods / "Moratalaz" = one neighborhood only
DATA_FOLDER = BASE_DIR / "data"
RESULTS_FOLDER = BASE_DIR / "results"

def load_city_data(city: str):
    city_folder = DATA_FOLDER / city

    points = pd.read_csv(city_folder / "puntos_b2c.csv")
    centers = pd.read_csv(city_folder / "centros_cc.csv")
    boundaries = pd.read_csv(city_folder / "limites_barrios.csv")
    parameters = pd.read_csv(DATA_FOLDER / "parametros_modelos.csv")

    return points, centers, boundaries, parameters


def get_parameters(parameters: pd.DataFrame) -> dict:
    parameters = parameters.set_index("modelo")
    return parameters.to_dict(orient="index")


def load_networks(city: str):

    place = f"{city.title()}, Spain"

    G_drive = ox.graph_from_place(
        place,
        network_type="drive"
    )

    print("Network loaded successfully")
    print(f"   Nodes: {len(G_drive.nodes):,}")
    print(f"   Edges: {len(G_drive.edges):,}")

    return G_drive

def build_neighborhood_subgraph(
    G_drive,
    neighborhood,
    buffer=0.01
):

    north = neighborhood["lat_max"] + buffer
    south = neighborhood["lat_min"] - buffer
    east = neighborhood["lon_max"] + buffer
    west = neighborhood["lon_min"] - buffer

    return ox.truncate.truncate_graph_bbox(
        G_drive,
        bbox=(west, south, east, north)
    )

def assign_nodes(
    points,
    centers,
    G_drive
):
    
    print("Matching clients and logistics centers to the OpenStreetMap network...")

    points = points.copy()
    centers = centers.copy()

    points["node_drive"] = ox.distance.nearest_nodes(
        G_drive,
        points["Longitude"],
        points["Latitude"]
    )

    # Logistics centers

    centers["node_drive"] = ox.distance.nearest_nodes(
        G_drive,
        centers["Longitude"],
        centers["Latitude"]
    )

    print(f"{len(points)} clients matched to network nodes.")

    return points, centers


def filter_neighborhood_points(points: pd.DataFrame, neighborhood: pd.Series):
    return points[
        (points["Latitude"].between(neighborhood["lat_min"], neighborhood["lat_max"]))
        & (points["Longitude"].between(neighborhood["lon_min"], neighborhood["lon_max"]))
    ].copy()

# PROBLEM: the microhub/PUDO is assumed to be at the centroid of the neighborhood; there is no location decision, which is the core of the promised LRP (PR1/PR2).
# TODO: Implement an optimization model to determine the optimal location of the microhub/PUDO within the neighborhood, considering candidate locations such as parking lots, markets, post offices, metro stations, and lockers. 

def prepare_neighborhood(
    neighborhood_points,
    G_drive
):

    # Centroid
    lat = neighborhood_points["Latitude"].mean()
    lon = neighborhood_points["Longitude"].mean()

    # Centroid node
    centroid_drive_node = ox.distance.nearest_nodes(
        G_drive,
        lon,
        lat
    )

    return {
        "lat": lat,
        "lon": lon,
        "centro_drive": centroid_drive_node,
        "clientes_drive": neighborhood_points["node_drive"].tolist()
    }


def select_logistics_center(
    centers: pd.DataFrame,
    centroid_node: int,
    G_drive
):
    """
    Select the logistics center closest to the neighborhood centroid
    using the road network.
    """

    # Distances from the centroid to the entire network
    distances = nx.single_source_dijkstra_path_length(
        G_drive,
        centroid_node,
        weight="length"
    )

    centers = centers.copy()

    centers["distancia_km"] = centers["node_drive"].apply(
        lambda node: distances.get(node, np.inf) / 1000
    )

    selected_center = centers.loc[
        centers["distancia_km"].idxmin()
    ]

    return selected_center

# PROBLEM: The current implementation constructs a dense distance matrix by running Dijkstra's algorithm for each node in the subgraph. 
# This approach does not scale well, especially when dealing with multiple neighborhoods across different cities. Additionally, much of the computed data is only used for the Traveling Salesman Problem (TSP), 
# while the radial distance calculation only requires the row corresponding to the centroid.

# TODO: Refactor the distance calculation to be more efficient and scalable. Consider using a more targeted approach that computes only the necessary distances for the TSP and radial calculations,
# possibly by leveraging more efficient graph traversal algorithms or data structures. This will improve performance and scalability. 


def build_distance_matrix(
    G,
    node_list
):

    print("Calculating shortest paths with Dijkstra...")

    node_list = list(dict.fromkeys(node_list))

    n = len(node_list)

    matrix = np.full(
        (n, n),
        np.inf
    )

    node_map = {
        node: i 
        for i, node in enumerate(node_list)
    }

    for origin in node_list:

        i = node_map[origin]

        distances = nx.single_source_dijkstra_path_length(
            G,
            origin,
            weight="length"
        )

        for destination in node_list:

            j = node_map[destination]

            matrix[i, j] = (
                distances.get(destination, np.inf)
                / 1000
            )

    return matrix, node_map


def get_neighborhood_matrices(
    neighborhood_info,
    G_drive
):
    """
    Build the neighborhood distance matrices
    for driving, cycling, and walking.
    """

    drive_nodes = (
        [neighborhood_info["centro_drive"]]
        + neighborhood_info["clientes_drive"])

    print(
        f"\nBuilding distance matrix "
        f"on the OSM network ({len(drive_nodes)} nodes)..."
    )

    drive_matrix, node_map = build_distance_matrix(
        G_drive,
        drive_nodes
    )

    print("Distance matrix created with Dijkstra.")

    return drive_matrix, node_map

# PROBLEM: blocks of commented-out code and debug print statements are embedded in the main flow, which clutters and confuses the code.
# TODO: Remove commented-out code and replace print statements with configurable logging.


# def distancia_cc_barrio(
#     info_barrio,
#     cc,
#     matriz_drive
# ):
#     """
#     Calcula la distancia real entre el centro logístico seleccionado
#     y el centroide del barrio.
#     """

#     return matriz_drive.loc[
#         cc["node_drive"],
#         info_barrio["centro_drive"]
#     ]

# PROBLEM: The current implementation uses a nearest-neighbor heuristic for the Traveling Salesman Problem (TSP) without any declaration or quantification of the optimality gap.
# TODO: Investigate and implement a more robust TSP heuristic or solver that provides a quantifiable optimality gap. 
# Consider using established libraries or algorithms that can offer better performance and accuracy for TSP solutions in urban logistics scenarios.

# PROBLEM: The code currently uses a nearest-neighbor heuristic for the Traveling Salesman Problem (TSP) without any declaration or quantification of the optimality gap.
# TODO: Optimization nucleus: Implement a multi-start Clarke-Wright Savings (CWS) algorithm followed by an Iterated Local Search (ILS) metaheuristic to solve the TSP with an objective 
# function that penalizes CO2 emissions and traversing through vulnerable zones. 

def nearest_neighbor_tsp(
    start_node,
    clients,
    matrix,
    node_map
):

    pending = clients.copy()

    current_node = start_node

    km = 0

    while pending:

        next_node = min(
            pending,
            key=lambda node:
                matrix[
                    node_map[current_node],
                    node_map[node]
                ]
        )

        km += matrix[
            node_map[current_node],
            node_map[next_node]
        ]

        pending.remove(next_node)

        current_node = next_node


    km += matrix[
        node_map[current_node],
        node_map[start_node]
    ]

    return km


def calculate_radial_distance(
    nodo_origen,
    clients,
    matrix,
    node_map
):

    distance = 0

    for client in clients:

        distance += (
            matrix[
                node_map[nodo_origen],
                node_map[client]
            ] * 2
        )

    return distance


def simulate_neighborhood(
    city: str,
    neighborhood_name: str,
    neighborhood_points: pd.DataFrame,
    centers: pd.DataFrame,
    parameters: dict,
    G_drive,
    neighborhood: pd.Series,
):

    package_count = len(neighborhood_points)

    if package_count == 0:
        return []

    # --------------------------------------------------
    # Neighborhood preparation
    # --------------------------------------------------

    neighborhood_info = prepare_neighborhood(
        neighborhood_points,
        G_drive
    )

    print("Centroid:", neighborhood_info["lat"], neighborhood_info["lon"])

    node = neighborhood_info["centro_drive"]

    print("Node:", G_drive.nodes[node]["y"], G_drive.nodes[node]["x"])

    print(
        "Separation:",
        calculate_haversine(
            neighborhood_info["lat"],
            neighborhood_info["lon"],
            G_drive.nodes[node]["y"],
            G_drive.nodes[node]["x"]
        )
    )

    # --------------------------------------------------
    # Logistics center selection
    # --------------------------------------------------

    selected_cc = select_logistics_center(
        centers,
        neighborhood_info["centro_drive"],
        G_drive
    )

    # PROBLEM: truncate_graph_bbox and nearest_nodes changed their signatures between OSMnx 1.x and 2.x; without a pinned version, the code may break.
    # TODO: Ensure a reproducible environment with pinned dependencies to avoid compatibility issues with OSMnx updates.

    neighborhood_graph = build_neighborhood_subgraph(
        G_drive,
        neighborhood    
    )

    print(
        f"Selected logistics center: "
        f"{selected_cc['Location']}"
    )
    # --------------------------------------------------
    # Distance matrix construction
    # --------------------------------------------------

    drive_matrix, node_map = get_neighborhood_matrices(
        neighborhood_info,
        neighborhood_graph
    )

    # --------------------------------------------------
    # Trunk distance
    # --------------------------------------------------

    # distancia_troncal = distancia_cc_barrio(
    #     info_barrio,
    #     cc_seleccionado,
    #     matriz_drive
    # )

    trunk_distance = (
        nx.astar_path_length(
            G_drive,
            selected_cc["node_drive"],
            neighborhood_info["centro_drive"],
            heuristic=lambda u, v: calculate_haversine(
                G_drive.nodes[u]["y"],
                G_drive.nodes[u]["x"],
                G_drive.nodes[v]["y"],
                G_drive.nodes[v]["x"],
            ) * 1000,   # metros
            weight="length",
        )
        / 1000
    )

    print(
        f"Road distance from CC to neighborhood: "
        f"{trunk_distance:.2f} km"
    )

    # --------------------------------------------------
    # Internal kilometers (TSP)
    # --------------------------------------------------

    # PROBLEM: It does not consider the vehicle capacity, it just calculates the TSP for all clients in the neighborhood. This is not realistic for delivery scenarios where vehicles have limited capacity.
    
    # TODO:

    ### Divide list in groups of size vehicle_capacity
    ### For each group, calculate the optimal internal route (TSP)
    ### Sum the kilometers of all internal routes (one per group) plus the round trips to the CC.

    internal_km = nearest_neighbor_tsp(
        neighborhood_info["centro_drive"],
        neighborhood_info["clientes_drive"],
        drive_matrix,
        node_map
    )

    # --------------------------------------------------
    # Radial distance (PUDO and walking delivery)
    # --------------------------------------------------

    # PROBLEM: The radial distance for the delivery person on foot (km_repartidor_pie) is calculated using the drive network, which may not accurately reflect the actual walking distances. 
    # This could lead to underestimating or overestimating the distances and times for pedestrian deliveries.
    # TODO: Consider using a pedestrian network (if available) to calculate the radial distance for the delivery person on foot. This would provide a more accurate estimate of the distances and times for pedestrian deliveries.

    courier_walking_km = calculate_radial_distance(
        neighborhood_info["centro_drive"],
        neighborhood_info["clientes_drive"],
        drive_matrix,
        node_map
    )

    results = []

    # ==================================================
    # M1
    # ==================================================

    m1 = parameters["FURGONETA_CONV"]

    trips_1 = int(np.ceil(package_count / m1["capacidad"]))

    total_km_1 = (
        trunk_distance * 2 * trips_1
        + internal_km
    )

    cost_1 = (
        total_km_1 * m1["costo_km"]
        + (total_km_1 / m1["v_media"]) * m1["costo_hora"]
    )

    co2_1 = (
        total_km_1 * m1["co2_km"]
    ) / 1000

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

    total_km_2 = (
        trunk_distance * 2 * trips_2
        + internal_km
    )

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

    # PROBLEM: The code currently uses the parameters of "FURGONETA_CONV" (conventional van) for the trunk leg from the CC to the hub in models M3, M4, and M5. 
    # However, according to the project specifications, this trunk leg should use the parameters of "FURGONETA_ELEC" (electric van) for sustainable scenarios. This discrepancy leads to inflated emissions and costs in the green scenarios.

    # TODO: Update the code to use the correct vehicle parameters for the trunk leg in models M3, M4, and M5. 
    # Specifically, replace the references to "FURGONETA_CONV" with "FURGONETA_ELEC" when calculating the cost and CO₂ emissions for the CC→hub leg.

    hub_truck_cost = (
        hub_supply_km
        * parameters["FURGONETA_CONV"]["costo_km"]
    )

    hub_truck_co2 = (
        hub_supply_km
        * parameters["FURGONETA_CONV"]["co2_km"]
    ) / 1000

    internal_bike_km = internal_km * 1.15

    # PROBLEM: The current implementation uses a fixed factor of 1.15 to account for the deviation in bicycle routes compared to the optimal TSP route.
    # TODO: Validate the 1.15 deviation factor for bicycle routes against real-world data or consider using actual routing algorithms to calculate more accurate bicycle distances. 

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

    walking_cost = (
        courier_walking_km / m4["v_media"]
    ) * m4["costo_hora"]

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

    client = neighborhood_info["clientes_drive"][0]

    print(
        "OSM:",
        drive_matrix[
            node_map[neighborhood_info["centro_drive"]],
            node_map[client]
        ]
    )

    row = neighborhood_points.iloc[0]

    print("HAV:", calculate_haversine(
        neighborhood_info["lat"],
        neighborhood_info["lon"],
        row["Latitude"],
        row["Longitude"]
    ))

    # ==================================================
    # M5
    # ==================================================

    m5 = parameters["PUDO_CONSUMIDOR"]

    customer_co2 = (
        courier_walking_km
        * m5["co2_km_estimado_cliente"]
    ) / 1000

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

# PROBLEM: One single run per neighborhood; there is no stochasticity or Monte Carlo simulation, even though PR3/PR4 mention variability in demand and density.
# TODO: Implement multiple runs with variable demand and density, and calculate confidence intervals for the results to account for stochasticity in the simulation.

def simulate_city(
    city: str,
    active_neighborhood: str | None = None
):

    # ---------------------------------------------
    # Data loading
    # ---------------------------------------------

    points, centers, boundaries, parameters_df = load_city_data(city)
    parameters = get_parameters(parameters_df)

    # ---------------------------------------------
    # Road network loading
    # ---------------------------------------------

    G_drive = load_networks(city)

    ## PROBELM: The current implementation uses OSMnx to download and prepare the road network for the city. 
    # However, it does not provide any references or citations to support the choice of using OSMnx or the specific methods employed for shortest-path calculations. 

    # TODO: Include references to relevant literature, such as Boeing (2017) or other studies that validate the use of OSMnx and shortest-path algorithms for urban logistics simulations. 

    # ---------------------------------------------
    # OSM node assignment
    # ---------------------------------------------

    print("\nAssigning each client to the nearest OSM network node...")

    points, centers = assign_nodes(
        points,
        centers,
        G_drive
    )

    print(f"Clients matched to the network: {len(points)}")
    print(f"Logistics centers matched: {len(centers)}")

    all_results = []

    # ---------------------------------------------
    # Filter neighborhood when applicable
    # ---------------------------------------------

    if active_neighborhood is not None:

        boundaries = boundaries[
            boundaries["barrio"].str.lower()
            == active_neighborhood.lower()
        ]

        if boundaries.empty:
            raise ValueError(
                f"Neighborhood '{active_neighborhood}' was not found in {city}."
            )

    # ---------------------------------------------
    # Neighborhood-by-neighborhood simulation
    # ---------------------------------------------

    for _, neighborhood in boundaries.iterrows():

        neighborhood_name = neighborhood["barrio"]

        print(f"\n📍 Simulating {city.upper()} - {neighborhood_name}")

        neighborhood_points = filter_neighborhood_points(
            points,
            neighborhood
        )

        if neighborhood_points.empty:

            print(
                f"⚠️ There are no points within the boundaries of {neighborhood_name}."
            )

            continue

        results = simulate_neighborhood(
            city=city,
            neighborhood_name=neighborhood_name,
            neighborhood_points=neighborhood_points,
            centers=centers,
            neighborhood=neighborhood,
            parameters=parameters,
            G_drive=G_drive
        )

        all_results.extend(results)

        print(
            f"✅ {neighborhood_name}: {len(neighborhood_points)} packages simulated"
        )

    return pd.DataFrame(all_results)


# =============================================================================
# 7. EXECUTION
# =============================================================================

if __name__ == "__main__":

    print("=" * 60)
    print("OPENSTREETMAP-BASED LOGISTICS SIMULATION")
    print("=" * 60)
    print(f"City: {ACTIVE_CITY}")
    print(f"Neighborhood: {ACTIVE_NEIGHBORHOOD}")
    print(f"OSMnx version: {ox.__version__}")
    print("Network type: drive")
    print("=" * 60)

    RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    print("Downloading/preparing road network...")

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
            f"resultados_{ACTIVE_CITY}.csv"
            if ACTIVE_NEIGHBORHOOD is None
            else f"resultados_{ACTIVE_CITY}_{ACTIVE_NEIGHBORHOOD}.csv"
        )

        output_path = RESULTS_FOLDER / output_filename

        results_df.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"\nResults saved to: {output_path.resolve()}")