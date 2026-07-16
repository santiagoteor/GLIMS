from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent

INPUT_FILE = (
    PROJECT_ROOT
    / "results"
    / "citypaq_source_comparison"
    / "citypaq_possibly_new_all_cities.csv"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "results"
    / "citypaq_source_comparison"
    / "citypaq_candidates_for_import.csv"
)


def main() -> None:
    df = pd.read_csv(INPUT_FILE)

    candidates = pd.DataFrame(
        {
            "Company": df["CityPaq_Company"],
            "Type": "Locker",
            "Infrastructure": "Public",
            "Type Infrastructure": "Locker",
            "City": df["Source_City"],
            "Zip Code": "",
            "Location": df["CityPaq_Location"],
            "Address": df["CityPaq_Address"],
            "Latitude": df["CityPaq_Latitude"],
            "Longitude": df["CityPaq_Longitude"],
        }
    )

    candidates["Import_Source"] = "Correos CityPaq"
    candidates["Import_Classification"] = df["Classification"]

    candidates["Data_Quality_Flag"] = ""

    missing_location = (
        candidates["Location"]
        .fillna("")
        .str.strip()
        .eq("")
    )

    candidates.loc[
        missing_location,
        "Data_Quality_Flag",
    ] = "Missing location"

    candidates.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Archivo generado: {OUTPUT_FILE}")
    print(f"Total candidatos: {len(candidates)}")

    print("\nPor ciudad:")
    print(candidates["City"].value_counts().to_string())

    print("\nFlags de calidad:")
    flags = candidates[
        candidates["Data_Quality_Flag"] != ""
    ]

    if flags.empty:
        print("Sin flags")
    else:
        print(
            flags[
                [
                    "City",
                    "Location",
                    "Address",
                    "Data_Quality_Flag",
                ]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()