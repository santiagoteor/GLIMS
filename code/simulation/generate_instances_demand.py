"""
Generador de instancias de demanda de e-commerce para Barcelona, a partir
de direcciones postales reales ponderadas por poblacion real de cada
seccion censal, replicando los escenarios de demanda de Castillo et al.
(2024), "Towards greener city logistics".

Enfoque:
  - Las direcciones (adreces_postals_elementals.csv) traen distrito y
    seccion LOCAL al distrito (secc_cens). El padron de poblacion
    (2026_pad_mdbas.csv) usa un codigo de seccion COMPUESTO
    (Seccio_Censal = Codi_Districte * 1000 + seccion local). Para poder
    cruzar ambos archivos, se reconstruye ese mismo codigo compuesto en
    el archivo de direcciones: seccio_censal = districte * 1000 + secc_cens.

  - A cada direccion se le asigna la poblacion real de su seccion censal
    (columna Valor del padron). Como varias direcciones comparten la
    misma seccion, el peso de muestreo de cada direccion es:

        peso_direccion = poblacion_seccion / nº direcciones en esa seccion

    De este modo, la suma de pesos de todas las direcciones de una
    seccion es igual a la poblacion real de esa seccion (ni mas ni
    menos), independientemente de cuantos portales tenga registrados.

  - Los clientes candidatos se muestrean sin reemplazo usando ese peso
    (en vez de muestreo uniforme), asi que las zonas mas pobladas
    generan proporcionalmente mas clientes candidatos.

  - NOTA: esta version NO excluye direcciones cercanas a micro-hubs /
    puntos de recogida (se pidio explicitamente no aplicar ese filtro).

  - La demanda de cada cliente (paquetes/dia) NO usa los rangos del
    articulo original (5-25 / 50-100 / 100-200), porque esos valores
    estaban pensados para nodos agregados. Aqui cada cliente es una
    parada real (un portal/direccion concreta), asi que se usan rangos
    de demanda por parada individual:
        baja:   1-2 paquetes/dia   (dia con pocos pedidos)
        media:  2-5 paquetes/dia   (dia normal)
        alta:   5-10 paquetes/dia  (pico de e-commerce)
    La poblacion decide DONDE aparecen los clientes, no CUANTO pide
    cada uno.

  - Se generan instancias anidadas para 100, 200, 400, 600, 800 y 1000
    clientes (los primeros n del pool maestro ya ponderado).

Requisitos:
    pip install pandas geopandas shapely numpy --break-system-packages
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ---------------------------------------------------------------------
# Rutas (relativas a la estructura de carpetas de GLIMS)
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent          # GLIMS/code/simulation
GLIMS_DIR = BASE_DIR.parent.parent                  # GLIMS
RAW_DATA_DIR = GLIMS_DIR / "raw_data"
RESULTS_DIR = GLIMS_DIR / "results" / "barcelona" / "demand"

ADDRESSES_PATH = RAW_DATA_DIR / "adreces_postals_elementals.csv"
POPULATION_PATH = RAW_DATA_DIR / "2026_pad_mdbas.csv"

# --- Columnas del csv de direcciones ---
ADDR_X_COL = "x_etrs89"
ADDR_Y_COL = "y_etrs89"
ADDR_CP_COL = "dist_post"
ADDR_DISTRICTE_COL = "districte"
ADDR_BARRI_COL = "barri"
ADDR_SECC_CENS_COL = "secc_cens"  # seccion LOCAL al distrito

# --- Columnas del csv de poblacion (2026_pad_mdbas.csv, separado por ";") ---
POP_SEP = ";"
POP_DATE_COL = "Data_Referencia"
POP_SECCIO_COL = "Seccio_Censal"  # codigo COMPUESTO: districte*1000 + seccion local
POP_VALOR_COL = "Valor"           # poblacion de esa seccion

RANDOM_SEED = 42

# Rangos de demanda por parada individual (paquetes/dia por cliente).
# No son los rangos del articulo original (esos eran para nodos
# agregados) -- aqui cada cliente es un portal/direccion real, asi que
# se usan volumenes propios de una parada de reparto de ultima milla.
DEMAND_SCENARIOS = {
    "low": (1, 2),
    "medium": (2, 5),
    "high": (5, 10),
}

CUSTOMER_COUNTS = [100, 200, 400, 600, 800, 1000]

METRIC_CRS = "EPSG:25831"  # ETRS89 / UTM 31N, igual que x_etrs89 / y_etrs89
GEOGRAPHIC_CRS = "EPSG:4326"

rng = np.random.default_rng(RANDOM_SEED)


# ---------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------

def load_addresses(path: Path) -> gpd.GeoDataFrame:
    df = pd.read_csv(path)
    df = df.dropna(subset=[ADDR_X_COL, ADDR_Y_COL, ADDR_DISTRICTE_COL, ADDR_SECC_CENS_COL]).copy()
    geometry = [Point(xy) for xy in zip(df[ADDR_X_COL], df[ADDR_Y_COL])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=METRIC_CRS)
    return gdf


def load_population(path: Path) -> pd.DataFrame:
    """Carga el padron y se queda solo con la fecha de referencia mas reciente."""
    df = pd.read_csv(path, sep=POP_SEP)
    df[POP_DATE_COL] = pd.to_datetime(df[POP_DATE_COL], dayfirst=True, errors="coerce")
    latest_date = df[POP_DATE_COL].max()
    df_latest = df.loc[df[POP_DATE_COL] == latest_date].copy()
    print(f"  Usando fecha de referencia del padron: {latest_date.date()}")

    df_latest = df_latest.groupby(POP_SECCIO_COL, as_index=False)[POP_VALOR_COL].sum()
    return df_latest[[POP_SECCIO_COL, POP_VALOR_COL]]


# ---------------------------------------------------------------------
# Cruce con poblacion real y calculo de pesos de muestreo
# ---------------------------------------------------------------------

def add_seccio_censal_key(addresses: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reconstruye el codigo compuesto seccio_censal = districte*1000 + secc_cens."""
    out = addresses.copy()
    out["seccio_censal"] = (
        out[ADDR_DISTRICTE_COL].astype(int) * 1000 + out[ADDR_SECC_CENS_COL].astype(int)
    )
    return out


def merge_population(addresses: gpd.GeoDataFrame, population: pd.DataFrame) -> gpd.GeoDataFrame:
    """Cruza direcciones con poblacion real por seccio_censal."""
    merged = addresses.merge(
        population, left_on="seccio_censal", right_on=POP_SECCIO_COL, how="left"
    )
    merged = merged.rename(columns={POP_VALOR_COL: "poblacion_seccion"})

    n_sin_poblacion = merged["poblacion_seccion"].isna().sum()
    if n_sin_poblacion > 0:
        print(
            f"  [aviso] {n_sin_poblacion} direcciones no encontraron seccion censal "
            "en el padron y se descartan del pool de clientes candidatos"
        )
    merged = merged.dropna(subset=["poblacion_seccion"]).copy()

    n_secciones_padron = population[POP_SECCIO_COL].nunique()
    n_secciones_con_direcciones = merged["seccio_censal"].nunique()
    n_secciones_sin_direcciones = n_secciones_padron - n_secciones_con_direcciones
    if n_secciones_sin_direcciones > 0:
        print(
            f"  [aviso] {n_secciones_sin_direcciones} secciones del padron no tienen "
            "ninguna direccion candidata (no aportan clientes)"
        )
    return merged


def compute_sampling_weights(addresses: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    peso_direccion = poblacion_seccion / nº direcciones candidatas en esa seccion.
    La suma de pesos dentro de una seccion es igual a su poblacion real.
    """
    out = addresses.copy()
    n_por_seccion = out.groupby("seccio_censal")["seccio_censal"].transform("count")
    out["peso_muestreo"] = out["poblacion_seccion"] / n_por_seccion

    out = out[(out["peso_muestreo"] > 0) & out["peso_muestreo"].notna()].copy()
    return out


# ---------------------------------------------------------------------
# Muestreo de clientes candidatos (ponderado por poblacion real)
# ---------------------------------------------------------------------

def sample_master_customers(addresses: gpd.GeoDataFrame, total: int) -> pd.DataFrame:
    if total > len(addresses):
        raise ValueError(
            f"Se pidieron {total} clientes pero solo quedan {len(addresses)} "
            "direcciones disponibles con peso valido."
        )

    weights = addresses["peso_muestreo"].to_numpy()
    probs = weights / weights.sum()

    sampled_idx = rng.choice(addresses.index.to_numpy(), size=total, replace=False, p=probs)
    sample = addresses.loc[sampled_idx].copy()
    sample = sample.sample(frac=1, random_state=RANDOM_SEED).reset_index(drop=True)

    sample_wgs84 = sample.to_crs(GEOGRAPHIC_CRS)

    out = pd.DataFrame(
        {
            "customer_id": range(len(sample)),
            "codi_carrer": sample.get("codi_carrer"),
            "numpost": sample.get("numpost"),
            "lletra": sample.get("lletra"),
            "districte": sample.get(ADDR_DISTRICTE_COL),
            "barri": sample.get(ADDR_BARRI_COL),
            "seccio_censal": sample.get("seccio_censal"),
            "poblacion_seccion": sample.get("poblacion_seccion"),
            "postal_code": sample.get(ADDR_CP_COL),
            "lon": sample_wgs84.geometry.x.values,
            "lat": sample_wgs84.geometry.y.values,
            "x_etrs89": sample.geometry.x.values,
            "y_etrs89": sample.geometry.y.values,
        }
    )
    return out


# ---------------------------------------------------------------------
# Asignacion de demanda (por parada individual, sin relacion con la poblacion)
# ---------------------------------------------------------------------

def assign_demand(customers_df: pd.DataFrame, scenario: str) -> pd.DataFrame:
    low, high = DEMAND_SCENARIOS[scenario]
    df = customers_df.copy()
    df["demand"] = rng.integers(low, high + 1, size=len(df))
    df["scenario"] = scenario
    return df


def build_instances(master_df: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    for n in CUSTOMER_COUNTS:
        if n > len(master_df):
            print(f"[aviso] se pidieron {n} clientes pero el pool maestro solo tiene {len(master_df)}")
            continue

        subset = master_df.iloc[:n].copy()  # subconjunto anidado

        for scenario in DEMAND_SCENARIOS:
            instance = assign_demand(subset, scenario)
            instance["instance_size"] = n

            # Columnas finales: id, ubicacion (lat/lon) y demanda de paquetes
            cols = [
                "customer_id",
                "lon",
                "lat",
                "demand",
                "scenario",
                "instance_size",
                "districte",
                "barri",
                "seccio_censal",
                "poblacion_seccion",
                "postal_code",
                "codi_carrer",
                "numpost",
                "lletra",
                "x_etrs89",
                "y_etrs89",
            ]
            instance = instance[cols]

            fname = output_dir / f"demand_{scenario}_{n}.csv"
            instance.to_csv(fname, index=False)
            total_demand = instance["demand"].sum()
            print(f"Guardado: {fname} | {n} clientes | demanda total = {total_demand}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> None:
    print("Cargando direcciones postales reales...")
    addresses = load_addresses(ADDRESSES_PATH)
    print(f"  {len(addresses)} direcciones cargadas")

    print("Reconstruyendo el codigo de seccion censal compuesto (districte*1000+secc_cens)...")
    addresses = add_seccio_censal_key(addresses)

    print("Cargando poblacion por seccion censal...")
    population = load_population(POPULATION_PATH)
    print(f"  {len(population)} secciones censales con poblacion")

    print("Cruzando direcciones con poblacion real...")
    addresses = merge_population(addresses, population)
    print(f"  {len(addresses)} direcciones con poblacion de seccion asignada")

    print("Calculando pesos de muestreo (poblacion repartida entre direcciones de cada seccion)...")
    addresses = compute_sampling_weights(addresses)
    print(f"  {len(addresses)} direcciones con peso de muestreo valido")

    n_max = max(CUSTOMER_COUNTS)
    print(f"Muestreando pool maestro de {n_max} clientes (ponderado por poblacion real)...")
    master = sample_master_customers(addresses, n_max)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    master_path = RESULTS_DIR / f"master_customers_{n_max}.csv"
    master.to_csv(master_path, index=False)
    print(f"Pool maestro guardado en: {master_path}")

    print("Generando instancias por escenario y tamano...")
    build_instances(master, RESULTS_DIR)

    print("Listo.")


if __name__ == "__main__":
    main()
