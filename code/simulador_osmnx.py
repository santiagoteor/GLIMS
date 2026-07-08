from pathlib import Path
import numpy as np
import pandas as pd
import osmnx as ox
import networkx as nx
from simulador import calcular_haversine

ox.settings.log_console = True
ox.settings.use_cache = True

#np.random.seed(42)

BASE_DIR = Path(__file__).resolve().parent.parent

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


def construir_matriz_distancias(
    G,
    lista_nodos
):
    """
    Construye una matriz de distancias (km) entre todos los nodos
    utilizando la red viaria.
    """

    print(f"Calculando rutas mínimas con Dijkstra...")

    # Eliminar duplicados conservando el orden
    lista_nodos = list(dict.fromkeys(lista_nodos))

    matriz = pd.DataFrame(
        index=lista_nodos,
        columns=lista_nodos,
        dtype=float
    )

    for origen in lista_nodos:

        # Distancias desde un nodo a todos los demás
        distancias = nx.single_source_dijkstra_path_length(
            G,
            origen,
            weight="length"
        )

        for destino in lista_nodos:

            matriz.loc[origen, destino] = (
                distancias.get(destino, np.inf)
                / 1000
            )

    return matriz


def obtener_matrices_barrio(
    info_barrio,
    cc,
    G_drive
):
    """
    Construye las matrices de distancias del barrio
    para coche, bicicleta y peatón.
    """

    nodos_drive = (
        [info_barrio["centro_drive"]]
        + info_barrio["clientes_drive"]
        + [cc["node_drive"]]
    )

    print(
        f"\nConstruyendo matriz de distancias "
        f"sobre la red OSM ({len(nodos_drive)} nodos)..."
    )

    matriz_drive = construir_matriz_distancias(
        G_drive,
        nodos_drive
    )

    print("Matriz de distancias creada mediante Dijkstra.")

    return matriz_drive


def distancia_cc_barrio(
    info_barrio,
    cc,
    matriz_drive
):
    """
    Calcula la distancia real entre el centro logístico seleccionado
    y el centroide del barrio.
    """

    return matriz_drive.loc[
        cc["node_drive"],
        info_barrio["centro_drive"]
    ]


def tsp_vecino_mas_cercano(
    nodo_inicio,
    clientes,
    matriz
):

    pendientes = clientes.copy()

    nodo_actual = nodo_inicio

    km = 0

    while pendientes:

        siguiente = min(
            pendientes,
            key=lambda nodo:
                matriz.loc[
                    nodo_actual,
                    nodo
                ]
        )

        km += matriz.loc[
            nodo_actual,
            siguiente
        ]

        pendientes.remove(siguiente)

        nodo_actual = siguiente

    km += matriz.loc[
        nodo_actual,
        nodo_inicio
    ]

    return km


def calcular_distancia_radial(
    nodo_origen,
    clientes,
    matriz
):

    distancia = 0

    for cliente in clientes:

        distancia += (
            matriz.loc[nodo_origen, cliente] * 2
        )

    return distancia


def simular_barrio(
    ciudad: str,
    nombre_barrio: str,
    puntos_barrio: pd.DataFrame,
    centros: pd.DataFrame,
    parametros: dict,
    G_drive
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

    print(
        f"Centro logístico seleccionado: "
        f"{cc_seleccionado['Location']}"
    )

    # --------------------------------------------------
    # Construcción de la matriz de distancias
    # --------------------------------------------------

    matriz_drive = obtener_matrices_barrio(
        info_barrio,
        cc_seleccionado,
        G_drive
    )

    # --------------------------------------------------
    # Distancia troncal
    # --------------------------------------------------

    distancia_troncal = distancia_cc_barrio(
        info_barrio,
        cc_seleccionado,
        matriz_drive
    )

    print(
        f"Distancia por carretera CC → barrio: "
        f"{distancia_troncal:.2f} km"
    )

    # --------------------------------------------------
    # Kilómetros internos (TSP)
    # --------------------------------------------------

    km_internos = tsp_vecino_mas_cercano(
        info_barrio["centro_drive"],
        info_barrio["clientes_drive"],
        matriz_drive
    )

    # --------------------------------------------------
    # Distancia radial (PUDO y reparto a pie)
    # --------------------------------------------------

    km_repartidor_pie = calcular_distancia_radial(
        info_barrio["centro_drive"],
        info_barrio["clientes_drive"],
        matriz_drive
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

    costo_camion_hub = (
        km_abastecimiento_hub
        * parametros["FURGONETA_CONV"]["costo_km"]
    )

    co2_camion_hub = (
        km_abastecimiento_hub
        * parametros["FURGONETA_CONV"]["co2_km"]
    ) / 1000

    km_bike_internos = km_internos * 1.15

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

    print("OSM:", matriz_drive.loc[
        info_barrio["centro_drive"],
        cliente
    ])

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