
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
from code.simulation.simulador import calcular_haversine
from code.common.paths import PROJECT_ROOT
ox.settings.log_console = True
ox.settings.use_cache = True


#np.random.seed(42)

# PROBLEM: Commented seeds and unfixed versions, configuration not saved with results.
# TODO: reproducible results bit by bit.


BASE_DIR = PROJECT_ROOT

CIUDAD_ACTIVA = "madrid"      # "madrid", "barcelona" o "valencia"
BARRIO_ACTIVO = "Moratalaz"          # None = todos los barrios / "Moratalaz" = solo uno
CARPETA_DATA = BASE_DIR / "data"
CARPETA_RESULTADOS = BASE_DIR / "results"

def cargar_datos_ciudad(ciudad: str):
    carpeta_ciudad = CARPETA_DATA / ciudad

    puntos = pd.read_csv(carpeta_ciudad / "puntos_b2c.csv")
    centros = pd.read_csv(carpeta_ciudad / "centros_cc.csv")
    limites = pd.read_csv(carpeta_ciudad / "limites_barrios.csv")
    parametros = pd.read_csv(CARPETA_DATA / "parametros_modelos.csv")

    return puntos, centros, limites, parametros


def obtener_parametros(parametros: pd.DataFrame) -> dict:
    parametros = parametros.set_index("modelo")
    return parametros.to_dict(orient="index")


def cargar_redes(ciudad: str):

    lugar = f"{ciudad.title()}, Spain"

    G_drive = ox.graph_from_place(
        lugar,
        network_type="drive"
    )

    print(f"Red cargada correctamente")
    print(f"   Nodos: {len(G_drive.nodes):,}")
    print(f"   Aristas: {len(G_drive.edges):,}")

    return G_drive

def construir_subgrafo_barrio(
    G_drive,
    barrio,
    buffer=0.01
):

    north = barrio["lat_max"] + buffer
    south = barrio["lat_min"] - buffer
    east = barrio["lon_max"] + buffer
    west = barrio["lon_min"] - buffer

    return ox.truncate.truncate_graph_bbox(
        G_drive,
        bbox=(west, south, east, north)
    )

def asignar_nodos(
    puntos,
    centros,
    G_drive
):
    
    print("Asociando clientes y centros a la red de OpenStreetMap...")

    puntos = puntos.copy()
    centros = centros.copy()

    puntos["node_drive"] = ox.distance.nearest_nodes(
        G_drive,
        puntos["Longitude"],
        puntos["Latitude"]
    )

    # Centros logísticos

    centros["node_drive"] = ox.distance.nearest_nodes(
        G_drive,
        centros["Longitude"],
        centros["Latitude"]
    )

    print(f"{len(puntos)} clientes asociados a nodos de la red.")

    return puntos, centros


def filtrar_puntos_barrio(puntos: pd.DataFrame, barrio: pd.Series):
    return puntos[
        (puntos["Latitude"].between(barrio["lat_min"], barrio["lat_max"]))
        & (puntos["Longitude"].between(barrio["lon_min"], barrio["lon_max"]))
    ].copy()

# PROBLEM: the microhub/PUDO is assumed to be at the centroid of the neighborhood; there is no location decision, which is the core of the promised LRP (PR1/PR2).
# TODO: Implement an optimization model to determine the optimal location of the microhub/PUDO within the neighborhood, considering candidate locations such as parking lots, markets, post offices, metro stations, and lockers. 

def preparar_barrio(
    puntos_barrio,
    G_drive
):

    # Centroide
    lat = puntos_barrio["Latitude"].mean()
    lon = puntos_barrio["Longitude"].mean()

    # Nodo del centroide
    centro_drive = ox.distance.nearest_nodes(
        G_drive,
        lon,
        lat
    )

    return {
        "lat": lat,
        "lon": lon,
        "centro_drive": centro_drive,
        "clientes_drive": puntos_barrio["node_drive"].tolist()
    }


def seleccionar_centro_logistico(
    centros: pd.DataFrame,
    nodo_centroide: int,
    G_drive
):
    """
    Selecciona el centro logístico más cercano al centroide del barrio
    utilizando la red viaria.
    """

    # Distancias desde el centroide a toda la red
    distancias = nx.single_source_dijkstra_path_length(
        G_drive,
        nodo_centroide,
        weight="length"
    )

    centros = centros.copy()

    centros["distancia_km"] = centros["node_drive"].apply(
        lambda nodo: distancias.get(nodo, np.inf) / 1000
    )

    cc = centros.loc[
        centros["distancia_km"].idxmin()
    ]

    return cc

# PROBLEM: The current implementation constructs a dense distance matrix by running Dijkstra's algorithm for each node in the subgraph. 
# This approach does not scale well, especially when dealing with multiple neighborhoods across different cities. Additionally, much of the computed data is only used for the Traveling Salesman Problem (TSP), 
# while the radial distance calculation only requires the row corresponding to the centroid.

# TODO: Refactor the distance calculation to be more efficient and scalable. Consider using a more targeted approach that computes only the necessary distances for the TSP and radial calculations,
# possibly by leveraging more efficient graph traversal algorithms or data structures. This will improve performance and scalability. 


def construir_matriz_distancias(
    G,
    lista_nodos
):

    print("Calculando rutas mínimas con Dijkstra...")

    lista_nodos = list(dict.fromkeys(lista_nodos))

    n = len(lista_nodos)

    matriz = np.full(
        (n, n),
        np.inf
    )

    mapa_nodos = {
        nodo: i 
        for i, nodo in enumerate(lista_nodos)
    }

    for origen in lista_nodos:

        i = mapa_nodos[origen]

        distancias = nx.single_source_dijkstra_path_length(
            G,
            origen,
            weight="length"
        )

        for destino in lista_nodos:

            j = mapa_nodos[destino]

            matriz[i, j] = (
                distancias.get(destino, np.inf)
                / 1000
            )

    return matriz, mapa_nodos


def obtener_matrices_barrio(
    info_barrio,
    G_drive
):
    """
    Construye las matrices de distancias del barrio
    para coche, bicicleta y peatón.
    """

    nodos_drive = (
        [info_barrio["centro_drive"]]
        + info_barrio["clientes_drive"])

    print(
        f"\nConstruyendo matriz de distancias "
        f"sobre la red OSM ({len(nodos_drive)} nodos)..."
    )

    matriz_drive, mapa_nodos = construir_matriz_distancias(
        G_drive,
        nodos_drive
    )

    print("Matriz de distancias creada mediante Dijkstra.")

    return matriz_drive, mapa_nodos

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

def tsp_vecino_mas_cercano(
    nodo_inicio,
    clientes,
    matriz,
    mapa_nodos
):

    pendientes = clientes.copy()

    nodo_actual = nodo_inicio

    km = 0

    while pendientes:

        siguiente = min(
            pendientes,
            key=lambda nodo:
                matriz[
                    mapa_nodos[nodo_actual],
                    mapa_nodos[nodo]
                ]
        )

        km += matriz[
            mapa_nodos[nodo_actual],
            mapa_nodos[siguiente]
        ]

        pendientes.remove(siguiente)

        nodo_actual = siguiente


    km += matriz[
        mapa_nodos[nodo_actual],
        mapa_nodos[nodo_inicio]
    ]

    return km


def calcular_distancia_radial(
    nodo_origen,
    clientes,
    matriz,
    mapa_nodos
):

    distancia = 0

    for cliente in clientes:

        distancia += (
            matriz[
                mapa_nodos[nodo_origen],
                mapa_nodos[cliente]
            ] * 2
        )

    return distancia


def simular_barrio(
    ciudad: str,
    nombre_barrio: str,
    puntos_barrio: pd.DataFrame,
    centros: pd.DataFrame,
    parametros: dict,
    G_drive,
    barrio: pd.Series,
):

    num_paquetes = len(puntos_barrio)

    if num_paquetes == 0:
        return []

    # --------------------------------------------------
    # Preparación del barrio
    # --------------------------------------------------

    info_barrio = preparar_barrio(
        puntos_barrio,
        G_drive
    )

    print("Centroide:", info_barrio["lat"], info_barrio["lon"])

    nodo = info_barrio["centro_drive"]

    print("Nodo:", G_drive.nodes[nodo]["y"], G_drive.nodes[nodo]["x"])

    print(
        "Separación:",
        calcular_haversine(
            info_barrio["lat"],
            info_barrio["lon"],
            G_drive.nodes[nodo]["y"],
            G_drive.nodes[nodo]["x"]
        )
    )

    # --------------------------------------------------
    # Selección del centro logístico
    # --------------------------------------------------

    cc_seleccionado = seleccionar_centro_logistico(
        centros,
        info_barrio["centro_drive"],
        G_drive
    )

    # PROBLEM: truncate_graph_bbox and nearest_nodes changed their signatures between OSMnx 1.x and 2.x; without a pinned version, the code may break.
    # TODO: Ensure a reproducible environment with pinned dependencies to avoid compatibility issues with OSMnx updates.

    G_barrio = construir_subgrafo_barrio(
        G_drive,
        barrio    
    )

    print(
        f"Centro logístico seleccionado: "
        f"{cc_seleccionado['Location']}"
    )
    # --------------------------------------------------
    # Construcción de la matriz de distancias
    # --------------------------------------------------

    matriz_drive, mapa_nodos = obtener_matrices_barrio(
        info_barrio,
        G_barrio
    )

    # --------------------------------------------------
    # Distancia troncal
    # --------------------------------------------------

    # distancia_troncal = distancia_cc_barrio(
    #     info_barrio,
    #     cc_seleccionado,
    #     matriz_drive
    # )

    distancia_troncal = (
        nx.astar_path_length(
            G_drive,
            cc_seleccionado["node_drive"],
            info_barrio["centro_drive"],
            heuristic=lambda u, v: calcular_haversine(
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
        f"Distancia por carretera CC → barrio: "
        f"{distancia_troncal:.2f} km"
    )

    # --------------------------------------------------
    # Kilómetros internos (TSP)
    # --------------------------------------------------

    # PROBLEM: It does not consider the vehicle capacity, it just calculates the TSP for all clients in the neighborhood. This is not realistic for delivery scenarios where vehicles have limited capacity.
    
    # TODO:

    ### Divide list in groups of size vehicle_capacity
    ### For each group, calculate the optimal internal route (TSP)
    ### Sum the kilometers of all internal routes (one per group) plus the round trips to the CC.

    km_internos = tsp_vecino_mas_cercano(
        info_barrio["centro_drive"],
        info_barrio["clientes_drive"],
        matriz_drive,
        mapa_nodos
    )

    # --------------------------------------------------
    # Distancia radial (PUDO y reparto a pie)
    # --------------------------------------------------

    # PROBLEM: The radial distance for the delivery person on foot (km_repartidor_pie) is calculated using the drive network, which may not accurately reflect the actual walking distances. 
    # This could lead to underestimating or overestimating the distances and times for pedestrian deliveries.
    # TODO: Consider using a pedestrian network (if available) to calculate the radial distance for the delivery person on foot. This would provide a more accurate estimate of the distances and times for pedestrian deliveries.

    km_repartidor_pie = calcular_distancia_radial(
        info_barrio["centro_drive"],
        info_barrio["clientes_drive"],
        matriz_drive,
        mapa_nodos
    )

    resultados = []

    # ==================================================
    # M1
    # ==================================================

    m1 = parametros["FURGONETA_CONV"]

    viajes_1 = int(np.ceil(num_paquetes / m1["capacidad"]))

    km_totales_1 = (
        distancia_troncal * 2 * viajes_1
        + km_internos
    )

    costo_1 = (
        km_totales_1 * m1["costo_km"]
        + (km_totales_1 / m1["v_media"]) * m1["costo_hora"]
    )

    co2_1 = (
        km_totales_1 * m1["co2_km"]
    ) / 1000

    resultados.append({
        "ciudad": ciudad,
        "barrio": nombre_barrio,
        "modelo": "M1: Furgoneta Combustión desde CC",
        "centro_logistico": cc_seleccionado["Location"],
        "paquetes": num_paquetes,
        "distancia_troncal_km": distancia_troncal,
        "km_recorridos": km_totales_1,
        "numero_viajes": viajes_1,
        "emisiones_co2_kg": co2_1,
        "costo_total_eur": costo_1,
    })

    # ==================================================
    # M2
    # ==================================================

    m2 = parametros["FURGONETA_ELEC"]

    viajes_2 = int(np.ceil(num_paquetes / m2["capacidad"]))

    km_totales_2 = (
        distancia_troncal * 2 * viajes_2
        + km_internos
    )

    costo_2 = (
        km_totales_2 * m2["costo_km"]
        + (km_totales_2 / m2["v_media"]) * m2["costo_hora"]
    )

    resultados.append({
        "ciudad": ciudad,
        "barrio": nombre_barrio,
        "modelo": "M2: Furgoneta Eléctrica desde CC",
        "centro_logistico": cc_seleccionado["Location"],
        "paquetes": num_paquetes,
        "distancia_troncal_km": distancia_troncal,
        "km_recorridos": km_totales_2,
        "numero_viajes": viajes_2,
        "emisiones_co2_kg": 0.0,
        "costo_total_eur": costo_2,
    })

    # ==================================================
    # M3
    # ==================================================

    m3 = parametros["BICICLETA_CARGO"]

    viajes_bike = int(np.ceil(num_paquetes / m3["capacidad"]))

    km_abastecimiento_hub = distancia_troncal * 2

    # PROBLEM: The code currently uses the parameters of "FURGONETA_CONV" (conventional van) for the trunk leg from the CC to the hub in models M3, M4, and M5. 
    # However, according to the project specifications, this trunk leg should use the parameters of "FURGONETA_ELEC" (electric van) for sustainable scenarios. This discrepancy leads to inflated emissions and costs in the green scenarios.

    # TODO: Update the code to use the correct vehicle parameters for the trunk leg in models M3, M4, and M5. 
    # Specifically, replace the references to "FURGONETA_CONV" with "FURGONETA_ELEC" when calculating the cost and CO₂ emissions for the CC→hub leg.

    costo_camion_hub = (
        km_abastecimiento_hub
        * parametros["FURGONETA_CONV"]["costo_km"]
    )

    co2_camion_hub = (
        km_abastecimiento_hub
        * parametros["FURGONETA_CONV"]["co2_km"]
    ) / 1000

    km_bike_internos = km_internos * 1.15

    # PROBLEM: The current implementation uses a fixed factor of 1.15 to account for the deviation in bicycle routes compared to the optimal TSP route.
    # TODO: Validate the 1.15 deviation factor for bicycle routes against real-world data or consider using actual routing algorithms to calculate more accurate bicycle distances. 

    costo_bike = (
        km_bike_internos * m3["costo_km"]
        + (km_bike_internos / m3["v_media"]) * m3["costo_hora"]
    )

    resultados.append({
        "ciudad": ciudad,
        "barrio": nombre_barrio,
        "modelo": "M3: CC -> Microhub -> Bicicleta",
        "centro_logistico": cc_seleccionado["Location"],
        "paquetes": num_paquetes,
        "distancia_troncal_km": distancia_troncal,
        "km_recorridos": km_abastecimiento_hub + km_bike_internos,
        "numero_viajes": 1 + viajes_bike,
        "emisiones_co2_kg": co2_camion_hub,
        "costo_total_eur": costo_camion_hub + costo_bike + m3["fijo_hub_dia"],
    })

    # ==================================================
    # M4
    # ==================================================

    m4 = parametros["PUDO_A_PIE"]

    viajes_pie = int(np.ceil(num_paquetes / m4["capacidad"]))

    costo_pie = (
        km_repartidor_pie / m4["v_media"]
    ) * m4["costo_hora"]

    resultados.append({
        "ciudad": ciudad,
        "barrio": nombre_barrio,
        "modelo": "M4: CC -> PUDO -> Entrega a pie",
        "centro_logistico": cc_seleccionado["Location"],
        "paquetes": num_paquetes,
        "distancia_troncal_km": distancia_troncal,
        "km_recorridos": km_abastecimiento_hub + km_repartidor_pie,
        "numero_viajes": 1 + viajes_pie,
        "emisiones_co2_kg": co2_camion_hub,
        "costo_total_eur": (
            costo_camion_hub
            + costo_pie
            + num_paquetes * m4["comision_pudo"]
        ),
    })

    cliente = info_barrio["clientes_drive"][0]

    print(
        "OSM:",
        matriz_drive[
            mapa_nodos[info_barrio["centro_drive"]],
            mapa_nodos[cliente]
        ]
    )

    fila = puntos_barrio.iloc[0]

    print("HAV:", calcular_haversine(
        info_barrio["lat"],
        info_barrio["lon"],
        fila["Latitude"],
        fila["Longitude"]
    ))

    # ==================================================
    # M5
    # ==================================================

    m5 = parametros["PUDO_CONSUMIDOR"]

    co2_clientes = (
        km_repartidor_pie
        * m5["co2_km_estimado_cliente"]
    ) / 1000

    resultados.append({
        "ciudad": ciudad,
        "barrio": nombre_barrio,
        "modelo": "M5: CC -> PUDO -> Recogida Cliente",
        "centro_logistico": cc_seleccionado["Location"],
        "paquetes": num_paquetes,
        "distancia_troncal_km": distancia_troncal,
        "km_recorridos": km_abastecimiento_hub + km_repartidor_pie,
        "numero_viajes": 1 + num_paquetes,
        "emisiones_co2_kg": co2_camion_hub + co2_clientes,
        "costo_total_eur": (
            costo_camion_hub
            + num_paquetes * m5["comision_pudo"]
        ),
    })

    return resultados

# PROBLEM: One single run per neighborhood; there is no stochasticity or Monte Carlo simulation, even though PR3/PR4 mention variability in demand and density.
# TODO: Implement multiple runs with variable demand and density, and calculate confidence intervals for the results to account for stochasticity in the simulation.

def simular_ciudad(
    ciudad: str,
    barrio_activo: str | None = None
):

    # ---------------------------------------------
    # Carga de datos
    # ---------------------------------------------

    puntos, centros, limites, parametros_df = cargar_datos_ciudad(ciudad)
    parametros = obtener_parametros(parametros_df)

    # ---------------------------------------------
    # Carga de la red viaria
    # ---------------------------------------------

    G_drive = cargar_redes(ciudad)

    ## PROBELM: The current implementation uses OSMnx to download and prepare the road network for the city. 
    # However, it does not provide any references or citations to support the choice of using OSMnx or the specific methods employed for shortest-path calculations. 

    # TODO: Include references to relevant literature, such as Boeing (2017) or other studies that validate the use of OSMnx and shortest-path algorithms for urban logistics simulations. 

    # ---------------------------------------------
    # Asignación de nodos OSM
    # ---------------------------------------------

    print("\nAsignando cada cliente al nodo más cercano de la red OSM...")

    puntos, centros = asignar_nodos(
        puntos,
        centros,
        G_drive
    )

    print(f"Clientes asociados a la red: {len(puntos)}")
    print(f"Centros logísticos asociados: {len(centros)}")

    resultados_totales = []

    # ---------------------------------------------
    # Filtrar barrio si procede
    # ---------------------------------------------

    if barrio_activo is not None:

        limites = limites[
            limites["barrio"].str.lower()
            == barrio_activo.lower()
        ]

        if limites.empty:
            raise ValueError(
                f"No se encontró el barrio '{barrio_activo}' en {ciudad}."
            )

    # ---------------------------------------------
    # Simulación barrio a barrio
    # ---------------------------------------------

    for _, barrio in limites.iterrows():

        nombre_barrio = barrio["barrio"]

        print(f"\n📍 Simulando {ciudad.upper()} - {nombre_barrio}")

        puntos_barrio = filtrar_puntos_barrio(
            puntos,
            barrio
        )

        if puntos_barrio.empty:

            print(
                f"⚠️ No hay puntos dentro de los límites de {nombre_barrio}."
            )

            continue

        resultados = simular_barrio(
            ciudad=ciudad,
            nombre_barrio=nombre_barrio,
            puntos_barrio=puntos_barrio,
            centros=centros,
            barrio=barrio,
            parametros=parametros,
            G_drive=G_drive
        )

        resultados_totales.extend(resultados)

        print(
            f"✅ {nombre_barrio}: {len(puntos_barrio)} paquetes simulados"
        )

    return pd.DataFrame(resultados_totales)


# =============================================================================
# 7. EJECUCIÓN
# =============================================================================

if __name__ == "__main__":

    print("=" * 60)
    print("SIMULACIÓN LOGÍSTICA BASADA EN OpenStreetMap")
    print("=" * 60)
    print(f"Ciudad: {CIUDAD_ACTIVA}")
    print(f"Barrio: {BARRIO_ACTIVO}")
    print(f"Versión OSMnx: {ox.__version__}")
    print("Tipo de red: drive")
    print("=" * 60)

    CARPETA_RESULTADOS.mkdir(parents=True, exist_ok=True)

    print("Cargando datos...")
    print("Descargando/preparando red viaria...")

    df_resultados = simular_ciudad(
        ciudad=CIUDAD_ACTIVA,
        barrio_activo=BARRIO_ACTIVO,
    )

    if df_resultados.empty:
        print("\nNo se generaron resultados.")

    else:

        df_resultados = df_resultados.round(2)

        print("\nRESULTADOS")
        print(df_resultados.to_string(index=False))

        nombre_archivo = (
            f"resultados_{CIUDAD_ACTIVA}.csv"
            if BARRIO_ACTIVO is None
            else f"resultados_{CIUDAD_ACTIVA}_{BARRIO_ACTIVO}.csv"
        )

        ruta_salida = CARPETA_RESULTADOS / nombre_archivo

        df_resultados.to_csv(
            ruta_salida,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"\nResultados guardados en: {ruta_salida.resolve()}")