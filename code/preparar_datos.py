from __future__ import annotations

import re
import unicodedata
from pathlib import Path
import pandas as pd

# PROBLEM: The current code uses hardcoded absolute paths for the raw data and output directories, which makes it non-portable and difficult to reproduce on different machines or environments. 
# TODO: Update the code to use relative paths or configurable paths (e.g., through environment variables or a configuration file) for the raw data and output directories. 

# Carpeta de los Excel originales
RAW_DATA = Path("E:/UPV/Proyectos/GLIMS/raw_data")

# Nombre exacto del Excel de puntos B2C
ARCHIVO_PUNTOS = RAW_DATA / "Points B2C_20250402.xlsx"

# Nombre exacto del Excel de centros CC
ARCHIVO_CC = RAW_DATA / "CC.xlsx"

# Carpeta donde los CSVs
CARPETA_SALIDA = Path("E:/UPV/Proyectos/GLIMS/data")

CIUDADES = {
    "City of Barcelona": "barcelona",
    "City of Madrid": "madrid",
    "City of Valencia": "valencia",
}

# PROBLEM: The current bounding boxes for neighborhoods are hardcoded and may not accurately represent the actual administrative boundaries of the neighborhoods.
# TODO: Update the bounding boxes to use actual polygonal boundaries of the neighborhoods, possibly by using a geospatial library (e.g., GeoPandas) and shapefiles or GeoJSON files that contain the real boundaries of the neighborhoods. 

LIMITES_BARRIOS = {
    "barcelona": [
        {"barrio": "Eixample", "lat_min": 41.380, "lat_max": 41.405, "lon_min": 2.145, "lon_max": 2.175},
        {"barrio": "Ciutat Vella", "lat_min": 41.370, "lat_max": 41.390, "lon_min": 2.160, "lon_max": 2.190},
        {"barrio": "El Carmel", "lat_min": 41.415, "lat_max": 41.435, "lon_min": 2.145, "lon_max": 2.165},
    ],
    "madrid": [
        {"barrio": "Lavapiés", "lat_min": 40.405, "lat_max": 40.415, "lon_min": -3.708, "lon_max": -3.693},
        {"barrio": "Moratalaz", "lat_min": 40.400, "lat_max": 40.420, "lon_min": -3.660, "lon_max": -3.630},
        {"barrio": "El Pardo", "lat_min": 40.500, "lat_max": 40.550, "lon_min": -3.800, "lon_max": -3.750},
    ],
    "valencia": [
        {"barrio": "Benicalap", "lat_min": 39.485, "lat_max": 39.505, "lon_min": -0.400, "lon_max": -0.380},
        {"barrio": "Camins al Grau", "lat_min": 39.455, "lat_max": 39.475, "lon_min": -0.360, "lon_max": -0.330},
        {"barrio": "La Punta", "lat_min": 39.430, "lat_max": 39.455, "lon_min": -0.350, "lon_max": -0.320},
    ],
}

# PROBLEM: The current parameters for vehicle capacity and hub capacity are inconsistent with the project specifications. 
# The code uses a vehicle capacity of 60 packages per van, while the project documentation specifies a fixed daily capacity of 300 packages and a microhub area of 50 m², which corresponds to approximately 1,500 packages. 
# This discrepancy can lead to unrealistic simulations and results.
# TODO: Update the vehicle capacity and hub capacity parameters to be consistent with the project specifications.

# PROBLEM: The current CO2 emissions factors for diesel, electric, and bicycle vehicles are not well-documented and may not accurately reflect the actual emissions associated with each vehicle type. 
# The code uses a value of 220 g CO2/km for diesel vehicles, 0 g CO2/km for electric vehicles and bicycles, and 25 g CO2/km for customer trips, without providing references or clarifying whether these values represent tank-to-wheel (TTW) 
# or well-to-wheel (WTW) emissions.
# TODO: Update the CO2 emissions factors to be based on credible sources and provide clear documentation on whether the values represent TTW or WTW emissions. 
# Consider using lifecycle assessment (LCA) data or official emissions factors from recognized organizations (e.g., European Environment Agency, U.S. Environmental Protection Agency) to ensure accuracy and transparency.

# PROBLEM: The current cost parameters for vehicle operation and PUDO commission are based on assumptions without supporting evidence.
# TODO: Update the cost parameters to be based on credible sources or provide a range of values for sensitivity analysis. 
# Consider using industry reports, academic studies, or official statistics to inform the cost estimates and ensure that they are realistic and justifiable.

# PROBLEM: The current implementation uses hardcoded parameters for vehicle models, costs, speeds, CO2 emissions, and capacities in the code.
# TODO: Refactor the code to read these parameters from an external configuration file (e.g., JSON, YAML, or CSV) to allow for easier updates and maintenance.

PARAMETROS_MODELOS = [
    {"modelo": "FURGONETA_CONV",
     "costo_km": 0.45, 
     "costo_hora": 18.0, 
     "v_media": 22.0, 
     "co2_km": 220.0, 
     "capacidad": 60, 
     "fijo_hub_dia": None, 
     "comision_pudo": None, 
     "co2_km_estimado_cliente": None},
    
    {"modelo": "FURGONETA_ELEC",
     "costo_km": 0.20, 
     "costo_hora": 18.0, 
     "v_media": 20.0,
     "co2_km": 0.0,
     "capacidad": 60,
     "fijo_hub_dia": None,
     "comision_pudo": None,
     "co2_km_estimado_cliente": None},
    
    {"modelo": "BICICLETA_CARGO",
     "costo_km": 0.05,
     "costo_hora": 14.0,
     "v_media": 14.0,
     "co2_km": 0.0,
     "capacidad": 20,
     "fijo_hub_dia": 45.0,
     "comision_pudo": None,
     "co2_km_estimado_cliente": None},
    
    {"modelo": "PUDO_A_PIE",
     "costo_km": 0.0,
     "costo_hora": 12.0,
     "v_media": 4.5,
     "co2_km": 0.0,
     "capacidad": 12,
     "fijo_hub_dia": None,
     "comision_pudo": 0.50,
     "co2_km_estimado_cliente": None},
    
    {"modelo": "PUDO_CONSUMIDOR",
     "costo_km": None,
     "costo_hora": None,
     "v_media": None,
     "co2_km": None,
     "capacidad": None,
     "fijo_hub_dia": None,
     "comision_pudo": 0.50,
     "co2_km_estimado_cliente": 25.0},
]

def normalizar_texto(texto: str) -> str:
    texto = str(texto).replace("\u200b", "")
    texto = unicodedata.normalize("NFKC", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def encontrar_hoja(excel: pd.ExcelFile, nombre_objetivo: str) -> str:
    objetivo = normalizar_texto(nombre_objetivo)

    for hoja in excel.sheet_names:
        if normalizar_texto(hoja) == objetivo:
            return hoja

    raise ValueError(
        f"No se encontró la hoja '{nombre_objetivo}'. "
        f"Hojas disponibles: {excel.sheet_names}"
    )


def limpiar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df = df.dropna(axis=1, how="all")

    df.columns = [normalizar_texto(c) for c in df.columns]

    for col in ["Latitude", "Longitude"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            )

    if {"Latitude", "Longitude"}.issubset(df.columns):
        df = df.dropna(subset=["Latitude", "Longitude"])

    return df.reset_index(drop=True)


def validar_archivos() -> None:
    if not ARCHIVO_PUNTOS.exists():
        raise FileNotFoundError(f"No se encontró el archivo de puntos: {ARCHIVO_PUNTOS}")

    if not ARCHIVO_CC.exists():
        raise FileNotFoundError(f"No se encontró el archivo de centros: {ARCHIVO_CC}")


def exportar_hojas_por_ciudad(
    archivo_excel: Path,
    salida: Path,
    nombre_csv: str,
):
    excel = pd.ExcelFile(archivo_excel)

    for hoja_base, ciudad_slug in CIUDADES.items():
        hoja_real = encontrar_hoja(excel, hoja_base)

        df = pd.read_excel(
            archivo_excel,
            sheet_name=hoja_real,
            header=1,
        )

        df = limpiar_dataframe(df)

        carpeta_ciudad = salida / ciudad_slug
        carpeta_ciudad.mkdir(parents=True, exist_ok=True)

        destino = carpeta_ciudad / nombre_csv
        df.to_csv(destino, index=False, encoding="utf-8-sig")

        print(f"{destino} | {len(df):,} filas")


def exportar_limites_barrios(salida: Path) -> None:
    for ciudad_slug, barrios in LIMITES_BARRIOS.items():
        carpeta_ciudad = salida / ciudad_slug
        carpeta_ciudad.mkdir(parents=True, exist_ok=True)

        destino = carpeta_ciudad / "limites_barrios.csv"
        pd.DataFrame(barrios).to_csv(destino, index=False, encoding="utf-8-sig")

        print(f"{destino} | {len(barrios):,} barrios")


def exportar_parametros(salida: Path) -> None:
    destino = salida / "parametros_modelos.csv"

    pd.DataFrame(PARAMETROS_MODELOS).to_csv(
        destino,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"{destino} | {len(PARAMETROS_MODELOS):,} modelos")


def preparar_datos() -> None:
    validar_archivos()

    CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)

    print("\nExportando puntos B2C...")
    exportar_hojas_por_ciudad(
        archivo_excel=ARCHIVO_PUNTOS,
        salida=CARPETA_SALIDA,
        nombre_csv="puntos_b2c.csv",
    )

    print("\nExportando centros CC...")
    exportar_hojas_por_ciudad(
        archivo_excel=ARCHIVO_CC,
        salida=CARPETA_SALIDA,
        nombre_csv="centros_cc.csv",
    )

    print("\nExportando límites actuales de barrios...")
    exportar_limites_barrios(CARPETA_SALIDA)

    print("\nExportando parámetros de modelos...")
    exportar_parametros(CARPETA_SALIDA)

    print("\nPreparación completada.")
    print(f"Datos generados en: {CARPETA_SALIDA.resolve()}")

if __name__ == "__main__":
    preparar_datos()