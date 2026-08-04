from __future__ import annotations

import argparse

import geopandas as gpd
import pandas as pd

from code.common.paths import DATA_DIR

CITIES = ["madrid", "barcelona", "valencia"]

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
        gdf = gdf.rename(columns={tipo: "zona"})   # district/neighborhood -> zona
        gdf["tipo"] = tipo                          # "district" or "neighborhood"
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine districts and neighborhoods into one zones file."
    )
    parser.add_argument(
        "--city",
        choices=CITIES + ["all"],
        default="all",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    cities = CITIES if args.city == "all" else [args.city]
    for city in cities:
        print(f"\n{city}:")
        combine_city(city)
    print("\nDone.")


if __name__ == "__main__":
    main()