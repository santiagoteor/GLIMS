from pathlib import Path
import re
import unicodedata
from code.common.paths import PROJECT_ROOT, RESULTS_DIR
from code.common.text_utils import normalize_text, text_similarity
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree
from difflib import SequenceMatcher


MASTER_FILE = PROJECT_ROOT / "raw_data" / "Points B2C_20250402.xlsx"

CITYPAQ_FILES = {
    "Barcelona": PROJECT_ROOT / "raw_data" / "Barcelona Citypaq.csv",
    "Madrid": PROJECT_ROOT / "raw_data" / "Madrid Citypaq.csv",
    "Valencia": PROJECT_ROOT / "raw_data" / "Valencia Citypaq.csv",
}

OUTPUT_DIR = RESULTS_DIR / "citypaq_source_comparison"

DISTANCE_THRESHOLD_METERS = 50
EARTH_RADIUS_METERS = 6_371_000


def normalize_company(value) -> str:
    text = normalize_text(value)

    company_map = {
        "CORREOS CITYPAQ": "CORREOS",
        "CITYPAQ": "CORREOS",
        "CORREOS": "CORREOS",
        "CORREOS EXPRESS": "CORREOS EXPRESS",
    }

    return company_map.get(text, text)

def extract_branch_id(value) -> str:
    text = normalize_text(value)

    patterns = [
        r"\bSUC\s*(\d+)\b",
        r"\bSUCURSAL\s*(\d+)\b",
        r"\bSUC\s+([A-Z]+)\b",
        r"\bSUCURSAL\s+([A-Z]+)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            return match.group(1)

    if re.search(r"\bMADRID\s+OP\b", text):
        return "OP"

    return ""

def prepare_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["Latitude_num"] = pd.to_numeric(
        df["Latitude"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    df["Longitude_num"] = pd.to_numeric(
        df["Longitude"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    )

    return df.dropna(
        subset=["Latitude_num", "Longitude_num"]
    ).reset_index(drop=True)


def find_city_sheet(excel: pd.ExcelFile, city: str) -> str:
    city_key = normalize_text(city)

    for sheet in excel.sheet_names:
        if city_key in normalize_text(sheet):
            return sheet

    raise ValueError(f"No se encontró una hoja para {city}")


def compare_city(
    city: str,
    citypaq_file: Path,
    excel: pd.ExcelFile,
) -> pd.DataFrame:
    sheet = find_city_sheet(excel, city)

    master = pd.read_excel(
        MASTER_FILE,
        sheet_name=sheet,
        header=1,
    )

    master = master.loc[
        :,
        ~master.columns.astype(str).str.startswith("Unnamed"),
    ]

    citypaq = pd.read_csv(citypaq_file)

    master = prepare_coordinates(master)
    citypaq = prepare_coordinates(citypaq)

    master["Company_Normalized"] = master["Company"].apply(
        normalize_company
    )

    citypaq["Company_Normalized"] = citypaq["Company"].apply(
        normalize_company
    )

    source_companies = set(
        citypaq["Company_Normalized"].dropna().unique()
    )

    master_same_company = master[
        master["Company_Normalized"].isin(source_companies)
    ].reset_index(drop=True)

    if master_same_company.empty:
        raise ValueError(
            f"No hay registros de la misma empresa en el maestro para {city}"
        )

    master_coords = np.radians(
        master_same_company[
            ["Latitude_num", "Longitude_num"]
        ].to_numpy()
    )

    citypaq_coords = np.radians(
        citypaq[
            ["Latitude_num", "Longitude_num"]
        ].to_numpy()
    )

    tree = BallTree(master_coords, metric="haversine")

    distances, indices = tree.query(
        citypaq_coords,
        k=1,
    )

    rows = []

    for citypaq_index, (distance_array, index_array) in enumerate(
        zip(distances, indices)
    ):
        source_row = citypaq.iloc[citypaq_index]

        master_index = int(index_array[0])
        distance_m = (
            float(distance_array[0]) * EARTH_RADIUS_METERS
        )

        master_row = master_same_company.iloc[master_index]

        same_address = (
            normalize_text(source_row.get("Address"))
            == normalize_text(master_row.get("Address"))
        )
        address_similarity = text_similarity(
            source_row.get("Address"),
            master_row.get("Address"),
        )

        location_similarity = text_similarity(
            source_row.get("Location"),
            master_row.get("Location"),
        )
        
        source_branch_id = extract_branch_id(
        source_row.get("Location")
        )

        master_branch_id = extract_branch_id(
            master_row.get("Location")
        )

        same_branch_id = (
            bool(source_branch_id)
            and bool(master_branch_id)
            and source_branch_id == master_branch_id
        )
        
        analysis_city_normalized = normalize_text(city)
        source_city_normalized = normalize_text(
            source_row.get("City")
        )

        city_aliases = {
            "BARCELONA": {"BARCELONA"},
            "MADRID": {"MADRID"},
            "VALENCIA": {"VALENCIA", "VALENCIA"},
        }

        same_city = (
            source_city_normalized
            in city_aliases.get(
                analysis_city_normalized,
                {analysis_city_normalized},
            )
        )
        if not same_city:
            classification = "Outside analysis city"
        elif same_branch_id and distance_m <= 50:
            classification = "Existing branch, probable duplicate"
        elif distance_m <= 50:
            classification = "Manual review"
        else:
            classification = "Possibly new"
        rows.append(
            {
                "Analysis_City": city,
                "Source_City": source_row.get("City"),
                "CityPaq_Row": citypaq_index + 1,
                "CityPaq_Company": source_row.get("Company"),
                "CityPaq_Location": source_row.get("Location"),
                "CityPaq_Address": source_row.get("Address"),
                "CityPaq_Latitude": source_row.get("Latitude_num"),
                "CityPaq_Longitude": source_row.get("Longitude_num"),
                "Match_Type": "Nearest same company",
                "Nearest_Master_Row": master_index + 1,
                "Master_Company": master_row.get("Company"),
                "Master_Location": master_row.get("Location"),
                "Master_Address": master_row.get("Address"),
                "Master_Latitude": master_row.get("Latitude_num"),
                "Master_Longitude": master_row.get("Longitude_num"),
                "Distance_m": round(distance_m, 2),
                "Same_Company": True,
                "Same_Address": same_address,
                "Address_Similarity": address_similarity,
                "Location_Similarity": location_similarity,
                "CityPaq_Branch_ID": source_branch_id,
                "Master_Branch_ID": master_branch_id,
                "Same_Branch_ID": same_branch_id,
                "Same_City": same_city,
                "Classification": classification,
                "Within_50m": (
                    distance_m <= DISTANCE_THRESHOLD_METERS
                ),
            }
        )

    return pd.DataFrame(rows).sort_values(
        by="Distance_m",
        ascending=True,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    excel = pd.ExcelFile(MASTER_FILE)

    for city, citypaq_file in CITYPAQ_FILES.items():
        report = compare_city(
            city=city,
            citypaq_file=citypaq_file,
            excel=excel,
        )

        output_file = OUTPUT_DIR / (
            f"{city.lower()}_citypaq_vs_master.csv"
        )

        report.to_csv(
            output_file,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"\n{city}")
        print(f"Registros CityPaq: {len(report)}")
        print(
            f"A menos de 50 m: "
            f"{int(report['Within_50m'].sum())}"
        )
        print(
            f"Distancia mínima: "
            f"{report['Distance_m'].min():.2f} m"
        )
        print(f"Reporte: {output_file}")


if __name__ == "__main__":
    main()