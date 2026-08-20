from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from code.common.paths import DATA_DIR

# STEP 1 — Fetch district/neighborhood boundaries from local raw data

RAW_DATA_DIR = DATA_DIR.parent / "raw_data"

ADMIN_LEVELS = {
    "9": ("district", "limites_distritos"),
    "10": ("neighborhood", "limites_barrios"),
}

EXPECTED_COUNTS = {
    "madrid": {"district": 21, "neighborhood": 131},
    "barcelona": {"district": 10, "neighborhood": 73},
    "valencia": {"district": 19, "neighborhood": 88},
}

CITIES = list(EXPECTED_COUNTS)


def _madrid_districts(raw_data_dir: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(raw_data_dir / "districts_madrid" / "DISTRITOS.shp")
    return gdf.rename(columns={"NOMBRE": "district"})[["district", "geometry"]]


def _madrid_neighborhoods(raw_data_dir: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(raw_data_dir / "neighborhoods_madrid" / "BARRIOS.shp")
    return gdf.rename(columns={"NOMBRE": "neighborhood"})[["neighborhood", "geometry"]]


def _barcelona_districts(raw_data_dir: Path) -> gpd.GeoDataFrame:
    df = pd.read_csv(raw_data_dir / "districts_barcelona" / "BarcelonaCiutat_Districtes.csv")
    df = df.rename(columns={"nom_districte": "district"})
    geometry = gpd.GeoSeries.from_wkt(df["geometria_wgs84"], crs="EPSG:4326")
    return gpd.GeoDataFrame(df[["district"]], geometry=geometry, crs="EPSG:4326")


def _barcelona_neighborhoods(raw_data_dir: Path) -> gpd.GeoDataFrame:
    df = pd.read_csv(raw_data_dir / "neighborhoods_barcelona" / "BarcelonaCiutat_Barris.csv")
    df = df.rename(columns={"nom_barri": "neighborhood"})
    geometry = gpd.GeoSeries.from_wkt(df["geometria_wgs84"], crs="EPSG:4326")
    return gpd.GeoDataFrame(df[["neighborhood"]], geometry=geometry, crs="EPSG:4326")


def _valencia_districts(raw_data_dir: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(raw_data_dir / "districts_valencia" / "distritos_valencia.geojson")
    gdf = gdf.rename(columns={"nombre": "district"})
    gdf = gdf.dissolve(by="coddistrit", aggfunc="first").reset_index()
    return gdf[["district", "geometry"]]


def _valencia_neighborhoods(raw_data_dir: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(raw_data_dir / "neighborhoods_valencia" / "barrios_valencia.geojson")
    return gdf.rename(columns={"nombre": "neighborhood"})[["neighborhood", "geometry"]]


LOADERS = {
    ("madrid", "9"): _madrid_districts,
    ("madrid", "10"): _madrid_neighborhoods,
    ("barcelona", "9"): _barcelona_districts,
    ("barcelona", "10"): _barcelona_neighborhoods,
    ("valencia", "9"): _valencia_districts,
    ("valencia", "10"): _valencia_neighborhoods,
}


def load_city_level(city: str, admin_level: str, raw_data_dir: Path) -> gpd.GeoDataFrame:
    """Load one admin level for one city from the local raw data files."""

    label, _ = ADMIN_LEVELS[admin_level]
    print(f"\nLoading {label}s for {city} from {raw_data_dir}...")

    gdf = LOADERS[(city, admin_level)](raw_data_dir)
    gdf = gdf[gdf.geometry.notna()].copy()
    gdf = gdf[gdf[label].notna()].copy()

    if gdf.crs is None:
        raise ValueError(f"{city} {label}s have no CRS set; check the source file.")

    return gdf.to_crs(epsg=4326)


def save_level(
    gdf: gpd.GeoDataFrame,
    city: str,
    admin_level: str,
) -> gpd.GeoDataFrame | None:
    """Normalize columns and write one admin level to GeoJSON."""

    label, stem = ADMIN_LEVELS[admin_level]

    if gdf.empty:
        print(f"  [!] No {label}s found locally for {city}.")
        return None

    subset = gdf.copy()
    subset["admin_level"] = admin_level
    keep_columns = [label, "admin_level", "geometry"]
    subset = subset[keep_columns].reset_index(drop=True)

    city_folder = DATA_DIR / city
    city_folder.mkdir(parents=True, exist_ok=True)
    output_path = city_folder / f"{stem}.geojson"

    subset.to_file(output_path, driver="GeoJSON")

    expected = EXPECTED_COUNTS.get(city, {}).get(label)
    expected_note = f" (expected ~{expected})" if expected else ""
    print(
        f"  {label}s: {len(subset)} saved{expected_note} -> "
        f"{output_path.resolve()}"
    )

    return subset


def fetch_boundaries(cities: list[str], raw_data_dir: Path) -> None:
    """Run STEP 1 for the given cities: build districts_*.geojson / barrios_*.geojson."""

    for city in cities:
        for admin_level in ADMIN_LEVELS:
            gdf = load_city_level(city, admin_level, raw_data_dir)
            save_level(gdf, city, admin_level)


# STEP 2 — Combine districts + neighborhoods into a single zones file

SOURCES = {
    "limites_distritos": "district",
    "limites_barrios": "neighborhood",
}


def combine_city(city: str) -> None:
    folder = DATA_DIR / city
    parts = []

    for stem, tipo in SOURCES.items():
        path = folder / f"{stem}.geojson"
        if not path.exists():
            print(f"  [!] Missing {path.name} for {city}, skipping.")
            continue

        gdf = gpd.read_file(path).to_crs(epsg=4326)
        gdf = gdf.rename(columns={tipo: "zona"})   
        gdf["tipo"] = tipo                        
        parts.append(gdf[["zona", "tipo", "admin_level", "geometry"]])

    if not parts:
        raise ValueError(f"No boundary files found for {city}.")

    zones = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")

    output_path = folder / "limites_zonas.geojson"
    zones.to_file(output_path, driver="GeoJSON")

    n_dist = int((zones["tipo"] == "district").sum())
    n_neigh = int((zones["tipo"] == "neighborhood").sum())
    print(
        f"  {len(zones)} zones ({n_dist} districts, {n_neigh} neighborhoods) "
        f"-> {output_path.resolve()}"
    )


def build_zones(cities: list[str]) -> None:
    """Run STEP 2 for the given cities: build limites_zonas.geojson."""

    for city in cities:
        print(f"\n{city}:")
        combine_city(city)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build district/neighborhood boundaries from local raw data, then "
            "combine them into a single simulation-zones file per city."
        )
    )
    parser.add_argument(
        "--city",
        choices=CITIES + ["all"],
        default="all",
        help="City to process ('all' processes every configured city).",
    )
    parser.add_argument(
        "--raw-data-dir",
        type=Path,
        default=RAW_DATA_DIR,
        help=(
            "Folder holding districts_<city>/ and neighborhoods_<city>/ subfolders "
            f"(default: {RAW_DATA_DIR})."
        ),
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip Step 1 (fetching boundaries) and only combine existing geojson files.",
    )
    parser.add_argument(
        "--skip-combine",
        action="store_true",
        help="Skip Step 2 (combining into zones) and only fetch boundaries.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    cities = CITIES if args.city == "all" else [args.city]

    print(">>> DATA_DIR =", DATA_DIR)

    if not args.skip_fetch:
        print("\n=== STEP 1: Fetching district/neighborhood boundaries ===")
        fetch_boundaries(cities, args.raw_data_dir)
    else:
        print("\n=== STEP 1 skipped (--skip-fetch) ===")

    if not args.skip_combine:
        print("\n=== STEP 2: Building simulation zones ===")
        build_zones(cities)
    else:
        print("\n=== STEP 2 skipped (--skip-combine) ===")

    print("\nDone.")


if __name__ == "__main__":
    main()