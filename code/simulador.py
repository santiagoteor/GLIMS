from pathlib import Path
import numpy as np
import pandas as pd

np.random.seed(42)

BASE_DIR = Path(__file__).resolve().parent.parent

CIUDAD_ACTIVA = "madrid"      # "madrid", "barcelona" o "valencia"
BARRIO_ACTIVO = "moratalaz"          # None = todos los barrios / "Moratalaz" = solo uno

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


def calcular_haversine(lat1, lon1, lat2, lon2):
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


def simular_kilometros_tsp(inicio_lat, inicio_lon, df_puntos_ruta):
    puntos_restantes = df_puntos_ruta.copy()
    km_acumulados = 0.0
    pos_actual = (inicio_lat, inicio_lon)

    while len(puntos_restantes) > 0:
        distancias = puntos_restantes.apply(
            lambda r: calcular_haversine(
                pos_actual[0],
                pos_actual[1],
                r["Latitude"],
                r["Longitude"],
            ),
            axis=1,
        )

        idx_cercano = distancias.idxmin()
        km_acumulados += distancias.min()

        pos_actual = (
            puntos_restantes.loc[idx_cercano, "Latitude"],
            puntos_restantes.loc[idx_cercano, "Longitude"],
        )

        puntos_restantes = puntos_restantes.drop(idx_cercano)

    km_acumulados += calcular_haversine(
        pos_actual[0],
        pos_actual[1],
        inicio_lat,
        inicio_lon,
    )

    return km_acumulados


def filtrar_puntos_barrio(puntos: pd.DataFrame, barrio: pd.Series):
    return puntos[
        (puntos["Latitude"].between(barrio["lat_min"], barrio["lat_max"]))
        & (puntos["Longitude"].between(barrio["lon_min"], barrio["lon_max"]))
    ].copy()


def simular_barrio(
    ciudad: str,
    nombre_barrio: str,
    puntos_barrio: pd.DataFrame,
    centros: pd.DataFrame,
    parametros: dict,
):
    num_paquetes = len(puntos_barrio)

    if num_paquetes == 0:
        return []

    barrio_lat_centro = puntos_barrio["Latitude"].mean()
    barrio_lon_centro = puntos_barrio["Longitude"].mean()

    centros = centros.copy()
    centros["dist_al_barrio"] = centros.apply(
        lambda c: calcular_haversine(
            barrio_lat_centro,
            barrio_lon_centro,
            c["Latitude"],
            c["Longitude"],
        ),
        axis=1,
    )

    cc_seleccionado = centros.loc[centros["dist_al_barrio"].idxmin()]
    distancia_troncal = cc_seleccionado["dist_al_barrio"]

    km_internos = simular_kilometros_tsp(
        barrio_lat_centro,
        barrio_lon_centro,
        puntos_barrio,
    )

    resultados = []

    # -------------------------------------------------------------------------
    # M1: Furgoneta combustión
    # -------------------------------------------------------------------------

    m1 = parametros["FURGONETA_CONV"]
    viajes_1 = int(np.ceil(num_paquetes / m1["capacidad"]))
    km_totales_1 = (distancia_troncal * 2 * viajes_1) + km_internos

    costo_1 = (
        km_totales_1 * m1["costo_km"]
        + (km_totales_1 / m1["v_media"]) * m1["costo_hora"]
    )

    co2_1 = (km_totales_1 * m1["co2_km"]) / 1000

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

    # -------------------------------------------------------------------------
    # M2: Furgoneta eléctrica
    # -------------------------------------------------------------------------

    m2 = parametros["FURGONETA_ELEC"]
    viajes_2 = int(np.ceil(num_paquetes / m2["capacidad"]))
    km_totales_2 = (distancia_troncal * 2 * viajes_2) + km_internos

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

    # -------------------------------------------------------------------------
    # M3: Microhub + bicicleta cargo
    # -------------------------------------------------------------------------

    m3 = parametros["BICICLETA_CARGO"]
    viajes_bike = int(np.ceil(num_paquetes / m3["capacidad"]))

    km_abastecimiento_hub = distancia_troncal * 2

    costo_camion_hub = (
        km_abastecimiento_hub * parametros["FURGONETA_CONV"]["costo_km"]
    )

    co2_camion_hub = (
        km_abastecimiento_hub * parametros["FURGONETA_CONV"]["co2_km"]
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

    # -------------------------------------------------------------------------
    # M4: PUDO + entrega a pie
    # -------------------------------------------------------------------------

    m4 = parametros["PUDO_A_PIE"]
    viajes_pie = int(np.ceil(num_paquetes / m4["capacidad"]))

    km_repartidor_pie = (
        puntos_barrio.apply(
            lambda r: calcular_haversine(
                barrio_lat_centro,
                barrio_lon_centro,
                r["Latitude"],
                r["Longitude"],
            ),
            axis=1,
        ).sum()
        * 2
    )

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

    # -------------------------------------------------------------------------
    # M5: PUDO + recogida cliente
    # -------------------------------------------------------------------------

    m5 = parametros["PUDO_CONSUMIDOR"]

    km_clientes_radial = km_repartidor_pie

    co2_clientes = (
        km_clientes_radial * m5["co2_km_estimado_cliente"]
    ) / 1000

    resultados.append({
        "ciudad": ciudad,
        "barrio": nombre_barrio,
        "modelo": "M5: CC -> PUDO -> Recogida Cliente",
        "centro_logistico": cc_seleccionado["Location"],
        "paquetes": num_paquetes,
        "distancia_troncal_km": distancia_troncal,
        "km_recorridos": km_abastecimiento_hub + km_clientes_radial,
        "numero_viajes": 1 + num_paquetes,
        "emisiones_co2_kg": co2_camion_hub + co2_clientes,
        "costo_total_eur": (
            costo_camion_hub
            + num_paquetes * m5["comision_pudo"]
        ),
    })

    return resultados


def simular_ciudad(ciudad: str, barrio_activo: str | None = None):
    puntos, centros, limites, parametros_df = cargar_datos_ciudad(ciudad)
    parametros = obtener_parametros(parametros_df)

    resultados_totales = []

    if barrio_activo is not None:
        limites = limites[
            limites["barrio"].str.lower() == barrio_activo.lower()
        ]

        if limites.empty:
            raise ValueError(f"No se encontró el barrio '{barrio_activo}' en {ciudad}.")

    for _, barrio in limites.iterrows():
        nombre_barrio = barrio["barrio"]

        print(f"\n📍 Simulando {ciudad.upper()} - {nombre_barrio}")

        puntos_barrio = filtrar_puntos_barrio(puntos, barrio)

        if len(puntos_barrio) == 0:
            print(f"⚠️ No hay puntos dentro de los límites de {nombre_barrio}.")
            continue

        resultados_barrio = simular_barrio(
            ciudad=ciudad,
            nombre_barrio=nombre_barrio,
            puntos_barrio=puntos_barrio,
            centros=centros,
            parametros=parametros,
        )

        resultados_totales.extend(resultados_barrio)

        print(f"✅ {nombre_barrio}: {len(puntos_barrio)} paquetes simulados")

    return pd.DataFrame(resultados_totales)


# =============================================================================
# 7. EJECUCIÓN
# =============================================================================

if __name__ == "__main__":

    CARPETA_RESULTADOS.mkdir(parents=True, exist_ok=True)

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