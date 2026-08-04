"""
Combine OSM districts and neighborhoods into a single zones file per city.

Produces one GeoJSON per city containing every district (admin_level 9)
and every neighborhood (admin_level 10), each tagged with its type. WHICH
zones to actually simulate is decided later, at simulation time
(osrm_simulator --zones ...), so this step is level-agnostic and needs no
hardcoded list.

Output columns (names kept in Spanish because osrm_simulator reads them by
these exact names; the *values* of `tipo` are English):
    zona          zone name (from OSM 'name')
    tipo          'district' or 'neighborhood'   <- what the zone is
    admin_level   '9' or '10'
    geometry      the real polygon

Output, per city:
    DATA_DIR/<city>/limites_zonas.geojson

Run as a module from the project root:
    python -m code.preprocessing.build_simulation_zones
    python -m code.preprocessing.build_simulation_zones --city barcelona
"""

from __future__ import annotations

import argparse

import geopandas as gpd
import pandas as pd

from code.common.paths import DATA_DIR

CITIES = ["madrid", "barcelona", "valencia"]

# source filename stem -> tipo label.
# The label is ALSO the name of the name-column inside the source file, as
# written by fetch_neighborhood_boundaries.py. Keep both scripts in sync.
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
        gdf = gdf.rename(columns={tipo: "zone"})   # district/neighborhood -> zona
        gdf["tipo"] = tipo                          # "district" or "neighborhood"
        parts.append(gdf[["zone", "type", "admin_level", "geometry"]])

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