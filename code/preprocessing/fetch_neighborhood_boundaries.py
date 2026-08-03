"""
Download real district and neighborhood boundaries from OpenStreetMap.

For each configured city this script fetches administrative boundaries and
saves them as GeoJSON, preserving the actual polygons of each district and
neighborhood.

OSM admin_level convention for Spain:
    admin_level 9  -> distrito (district)
    admin_level 10 -> barrio   (neighborhood)

Output, per city, under DATA_DIR/<city>/:
    limites_distritos.geojson
    limites_barrios.geojson

Requirements (run inside your GLIMS env, with internet access):
    pip install osmnx geopandas shapely

Usage:
    python -m code.preprocessing.fetch_neighborhood_boundaries
    python -m code.preprocessing.fetch_neighborhood_boundaries --city barcelona
"""

from __future__ import annotations

import argparse

import geopandas as gpd
import osmnx as ox

from code.common.paths import DATA_DIR

print(">>> DATA_DIR =", DATA_DIR)

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

# OSM place strings. Being explicit avoids confusing the *city* of
# Valencia with the *province* of the same name during geocoding.
CITY_PLACES = {
    "madrid": "Madrid, Comunidad de Madrid, España",
    "barcelona": "Barcelona, Catalunya, España",
    "valencia": "València, Comunitat Valenciana, España",
}

# admin_level -> (label, output filename stem)
# NOTE: `label` becomes the name column inside the output GeoJSON, and it is
# also the value that ends up in the `tipo` column further down the pipeline
# (build_simulation_zones -> osrm_simulator). Whatever you put here is what
# you will type after the colon in `--zones "<name>:<label>"`.
ADMIN_LEVELS = {
    "9": ("district", "limites_distritos"),
    "10": ("neighborhood", "limites_barrios"),
}

# Known official counts, only as a sanity check printed to the console.
# (None where you should confirm against the town-hall figures.)
EXPECTED_COUNTS = {
    "madrid": {"district": 21, "neighborhood": 131},
    "barcelona": {"district": 10, "neighborhood": 73},
    "valencia": {"district": 19, "neighborhood": None},
}


# ---------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------

def fetch_city_boundaries(city: str, place: str) -> gpd.GeoDataFrame:
    """Fetch admin_level 9 and 10 boundaries for one city from OSM."""

    print(f"\nQuerying OSM for {city} ({place})...")

    features = ox.features_from_place(
        place,
        tags={
            "boundary": "administrative",
            "admin_level": list(ADMIN_LEVELS.keys()),
        },
    )

    # Keep only polygonal boundaries with a name and a known admin_level.
    features = features[features.geometry.type.isin(["Polygon", "MultiPolygon"])]
    features = features[features["admin_level"].isin(ADMIN_LEVELS.keys())]
    features = features[features["name"].notna()].copy()

    return features.to_crs(epsg=4326)


def save_level(
    features: gpd.GeoDataFrame,
    city: str,
    admin_level: str,
) -> gpd.GeoDataFrame | None:
    """Filter one admin level, normalize columns, and write GeoJSON."""

    label, stem = ADMIN_LEVELS[admin_level]
    subset = features[features["admin_level"] == admin_level].copy()

    if subset.empty:
        print(f"  [!] No {label}s found in OSM for {city}.")
        return None

    # Normalize to a minimal, predictable schema.
    subset = subset.rename(columns={"name": label})
    keep_columns = [label, "admin_level", "geometry"]
    subset = subset[keep_columns].reset_index(drop=True)

    city_folder = DATA_DIR / city
    city_folder.mkdir(parents=True, exist_ok=True)
    output_path = city_folder / f"{stem}.geojson"

    print("DATA_DIR:", DATA_DIR)
    print("city_folder:", city_folder)
    print("output_path:", output_path)

    subset.to_file(output_path, driver="GeoJSON")

    expected = EXPECTED_COUNTS.get(city, {}).get(label)
    expected_note = f" (expected ~{expected})" if expected else ""
    print(
        f"  {label}s: {len(subset)} saved{expected_note} -> "
        f"{output_path.resolve()}"
    )

    return subset


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download OSM district/neighborhood boundaries."
    )
    parser.add_argument(
        "--city",
        choices=list(CITY_PLACES) + ["all"],
        default="all",
        help="City to fetch ('all' fetches every configured city).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    cities = list(CITY_PLACES) if args.city == "all" else [args.city]

    for city in cities:
        features = fetch_city_boundaries(city, CITY_PLACES[city])

        save_level(features, city, "9")
        save_level(features, city, "10")

    print("\nDone.")


if __name__ == "__main__":
    main()