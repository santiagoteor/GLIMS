from code.common.paths import RESULTS_DIR
from code.common.constants import CITIES
import pandas as pd

# Reuses the capacity table already defined in the main script, so the
# numbers only live in one place.
from code.analysis.review_b2c_locations import TYPE_CAPACITY


# The exact name of the zip code column in puntos_b2c.csv may vary. Add any
# other variant you use to this list if none of these match.
ZIP_CODE_COLUMN_CANDIDATES = [
    "Zip Code",
    "ZipCode",
    "Zip_Code",
    "Postal Code",
    "PostalCode",
    "CP",
    "Codigo Postal",
    "Código Postal",
]

ID_COLUMN = "ID"

# Columns that describe the cluster/location itself rather than an
# individual record, and that are guaranteed to be identical across every
# record of the same cluster. These get a single value per merged row
# instead of being concatenated with "+".
SINGLE_VALUE_COLUMNS = {
    "Type_Group_Code",
    "Cluster_Record_Count",
    "Cluster_Company_Count",
    "Has_Locker",
    "Has_PUDO",
    "Has_Click_Collect",
    "Has_Distribution_Center",
    "Has_Last_Mile_Station",
    "Has_Warehouse",
    "Has_Convenience_Store",
    "Has_Unknown",
    "Cluster_Service_Type_Code",
    "Cluster_Service_Type_Name",
    "Cluster_Sharing_Type_Code",
    "Cluster_Sharing_Type_Name",
    "Cluster_Diameter_m",
    "Cluster_Spatial_Confidence_Code",
    "Cluster_Spatial_Confidence_Name",
    "Cluster_Review_Required",
}

# Columns where repeated identical values are collapsed to one before
# joining with "+", so "Privada + Privada" becomes just "Privada", but
# genuinely different values ("Convenience Stores + Lsp Pudos") are kept.
DEDUP_JOIN_COLUMNS = {
    "Type",
    "Infrastructure",
    "Type Infrastructure",
    "City",
    "Type_raw",
    "Infrastructure_raw",
    "Type Infrastructure_raw",
    "City_raw",
}

# Internal helper columns dropped entirely from the merged output.
DROPPED_COLUMNS = {
    "Company_Normalized",
    "Location_Normalized",
    "Address_Normalized",
}

# Columns already assigned an explicit value before the generic loop runs,
# so the loop must skip them.
ALREADY_ASSIGNED_COLUMNS = {
    "Location_Cluster_ID",
    "Type_Group_Name",
    "Location",
    "Address",
    "Latitude",
    "Longitude",
}


def find_zip_code_column(columns) -> str | None:
    lower_map = {
        str(column).strip().lower(): column
        for column in columns
    }

    for candidate in ZIP_CODE_COLUMN_CANDIDATES:
        match = lower_map.get(candidate.strip().lower())

        if match is not None:
            return match

    return None


def first_non_null(series: pd.Series):
    for value in series:
        if pd.notna(value) and str(value).strip():
            return value

    return None


def join_column(series: pd.Series) -> str:
    values = [
        str(value).strip()
        for value in series
        if pd.notna(value) and str(value).strip()
    ]

    return " + ".join(values)


def dedup_join_column(series: pd.Series) -> str:
    seen = set()
    values = []

    for value in series:
        if pd.notna(value) and str(value).strip():
            text = str(value).strip()

            if text not in seen:
                seen.add(text)
                values.append(text)

    return " + ".join(values)


def compute_id_final(series: pd.Series):
    numeric_ids = pd.to_numeric(series, errors="coerce").dropna()

    if numeric_ids.empty:
        return None

    return int(numeric_ids.min())


def load_records_classified(city: str) -> pd.DataFrame:
    records_file = (
        RESULTS_DIR
        / city
        / "location_review"
        / "records_classified.csv"
    )

    if not records_file.exists():
        raise FileNotFoundError(
            f"{city}: expected file not found: {records_file}. "
            "Run review_b2c_locations.py first."
        )

    return pd.read_csv(records_file)


def merge_city_clusters(city: str) -> pd.DataFrame:
    records = load_records_classified(city)

    zip_column = find_zip_code_column(records.columns)

    # Only clusters with more than one record are "fusions". Records that
    # never paired with anything (Cluster_Record_Count == 1), and clusters
    # flagged for manual review, are excluded entirely. Exact duplicates
    # were already removed upstream by review_b2c_locations.py.
    mergeable = records[
        (records["Cluster_Record_Count"] > 1)
        & (records["Cluster_Review_Required"] == False)
    ].copy()

    if mergeable.empty:
        return pd.DataFrame()

    already_assigned = set(ALREADY_ASSIGNED_COLUMNS)

    if zip_column is not None:
        already_assigned.add(zip_column)

    rows = []

    grouped = mergeable.groupby(
        "Location_Cluster_ID",
        sort=True,
    )

    for cluster_id, group in grouped:
        merged_row = {
            "Location_Cluster_ID": cluster_id,
            "Merged_Record_Count": len(group),
            "Main_Type": (
                group["Type_Group_Name"].iloc[0]
                if "Type_Group_Name" in group.columns
                else None
            ),
        }

        if ID_COLUMN in group.columns:
            merged_row["id_final"] = compute_id_final(
                group[ID_COLUMN]
            )

        merged_row["Location"] = first_non_null(
            group.get("Location", pd.Series(dtype="object"))
        )
        merged_row["Address"] = first_non_null(
            group.get("Address", pd.Series(dtype="object"))
        )
        merged_row["Latitude"] = group["Latitude"].mean()
        merged_row["Longitude"] = group["Longitude"].mean()
        merged_row["Capacity_Sum"] = sum(
            TYPE_CAPACITY.get(int(code), 0)
            for code in group["Record_Service_Type_Code"].dropna()
        )

        if zip_column is not None:
            merged_row["Zip_Code"] = first_non_null(
                group[zip_column]
            )

        for column in mergeable.columns:
            if column in already_assigned:
                continue

            if column in DROPPED_COLUMNS:
                continue

            if column in SINGLE_VALUE_COLUMNS:
                merged_row[column] = group[column].iloc[0]
            elif column in DEDUP_JOIN_COLUMNS:
                merged_row[column] = dedup_join_column(
                    group[column]
                )
            else:
                merged_row[column] = join_column(group[column])

        rows.append(merged_row)

    return pd.DataFrame(rows)


def main() -> None:
    for city in CITIES:
        print(f"\n{city.upper()}")

        merged = merge_city_clusters(city)

        output_dir = RESULTS_DIR / city / "mergens"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / "merged_records.csv"

        if merged.empty:
            print(
                "No clusters to merge "
                "(no multi-record, non-review clusters)."
            )
            merged.to_csv(
                output_file,
                index=False,
                encoding="utf-8-sig",
            )
            print(f"Saved (empty): {output_file}")
            continue

        merged.to_csv(
            output_file,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"Clusters merged: {len(merged)}")
        print(f"Saved: {output_file}")


if __name__ == "__main__":
    main()