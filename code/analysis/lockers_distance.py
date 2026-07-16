from pathlib import Path
import re
import unicodedata
from code.common.paths import DATA_DIR, RESULTS_DIR
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree
from tqdm import tqdm
from difflib import SequenceMatcher


BASE_DATA = DATA_DIR
BASE_RESULTS = RESULTS_DIR

CITIES = ["barcelona", "madrid", "valencia"]

INPUT_FILENAME = "puntos_b2c.csv"

DISTANCE_THRESHOLD_METERS = 30
EARTH_RADIUS_METERS = 6371000


def normalize_text(value):
    if pd.isna(value):
        return ""

    text = str(value).lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def text_similarity(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    return round(SequenceMatcher(None, a, b).ratio() * 100, 2)


def duplicate_score(distance_m, same_company, address_similarity, location_similarity):
    score = 0
    reasons = []

    if distance_m <= 5:
        score += 40
        reasons.append("distance <= 5m")
    elif distance_m <= 10:
        score += 30
        reasons.append("distance <= 10m")
    elif distance_m <= 20:
        score += 20
        reasons.append("distance <= 20m")
    elif distance_m <= 30:
        score += 10
        reasons.append("distance <= 30m")

    if same_company:
        score += 30
        reasons.append("same company")
    else:
        reasons.append("different company")

    if address_similarity >= 90:
        score += 20
        reasons.append("very similar address")
    elif address_similarity >= 75:
        score += 10
        reasons.append("similar address")

    if location_similarity >= 90:
        score += 10
        reasons.append("very similar location")
    elif location_similarity >= 75:
        score += 5
        reasons.append("similar location")

    return score, "; ".join(reasons)


def classify_pair(score, same_company):
    if not same_company:
        return "Co-location / nearby competitors"

    if score >= 80:
        return "Very probable duplicate"
    elif score >= 60:
        return "Probable duplicate"
    elif score >= 45:
        return "Manual review"
    else:
        return "Nearby same company"


for city in CITIES:
    print("\n" + "=" * 60)
    print(f"Procesando ciudad: {city}")
    print("=" * 60)

    input_file = BASE_DATA / city / INPUT_FILENAME
    output_folder = BASE_RESULTS / city
    output_folder.mkdir(parents=True, exist_ok=True)

    output_file = output_folder / "nearby_lockers_scored_report.csv"

    print(f"Leyendo archivo: {input_file}")

    df = pd.read_csv(input_file)
    df = df.dropna(subset=["Latitude", "Longitude"]).reset_index(drop=True)

    # Crear ID numérico si no existe
    if "ID" not in df.columns:
        df.insert(0, "ID", range(1, len(df) + 1))

    print(f"Total puntos con coordenadas: {len(df)}")

    if len(df) < 2:
        print("No hay suficientes puntos para comparar.")
        continue

    coords_rad = np.radians(df[["Latitude", "Longitude"]].to_numpy())
    radius_rad = DISTANCE_THRESHOLD_METERS / EARTH_RADIUS_METERS

    print("Construyendo índice espacial...")
    tree = BallTree(coords_rad, metric="haversine")

    print(f"Buscando pares a menos de {DISTANCE_THRESHOLD_METERS} metros...")

    indices, distances = tree.query_radius(
        coords_rad,
        r=radius_rad,
        return_distance=True,
        sort_results=True
    )

    results = []

    for i in tqdm(range(len(df)), desc=f"Analizando pares {city}"):
        neighbors = indices[i]
        dists = distances[i]

        for j, dist_rad in zip(neighbors, dists):
            if j <= i:
                continue

            distance_m = dist_rad * EARTH_RADIUS_METERS

            company_1 = df.loc[i, "Company"]
            company_2 = df.loc[j, "Company"]

            address_1 = df.loc[i, "Address"]
            address_2 = df.loc[j, "Address"]

            location_1 = df.loc[i, "Location"] if "Location" in df.columns else ""
            location_2 = df.loc[j, "Location"] if "Location" in df.columns else ""

            same_company = normalize_text(company_1) == normalize_text(company_2)

            address_sim = text_similarity(address_1, address_2)
            location_sim = text_similarity(location_1, location_2)

            score, reasons = duplicate_score(
                distance_m=distance_m,
                same_company=same_company,
                address_similarity=address_sim,
                location_similarity=location_sim
            )

            classification = classify_pair(score, same_company)

            if classification in ["Very probable duplicate", "Probable duplicate"]:
                recommended_action = "Review first, possible merge"
            elif classification == "Manual review":
                recommended_action = "Manual review"
            elif classification == "Co-location / nearby competitors":
                recommended_action = "Keep both"
            else:
                recommended_action = "Probably keep, review if needed"

            base_row = {
                "City": city,
                "Distance_m": round(distance_m, 2),
                "Duplicate_Score": score,
                "Classification": classification,
                "Recommended_Action": recommended_action,
                "Reasons": reasons,

                "Same_Company": same_company,
                "Address_Similarity": address_sim,
                "Location_Similarity": location_sim,

                "ID_1": df.loc[i, "ID"],
                "ID_2": df.loc[j, "ID"],

                "Latitude_1": df.loc[i, "Latitude"],
                "Longitude_1": df.loc[i, "Longitude"],
                "Latitude_2": df.loc[j, "Latitude"],
                "Longitude_2": df.loc[j, "Longitude"],

                "Address_1": address_1,
                "Address_2": address_2,

                "Company_1": company_1,
                "Company_2": company_2,
            }

            columnas_excluidas = {
                "ID",
                "Latitude",
                "Longitude",
                "Address",
                "Company",
            }

            for col in df.columns:
                if col in columnas_excluidas:
                    continue

                col_name = col.replace(" ", "_")
                base_row[f"{col_name}_1"] = df.loc[i, col]
                base_row[f"{col_name}_2"] = df.loc[j, col]

            results.append(base_row)

    report = pd.DataFrame(results)

    columnas_inicio = [
        "City",
        "Distance_m",
        "Duplicate_Score",
        "Classification",
        "Recommended_Action",
        "Reasons",
        "Same_Company",
        "Address_Similarity",
        "Location_Similarity",
        "ID_1",
        "ID_2",
        "Latitude_1",
        "Longitude_1",
        "Latitude_2",
        "Longitude_2",
        "Address_1",
        "Address_2",
        "Company_1",
        "Company_2",
    ]

    if len(report) > 0:
        columnas_restantes = [
            col for col in report.columns
            if col not in columnas_inicio
        ]

        report = report[columnas_inicio + columnas_restantes]

        report = report.sort_values(
            by=["Duplicate_Score", "Distance_m"],
            ascending=[False, True]
        )

    report.to_csv(output_file, index=False, encoding="utf-8-sig")

    print(f"\nReporte guardado en:")
    print(output_file)
    print(f"\nTotal pares cercanos encontrados: {len(report)}")

    if len(report) > 0:
        print("\nResumen por clasificación:")
        print(report["Classification"].value_counts())

        print("\nTop 10 candidatos más sospechosos:")
        print(
            report[
                [
                    "Distance_m",
                    "Duplicate_Score",
                    "Classification",
                    "ID_1",
                    "Company_1",
                    "Location_1",
                    "ID_2",
                    "Company_2",
                    "Location_2"
                ]
            ].head(10)
        )
    else:
        print("No se encontraron pares cercanos.")

print("\nProceso terminado.")