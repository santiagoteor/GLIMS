import json
from pathlib import Path
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FOLDER = PROJECT_ROOT / "raw_data"

FILES = [
    "Barcelona Citypaq.txt",
    "Madrid Citypaq.txt",
    "Valencia Citypaq.txt"
]

COMPANY = "Correos Citypaq"

for filename in FILES:
    input_file = INPUT_FOLDER / filename
    city_slug = filename.split(" ")[0].lower()

    print(f"\nProcesando {filename}...")

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    offices = data["others"]["offices"]
    rows = []

    for idx, office in enumerate(offices, start=1):
        is_private = office.get("isPrivate", False)

        if is_private:
            type_value = "Convenience stores"
            infrastructure = "Private"
            type_infrastructure = "Premises"
        else:
            type_value = "Locker"
            infrastructure = "Public"
            type_infrastructure = "Locker"

        office_id = office.get("officeId", "")
        unique_id = f"CORREOS_CITYPAQ_{city_slug.upper()}_{office_id or idx:0>5}"

        rows.append({
            "ID": unique_id,
            "Company": COMPANY,
            "Type": type_value,
            "Infrastructure": infrastructure,
            "Type Infrastructure": type_infrastructure,
            "City": office.get("cityName", ""),
            "Zip Code": office.get("postalCode", ""),
            "Location": office.get("unitName", ""),
            "Address": office.get("address", ""),
            "Latitude": office.get("latitude", ""),
            "Longitude": office.get("longitude", "")
        })

    df = pd.DataFrame(rows)

    output_file = input_file.with_suffix(".csv")

    df.to_csv(output_file, index=False, encoding="utf-8-sig")

    print(f"   ✓ CSV guardado en:")
    print(f"   {output_file}")
    print(f"   Total oficinas: {len(df)}")