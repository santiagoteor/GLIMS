from code.common.paths import DATA_DIR, RESULTS_DIR
from code.common.text_utils import normalize_text, text_similarity
from code.common.address_utils import compare_address_numbers
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from code.common.constants import (
    CITIES,
    EARTH_RADIUS_METERS,
)

RECORD_TYPE_CATALOG = {
    0: {
        "name": "Unknown",
        "description": "Empty or unrecognized type",
    },
    1: {
        "name": "Locker",
        "description": "Automatic locker or equivalent infrastructure",
    },
    2: {
        "name": "PUDO",
        "description": "Pick-Up and Drop-Off point",
    },
    3: {
        "name": "Click & Collect",
        "description": "Click & Collect pickup point",
    },
    4: {
        "name": "Distribution / Reception Center",
        "description": "Distribution or reception center",
    },
    5: {
        "name": "Last Mile Logistics Station",
        "description": "Last-mile logistics station",
    },
    6: {
        "name": "Warehouse / E-Fulfilment",
        "description": "Warehouse or e-fulfilment facility",
    },
    7: {
        "name": "Convenience Store",
        "description": "Convenience store",
    },
}


CLUSTER_TYPE_CATALOG = {
    1: {
        "name": "Locker only",
        "description": "The location contains only Locker records",
    },
    2: {
        "name": "PUDO only",
        "description": "The location contains only PUDO records",
    },
    3: {
        "name": "Locker and PUDO",
        "description": "The location contains at least one Locker and one PUDO",
    },
    4: {
        "name": "Click & Collect only",
        "description": "The location contains only Click & Collect records",
    },
    5: {
        "name": "Convenience Store only",
        "description": "The location contains only Convenience Store records",
    },
    6: {
        "name": "Other single service",
        "description": "The location contains a single service type other than Locker or PUDO",
    },
    7: {
        "name": "Multiple services",
        "description": "The location contains multiple service types",
    },
}

SHARING_TYPE_CATALOG = {
    1: {
        "name": "Single record",
        "description": (
            "The location contains a single record"
        ),
    },
    2: {
        "name": "Same company, multiple records",
        "description": (
            "The location contains multiple records "
            "from a single company"
        ),
    },
    3: {
        "name": "Multiple companies, single service",
        "description": (
            "The location contains multiple companies "
            "and a single service type"
        ),
    },
    4: {
        "name": "Multiple companies, multiple services",
        "description": (
            "The location contains multiple companies "
            "and multiple service types"
        ),
    },
}


SPATIAL_CONFIDENCE_CATALOG = {
    1: {
        "name": "High",
        "description": (
            "Maximum cluster diameter less than or equal to 15 m"
        ),
    },
    2: {
        "name": "Medium",
        "description": (
            "Maximum cluster diameter greater than 15 m "
            "and less than or equal to 30 m"
        ),
    },
    3: {
        "name": "Review",
        "description": (
            "Maximum cluster diameter greater than 30 m"
        ),
    },
}

STRONG_DISTANCE_METERS = 5
CONDITIONAL_DISTANCE_METERS = 15
REVIEW_DISTANCE_METERS = 30
WEAK_REVIEW_DISTANCE_METERS = 50

ADDRESS_SIMILARITY_THRESHOLD = 80
LOCATION_SIMILARITY_THRESHOLD = 80

class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])

        return self.parent[item]

    def union(self, first: int, second: int) -> None:
        root_first = self.find(first)
        root_second = self.find(second)

        if root_first == root_second:
            return

        if self.rank[root_first] < self.rank[root_second]:
            self.parent[root_first] = root_second
        elif self.rank[root_first] > self.rank[root_second]:
            self.parent[root_second] = root_first
        else:
            self.parent[root_second] = root_first
            self.rank[root_first] += 1

def classify_record_type(value) -> int:
    normalized = normalize_text(value)

    mapping = {
        "LOCKER": 1,
        "LOCKERS": 1,
        "LSP PUDOS": 2,
        "LSP PUDO": 2,
        "PUDO": 2,
        "PUDOS": 2,
        "CLICK COLLECT": 3,
        "CENTROS DE DISTRIBUCION RECEPCION": 4,
        "CENTRO DE DISTRIBUCION RECEPCION": 4,
        "ESTACION LOGISTICA DE ULTIMA MILLA": 5,
        "ALMACEN E FULFILMENT": 6,
        "CONVENIENCE STORES": 7,
        "CONVENIENCE STORE": 7,
    }

    return mapping.get(normalized, 0)


def load_city_records(city: str) -> pd.DataFrame:
    input_file = DATA_DIR / city / "puntos_b2c.csv"

    if not input_file.exists():
        raise FileNotFoundError(
            f"File not found: {input_file}"
        )

    df = pd.read_csv(input_file)

    required_columns = {
        "ID",
        "Company",
        "Type",
        "Latitude",
        "Longitude",
    }

    missing_columns = required_columns - set(df.columns)

    if missing_columns:
        raise ValueError(
            f"{city}: required columns are missing: "
            f"{sorted(missing_columns)}"
        )

    df = df.copy()

    df["Latitude"] = pd.to_numeric(
        df["Latitude"],
        errors="coerce",
    )

    df["Longitude"] = pd.to_numeric(
        df["Longitude"],
        errors="coerce",
    )

    df["Record_Service_Type_Code"] = (
        df["Type"].apply(classify_record_type)
    )

    df["Record_Service_Type_Name"] = (
        df["Record_Service_Type_Code"]
        .map(
            {
                code: values["name"]
                for code, values in RECORD_TYPE_CATALOG.items()
            }
        )
    )

    df["Company_Normalized"] = (
        df["Company"].apply(normalize_text)
    )

    df["Location_Normalized"] = (
        df.get("Location", pd.Series(index=df.index, dtype="object"))
        .apply(normalize_text)
    )

    df["Address_Normalized"] = (
        df.get("Address", pd.Series(index=df.index, dtype="object"))
        .apply(normalize_text)
    )

    return df

def build_candidate_pairs(
    df: pd.DataFrame,
) -> pd.DataFrame:
    valid = df.dropna(
        subset=["Latitude", "Longitude"]
    ).reset_index(drop=False)

    if len(valid) < 2:
        return pd.DataFrame()

    coords_rad = np.radians(
        valid[["Latitude", "Longitude"]].to_numpy()
    )

    radius_rad = (
        WEAK_REVIEW_DISTANCE_METERS
        / EARTH_RADIUS_METERS
    )

    tree = BallTree(
        coords_rad,
        metric="haversine",
    )

    indices, distances = tree.query_radius(
        coords_rad,
        r=radius_rad,
        return_distance=True,
        sort_results=True,
    )

    rows = []

    for local_i, (neighbors, dists) in enumerate(
        zip(indices, distances)
    ):
        for local_j, dist_rad in zip(neighbors, dists):
            if local_j <= local_i:
                continue

            row_i = valid.iloc[local_i]
            row_j = valid.iloc[local_j]

            distance_m = (
                float(dist_rad)
                * EARTH_RADIUS_METERS
            )

            address_similarity = text_similarity(
                row_i.get("Address"),
                row_j.get("Address"),
            )

            location_similarity = text_similarity(
                row_i.get("Location"),
                row_j.get("Location"),
            )
            (
                address_numbers_available,
                same_address_number,
            ) = compare_address_numbers(
                row_i.get("Address"),
                row_j.get("Address"),
            )

            same_company = (
                row_i["Company_Normalized"]
                == row_j["Company_Normalized"]
            )

            same_type = (
                row_i["Record_Service_Type_Code"]
                == row_j["Record_Service_Type_Code"]
            )

            strong_address_match = (
                address_similarity
                >= ADDRESS_SIMILARITY_THRESHOLD
                and (
                    not address_numbers_available
                    or same_address_number
                )
            )

            strong_location_match = (
                location_similarity
                >= LOCATION_SIMILARITY_THRESHOLD
            )

            strong_text_match = (
                strong_address_match
                or strong_location_match
            )

            if distance_m <= STRONG_DISTANCE_METERS:
                should_cluster = True
                pair_rule = "distance <= 5m"

            elif (
                distance_m
                <= CONDITIONAL_DISTANCE_METERS
                and strong_text_match
            ):
                should_cluster = True
                pair_rule = (
                    "distance <= 15m and strong text match"
                )

            else:
                should_cluster = False
                pair_rule = "review candidate only"

            possible_duplicate = (
                should_cluster
                and same_company
                and same_type
            )

            if should_cluster and not same_company:
                pair_classification = "Co-location"
            elif possible_duplicate:
                pair_classification = (
                    "Possible duplicate"
                )
            elif should_cluster and same_company:
                pair_classification = (
                    "Multi-service same company"
                )
            elif (
                distance_m <= REVIEW_DISTANCE_METERS
                and strong_text_match
            ):
                pair_classification = (
                    "Manual review"
                )
            elif (
                distance_m
                <= WEAK_REVIEW_DISTANCE_METERS
                and same_company
                and strong_text_match
            ):
                pair_classification = (
                    "Manual review"
                )
            else:
                pair_classification = (
                    "Nearby, no action"
                )

            rows.append(
                {
                    "Index_1": int(row_i["index"]),
                    "Index_2": int(row_j["index"]),
                    "ID_1": row_i["ID"],
                    "ID_2": row_j["ID"],
                    "Company_1": row_i.get("Company"),
                    "Company_2": row_j.get("Company"),
                    "Type_1": row_i.get("Type"),
                    "Type_2": row_j.get("Type"),
                    "Location_1": row_i.get("Location"),
                    "Location_2": row_j.get("Location"),
                    "Address_1": row_i.get("Address"),
                    "Address_2": row_j.get("Address"),
                    "Distance_m": round(
                        distance_m,
                        2,
                    ),
                    "Address_Similarity": (
                        address_similarity
                    ),
                    "Location_Similarity": (
                        location_similarity
                    ),
                    "Address_Numbers_Available": (
                        address_numbers_available
                    ),
                    "Same_Address_Number": (
                        same_address_number
                    ),
                    "Same_Company": same_company,
                    "Same_Type": same_type,
                    "Should_Cluster": should_cluster,
                    "Possible_Duplicate": (
                        possible_duplicate
                    ),
                    "Pair_Classification": (
                        pair_classification
                    ),
                    "Pair_Rule": pair_rule,
                }
            )

    return pd.DataFrame(rows)

def assign_location_clusters(
    city: str,
    df: pd.DataFrame,
    pairs: pd.DataFrame,
) -> pd.DataFrame:
    result = df.copy()

    union_find = UnionFind(len(result))

    if not pairs.empty:
        cluster_pairs = pairs[
            pairs["Should_Cluster"] == True
        ]

        for _, pair in cluster_pairs.iterrows():
            union_find.union(
                int(pair["Index_1"]),
                int(pair["Index_2"]),
            )

    root_to_cluster_id = {}
    cluster_counter = 1

    cluster_ids = []

    city_prefix = city.upper()[:3]

    for index in range(len(result)):
        root = union_find.find(index)

        if root not in root_to_cluster_id:
            root_to_cluster_id[root] = (
                f"{city_prefix}_LOC_{cluster_counter:06d}"
            )
            cluster_counter += 1

        cluster_ids.append(
            root_to_cluster_id[root]
        )

    result["Location_Cluster_ID"] = cluster_ids

    return result


def classify_cluster_service_type(
    service_codes: set[int],
) -> int:
    has_locker = 1 in service_codes
    has_pudo = 2 in service_codes

    if service_codes == {1}:
        return 1

    if service_codes == {2}:
        return 2

    if has_locker and has_pudo:
        return 3

    if service_codes == {3}:
        return 4

    if service_codes == {7}:
        return 5

    if len(service_codes) == 1:
        return 6

    return 7

def classify_cluster_sharing_type(
    record_count: int,
    company_count: int,
    service_type_count: int,
) -> int:
    if record_count == 1:
        return 1

    if company_count == 1:
        return 2

    if service_type_count == 1:
        return 3

    return 4


def calculate_cluster_diameter(
    group: pd.DataFrame,
) -> float:
    if len(group) < 2:
        return 0.0

    coords_rad = np.radians(
        group[["Latitude", "Longitude"]].to_numpy()
    )

    tree = BallTree(
        coords_rad,
        metric="haversine",
    )

    max_distance_rad = 0.0

    for index in range(len(coords_rad)):
        distances, _ = tree.query(
            coords_rad[index:index + 1],
            k=len(coords_rad),
        )

        current_max = float(distances[0].max())

        if current_max > max_distance_rad:
            max_distance_rad = current_max

    return max_distance_rad * EARTH_RADIUS_METERS


def classify_spatial_confidence(
    diameter_m: float,
) -> int:
    if diameter_m <= 15:
        return 1

    if diameter_m <= 30:
        return 2

    return 3

def build_cluster_summary(
    city: str,
    records: pd.DataFrame,
) -> pd.DataFrame:
    rows = []

    grouped = records.groupby(
        "Location_Cluster_ID",
        sort=True,
    )

    for cluster_id, group in grouped:
        service_codes = set(
            group["Record_Service_Type_Code"]
            .dropna()
            .astype(int)
            .tolist()
        )

        cluster_type_code = (
            classify_cluster_service_type(
                service_codes
            )
        )

        cluster_type_name = (
            CLUSTER_TYPE_CATALOG[
                cluster_type_code
            ]["name"]
        )
        record_count = len(group)
        company_count = len(
            group["Company_Normalized"]
            .replace("", pd.NA)
            .dropna()
            .unique()
        )
        service_type_count = len(service_codes)

        sharing_type_code = (
            classify_cluster_sharing_type(
                record_count=record_count,
                company_count=company_count,
                service_type_count=service_type_count,
            )
        )

        sharing_type_name = (
            SHARING_TYPE_CATALOG[
                sharing_type_code
            ]["name"]
        )

        cluster_diameter_m = (
            calculate_cluster_diameter(group)
        )

        spatial_confidence_code = (
            classify_spatial_confidence(
                cluster_diameter_m
            )
        )

        spatial_confidence_name = (
            SPATIAL_CONFIDENCE_CATALOG[
                spatial_confidence_code
            ]["name"]
        )

        companies = sorted(
            {
                str(value).strip()
                for value in group["Company"].dropna()
                if str(value).strip()
            }
        )

        addresses = sorted(
            {
                str(value).strip()
                for value in group.get(
                    "Address",
                    pd.Series(dtype="object"),
                ).dropna()
                if str(value).strip()
            }
        )

        locations = sorted(
            {
                str(value).strip()
                for value in group.get(
                    "Location",
                    pd.Series(dtype="object"),
                ).dropna()
                if str(value).strip()
            }
        )

        rows.append(
            {
                "City": city,
                "Location_Cluster_ID": cluster_id,
                "Representative_Latitude": (
                    group["Latitude"].mean()
                ),
                "Representative_Longitude": (
                    group["Longitude"].mean()
                ),
                "Record_Count": record_count,
                "Company_Count": company_count,
                "Companies": " | ".join(companies),
                "Addresses": " | ".join(addresses),
                "Locations": " | ".join(locations),
                "Has_Locker": 1 in service_codes,
                "Has_PUDO": 2 in service_codes,
                "Has_Click_Collect": 3 in service_codes,
                "Has_Distribution_Center": 4 in service_codes,
                "Has_Last_Mile_Station": 5 in service_codes,
                "Has_Warehouse": 6 in service_codes,
                "Has_Convenience_Store": 7 in service_codes,
                "Has_Unknown": 0 in service_codes,
                "Cluster_Service_Type_Code": (
                    cluster_type_code
                ),
                "Cluster_Service_Type_Name": (
                    cluster_type_name
                ),
                                "Cluster_Sharing_Type_Code": (
                    sharing_type_code
                ),
                "Cluster_Sharing_Type_Name": (
                    sharing_type_name
                ),
                "Cluster_Diameter_m": round(
                    cluster_diameter_m,
                    2,
                ),
                "Cluster_Spatial_Confidence_Code": (
                    spatial_confidence_code
                ),
                "Cluster_Spatial_Confidence_Name": (
                    spatial_confidence_name
                ),
            }
        )

    return pd.DataFrame(rows)

def enrich_records_with_cluster_data(
    records: pd.DataFrame,
    clusters: pd.DataFrame,
) -> pd.DataFrame:
    cluster_columns = [
        "Location_Cluster_ID",
        "Record_Count",
        "Company_Count",
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
        "Companies",
    ]

    enriched = records.merge(
        clusters[cluster_columns],
        on="Location_Cluster_ID",
        how="left",
        validate="many_to_one",
    )

    enriched = enriched.rename(
        columns={
            "Record_Count": "Cluster_Record_Count",
            "Company_Count": "Cluster_Company_Count",
            "Companies": "Cluster_Companies",
        }
    )

    return enriched



def export_catalogs() -> None:
    record_catalog = pd.DataFrame(
        [
            {
                "Code": code,
                "Name": values["name"],
                "Description": values["description"],
            }
            for code, values in RECORD_TYPE_CATALOG.items()
        ]
    )
      

    cluster_catalog = pd.DataFrame(
        [
            {
                "Code": code,
                "Name": values["name"],
                "Description": values["description"],
            }
            for code, values in CLUSTER_TYPE_CATALOG.items()
        ]
    )
    
    sharing_catalog = pd.DataFrame(
        [
            {
                "Code": code,
                "Name": values["name"],
                "Description": values["description"],
            }
            for code, values in SHARING_TYPE_CATALOG.items()
        ]
    )

    spatial_confidence_catalog = pd.DataFrame(
        [
            {
                "Code": code,
                "Name": values["name"],
                "Description": values["description"],
            }
            for code, values in SPATIAL_CONFIDENCE_CATALOG.items()
        ]
    )

    record_catalog.to_csv(
        RESULTS_DIR / "record_service_type_catalog.csv",
        index=False,
        encoding="utf-8-sig",
    )

    cluster_catalog.to_csv(
        RESULTS_DIR / "cluster_service_type_catalog.csv",
        index=False,
        encoding="utf-8-sig",
    )
    
    sharing_catalog.to_csv(
        RESULTS_DIR / "cluster_sharing_type_catalog.csv",
        index=False,
        encoding="utf-8-sig",
    )

    spatial_confidence_catalog.to_csv(
        RESULTS_DIR / "cluster_spatial_confidence_catalog.csv",
        index=False,
        encoding="utf-8-sig",
    )


def add_cluster_review_flags(
    records_classified: pd.DataFrame,
    cluster_summary: pd.DataFrame,
    pairs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Mark clusters that require manual review.

    A cluster is marked when:
    - its spatial confidence is Review;
    - it participates in a Manual review pair;
    - it contains a Possible duplicate;
    - it contains an Unknown or Unclassified service type.
    """
    records_classified = records_classified.copy()
    cluster_summary = cluster_summary.copy()

    review_cluster_ids = set(
        cluster_summary.loc[
            cluster_summary[
                "Cluster_Spatial_Confidence_Name"
            ].eq("Review"),
            "Location_Cluster_ID",
        ]
    )

    if (
        "Record_Service_Type_Name"
        in records_classified.columns
    ):
        normalized_service_type = (
            records_classified[
                "Record_Service_Type_Name"
            ]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )

        unknown_service_mask = (
            normalized_service_type.isin(
                {
                    "unknown",
                    "unclassified",
                }
            )
        )

        review_cluster_ids.update(
            records_classified.loc[
                unknown_service_mask,
                "Location_Cluster_ID",
            ].dropna()
        )

    cluster_summary[
        "Cluster_Review_Required"
    ] = cluster_summary[
        "Location_Cluster_ID"
    ].isin(review_cluster_ids)

    records_classified[
        "Cluster_Review_Required"
    ] = records_classified[
        "Location_Cluster_ID"
    ].isin(review_cluster_ids)

    return (
        records_classified,
        cluster_summary,
    )


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    export_catalogs()

    for city in CITIES:
        df = load_city_records(city)

        city_output_dir = (
            RESULTS_DIR
            / city
            / "location_review"
        )
        city_output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        print(f"\n{city.upper()}")
        print(f"Total records: {len(df)}")
        print(
            "Records with valid coordinates: "
            f"{df[['Latitude', 'Longitude']].notna().all(axis=1).sum()}"
        )

        print("\nGenerating candidate pairs...")
        pairs = build_candidate_pairs(df)

        print(f"Pairs within 50 m: {len(pairs)}")

        clustered_records = assign_location_clusters(
            city=city,
            df=df,
            pairs=pairs,
        )

        clusters = build_cluster_summary(
            city=city,
            records=clustered_records,
        )

        records_classified = enrich_records_with_cluster_data(
            records=clustered_records,
            clusters=clusters,
        )
        (
            records_classified,
            clusters,
        ) = add_cluster_review_flags(
            records_classified,
            clusters,
            pairs,
        )

        manual_review = pairs[
            pairs["Pair_Classification"].isin(
                [
                    "Manual review",
                    "Possible duplicate",
                    "Multi-service same company",
                ]
            )
        ].copy()

        records_output = (
            city_output_dir
            / "records_classified.csv"
        )

        clusters_output = (
            city_output_dir
            / "location_clusters.csv"
        )

        manual_output = (
            city_output_dir
            / "manual_review.csv"
        )
        
        candidate_pairs_output = (
            city_output_dir
            / "candidate_pairs.csv"
        )

        cluster_edges_output = (
            city_output_dir
            / "cluster_edges.csv"
        )

        records_classified.to_csv(
            records_output,
            index=False,
            encoding="utf-8-sig",
        )

        clusters.to_csv(
            clusters_output,
            index=False,
            encoding="utf-8-sig",
        )

        manual_review.to_csv(
            manual_output,
            index=False,
            encoding="utf-8-sig",
        )
        
        pairs.to_csv(
            candidate_pairs_output,
            index=False,
            encoding="utf-8-sig",
        )

        cluster_edges = pairs[
            pairs["Should_Cluster"] == True
        ].copy()

        cluster_edges.to_csv(
            cluster_edges_output,
            index=False,
            encoding="utf-8-sig",
        )

        print(
            f"Clusters generated: {len(clusters)}"
        )

        print(
            "Clusters with more than one record: "
            f"{int((clusters['Record_Count'] > 1).sum())}"
        )

        print(
            "Locker and PUDO clusters: "
            f"{int((clusters['Cluster_Service_Type_Code'] == 3).sum())}"
        )

        print(
            f"Pairs for manual review: "
            f"{len(manual_review)}"
        )

        print(f"Saved: {records_output}")
        print(f"Saved: {clusters_output}")
        print(f"Saved: {manual_output}")
        print(f"Saved: {candidate_pairs_output}")
        print(f"Saved: {cluster_edges_output}")


if __name__ == "__main__":
    main()