from __future__ import annotations

import re
import unicodedata
import pandas as pd
from code.common.paths import DATA_DIR, RAW_DATA_DIR
from pathlib import Path


RAW_DATA = RAW_DATA_DIR

POINTS_FILE = RAW_DATA / "Points B2C_20250402.xlsx"
CC_FILE = RAW_DATA / "CC.xlsx"

CITYPAQ_CANDIDATES_FILE = (
    RAW_DATA
    / "citypaq_candidates_for_import.csv"
)

OUTPUT_DIR = DATA_DIR

CITIES = {
    "City of Barcelona": "barcelona",
    "City of Madrid": "madrid",
    "City of Valencia": "valencia",
}

# PROBLEM: The current bounding boxes for neighborhoods are hardcoded and may not accurately represent the actual administrative boundaries of the neighborhoods.
# TODO: Update the bounding boxes to use actual polygonal boundaries of the neighborhoods, possibly by using a geospatial library (e.g., GeoPandas) and shapefiles or GeoJSON files that contain the real boundaries of the neighborhoods. 

NEIGHBORHOOD_BOUNDARIES = {
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

MODEL_PARAMETERS = [
    {"modelo": "FURGONETA_CONV", "costo_km": 0.45, "costo_hora": 18.0, "v_media": 22.0, "co2_km": 220.0, "capacidad": 60, "fijo_hub_dia": None, "comision_pudo": None, "co2_km_estimado_cliente": None},
    {"modelo": "FURGONETA_ELEC", "costo_km": 0.20, "costo_hora": 18.0, "v_media": 20.0, "co2_km": 0.0, "capacidad": 60, "fijo_hub_dia": None, "comision_pudo": None, "co2_km_estimado_cliente": None},
    {"modelo": "BICICLETA_CARGO", "costo_km": 0.05, "costo_hora": 14.0, "v_media": 14.0, "co2_km": 0.0, "capacidad": 20, "fijo_hub_dia": 45.0, "comision_pudo": None, "co2_km_estimado_cliente": None},
    {"modelo": "PUDO_A_PIE", "costo_km": 0.0, "costo_hora": 12.0, "v_media": 4.5, "co2_km": 0.0, "capacidad": 12, "fijo_hub_dia": None, "comision_pudo": 0.50, "co2_km_estimado_cliente": None},
    {"modelo": "PUDO_CONSUMIDOR", "costo_km": None, "costo_hora": None, "v_media": None, "co2_km": None, "capacidad": None, "fijo_hub_dia": None, "comision_pudo": 0.50, "co2_km_estimado_cliente": 25.0},
]


def normalize_text(text):
    if pd.isna(text):
        return pd.NA

    text = str(text).replace("\u200b", "")
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def text_key(text):
    if pd.isna(text):
        return pd.NA

    text = normalize_text(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.upper().strip()

    return text


def normalize_company(value):
    if pd.isna(value):
        return pd.NA

    key = text_key(value)

    company_map = {
        "AMAZON": "Amazon",
        "AMAZON LOCKER": "Amazon",
        "AMAZON HUB": "Amazon",
        "SEUR": "SEUR",
        "INPOST": "InPost",
        "CORREOS": "Correos",
        "CITYPAQ": "Correos",
        "CORREOS CITYPAQ": "Correos",
        "UPS": "UPS",
    }

    return company_map.get(key, normalize_text(value).title())


def normalize_type(value):
    if pd.isna(value):
        return pd.NA

    key = text_key(value)

    type_map = {
        "LOCKER": "Locker",
        "LOCKERS": "Locker",
        "LOCKER POINT": "Locker",
        "PUDO": "PUDO",
        "PUDO POINT": "PUDO",
        "PICK UP POINT": "PUDO",
        "PICK-UP POINT": "PUDO",
        "PICKUP POINT": "PUDO",
        "PARCEL SHOP": "PUDO",
        "SERVICE POINT": "PUDO",
        "DROP OFF POINT": "PUDO",
        "DROP-OFF POINT": "PUDO",
    }

    return type_map.get(key, normalize_text(value).title())


def normalize_infrastructure(value):
    if pd.isna(value):
        return pd.NA

    key = text_key(value)

    infrastructure_map = {
        "PUBLICA": "Pública",
        "PUBLIC": "Pública",
        "PRIVADA": "Privada",
        "PRIVATE": "Privada",
        "MIXTA": "Mixta",
        "MIXED": "Mixta",
    }

    return infrastructure_map.get(key, normalize_text(value).title())


def normalize_city(value):
    if pd.isna(value):
        return pd.NA

    key = text_key(value)

    city_map = {
        "BARCELONA": "Barcelona",
        "BCN": "Barcelona",
        "MADRID": "Madrid",
        "VALENCIA": "Valencia",
        "VALENCIA/VALENCIA": "Valencia",
        "VALENCIA VALENCIA": "Valencia",
    }

    return city_map.get(key, normalize_text(value).title())


def normalize_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    text_cols = [
        "Company",
        "Type",
        "Infrastructure",
        "Type Infrastructure",
        "City",
        "Location",
        "Address",
        "Zip Code",
    ]

    for col in text_cols:
        if col in df.columns:
            df[f"{col}_raw"] = df[col]
            df[col] = df[col].apply(normalize_text)

    if "Company" in df.columns:
        df["Company"] = df["Company"].apply(normalize_company)

    if "Type" in df.columns:
        df["Type"] = df["Type"].apply(normalize_type)

    if "Infrastructure" in df.columns:
        df["Infrastructure"] = df["Infrastructure"].apply(normalize_infrastructure)

    if "Type Infrastructure" in df.columns:
        df["Type Infrastructure"] = df["Type Infrastructure"].apply(normalize_infrastructure)

    if "City" in df.columns:
        df["City"] = df["City"].apply(normalize_city)

    return df


def add_id(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "ID" not in df.columns:
        df.insert(0, "ID", range(1, len(df) + 1))

    return df


def find_sheet(excel: pd.ExcelFile, target_name: str) -> str:
    target = normalize_text(target_name)

    for sheet in excel.sheet_names:
        if normalize_text(sheet) == target:
            return sheet

    raise ValueError(
        f"Sheet not found '{target_name}'. "
        f"Available sheets: {excel.sheet_names}"
    )


def clean_dataframe(df: pd.DataFrame, normalize_fields: bool = True) -> pd.DataFrame:
    df = df.copy()

    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df = df.dropna(axis=1, how="all")

    df.columns = [normalize_text(c) for c in df.columns]

    if normalize_fields:
        df = normalize_text_columns(df)

    for col in ["Latitude", "Longitude"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            )

    if {"Latitude", "Longitude"}.issubset(df.columns):
        df = df.dropna(subset=["Latitude", "Longitude"])

    return df.reset_index(drop=True)


def validate_files() -> None:
    if not POINTS_FILE.exists():
        raise FileNotFoundError(f"Points file not found: {POINTS_FILE}")

    if not CC_FILE.exists():
        raise FileNotFoundError(f"Centers file not found: {CC_FILE}")


def export_city_sheets(
    excel_file: Path,
    output_dir: Path,
    csv_name: str,
    deduplicate: bool = True,
):
    excel = pd.ExcelFile(excel_file)

    for base_sheet, city_slug in CITIES.items():
        sheet_name = find_sheet(excel, base_sheet)

        df = pd.read_excel(
            excel_file,
            sheet_name=sheet_name,
            header=1,
        )

        df = clean_dataframe(df)

        if deduplicate:
            duplicate_columns = [
                col for col in ["Company", "Type", "Address", "Latitude", "Longitude"]
                if col in df.columns
            ]

            if duplicate_columns:
                df = df.drop_duplicates(subset=duplicate_columns).reset_index(drop=True)

        prefix = Path(csv_name).stem.upper()

        df = add_id(df)

        city_dir = output_dir / city_slug
        city_dir.mkdir(parents=True, exist_ok=True)

        output_file = city_dir / csv_name
        df.to_csv(output_file, index=False, encoding="utf-8-sig")

        print(f"{output_file} | {len(df):,} rows")

def include_citypaq_candidates(output_dir: Path) -> None:
    if not CITYPAQ_CANDIDATES_FILE.exists():
        print(
            "CityPaq candidates file not found. "
            "Candidate inclusion will be skipped."
        )
        return

    candidates = pd.read_csv(CITYPAQ_CANDIDATES_FILE)

    city_map = {
        "Barcelona": "barcelona",
        "Madrid": "madrid",
        "València": "valencia",
        "Valencia": "valencia",
    }

    for source_city, city_slug in city_map.items():
        city_candidates = candidates[
            candidates["City"] == source_city
        ].copy()

        if city_candidates.empty:
            continue

        output_file = output_dir / city_slug / "puntos_b2c.csv"

        if not output_file.exists():
            print(
                f"File does not exist: {output_file}; "
                "candidates will not be included."
            )
            continue

        base = pd.read_csv(output_file)

        city_candidates = normalize_text_columns(
            city_candidates
        )

        base_columns = list(base.columns)

        for col in base_columns:
            if col not in city_candidates.columns:
                city_candidates[col] = pd.NA

        for col in city_candidates.columns:
            if col not in base.columns:
                base[col] = pd.NA

        city_candidates = city_candidates[base.columns]

        combined = pd.concat(
            [base, city_candidates],
            ignore_index=True,
        )

        combined["ID"] = range(1, len(combined) + 1)

        combined.to_csv(
            output_file,
            index=False,
            encoding="utf-8-sig",
        )

        print(
            f"{output_file} | "
            f"{len(city_candidates)} CityPaq candidates included"
        )

def export_b2c_summary(output_dir: Path) -> None:
    records = []

    for city_slug in CITIES.values():
        file_path = output_dir / city_slug / "puntos_b2c.csv"

        if not file_path.exists():
            continue

        df = pd.read_csv(file_path)

        if {"Company", "Type"}.issubset(df.columns):
            summary = (
                df.groupby(["Company", "Type"])
                .size()
                .reset_index(name="count")
            )
            summary["city"] = city_slug
            records.append(summary)

    if not records:
        return

    total_summary = pd.concat(records, ignore_index=True)

    output_file = output_dir / "resumen_b2c_por_company_type.csv"
    total_summary.to_csv(output_file, index=False, encoding="utf-8-sig")

    print(f"{output_file} | {len(total_summary):,} rows")


def export_neighborhood_boundaries(output_dir: Path) -> None:
    for city_slug, neighborhoods in NEIGHBORHOOD_BOUNDARIES.items():
        city_dir = output_dir / city_slug
        city_dir.mkdir(parents=True, exist_ok=True)

        output_file = city_dir / "limites_barrios.csv"
        pd.DataFrame(neighborhoods).to_csv(output_file, index=False, encoding="utf-8-sig")

        print(f"{output_file} | {len(neighborhoods):,} neighborhoods")


def export_model_parameters(output_dir: Path) -> None:
    output_file = output_dir / "parametros_modelos.csv"

    pd.DataFrame(MODEL_PARAMETERS).to_csv(
        output_file,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"{output_file} | {len(MODEL_PARAMETERS):,} models")


def prepare_data() -> None:
    validate_files()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\nExporting B2C points...")
    export_city_sheets(
        excel_file=POINTS_FILE,
        output_dir=OUTPUT_DIR,
        csv_name="puntos_b2c.csv",
        deduplicate=False,
    )
    print("\nIncluding CityPaq candidates...")
    include_citypaq_candidates(OUTPUT_DIR)

    print("\nExporting CC centers...")
    export_city_sheets(
        excel_file=CC_FILE,
        output_dir=OUTPUT_DIR,
        csv_name="centros_cc.csv",
    )

    print("\nExporting B2C summary by company and type...")
    export_b2c_summary(OUTPUT_DIR)

    print("\nExporting current neighborhood boundaries...")
    export_neighborhood_boundaries(OUTPUT_DIR)

    print("\nExporting model parameters...")
    export_model_parameters(OUTPUT_DIR)

    print("\nData preparation completed.")
    print(f"Data generated in: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    prepare_data()