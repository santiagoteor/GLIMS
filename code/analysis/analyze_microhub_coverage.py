"""Geographic 3-km microhub coverage at municipal, district and neighborhood scales.

Run from project root: python -m code.analysis.analyze_microhub_coverage
Requires: geopandas, shapely, pyproj, pandas, fiona/pyogrio.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from code.common.paths import DATA_DIR, RESULTS_DIR

CITIES = ("barcelona", "madrid", "valencia")
METRIC_EPSG = {"barcelona": 25831, "madrid": 25830, "valencia": 25830}
RADIUS_M = 3000.0


def _read_boundaries(path: Path, name_field: str, metric_crs: int) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing boundaries: {path}")
    data = gpd.read_file(path)
    if data.crs is None:
        raise ValueError(f"Missing CRS: {path}")
    if name_field not in data.columns:
        raise ValueError(f"Missing column {name_field!r}: {path}")
    data = data[[name_field, "geometry"]].rename(columns={name_field: "name"}).to_crs(metric_crs)
    if data.geometry.isna().any() or data.geometry.is_empty.any():
        raise ValueError(f"Invalid empty geometries: {path}")
    data["geometry"] = data.geometry.make_valid()
    return data.reset_index(drop=True)


def _stats(boundaries: gpd.GeoDataFrame, coverage, city: str, level: str) -> gpd.GeoDataFrame:
    out = boundaries.copy()
    areas = out.geometry.area
    covered = out.geometry.intersection(coverage).area
    out.insert(0, "city", city)
    out.insert(1, "level", level)
    out["area_km2"] = areas / 1e6
    out["covered_km2"] = covered / 1e6
    out["uncovered_km2"] = (areas - covered).clip(lower=0) / 1e6
    out["coverage_pct"] = (covered / areas.where(areas > 0)).fillna(0).clip(0, 1) * 100
    out["coverage_class"] = out["coverage_pct"].map(
        lambda p: "uncovered" if p < 1e-7 else "covered" if p > 100 - 1e-7 else "partial"
    )
    return out


def analyze_city(city: str, radius_m: float = RADIUS_M) -> dict:
    crs = METRIC_EPSG[city]
    folder = DATA_DIR / city
    output = RESULTS_DIR / city / "microhub_coverage"
    output.mkdir(parents=True, exist_ok=True)
    districts = _read_boundaries(folder / "districts_limits.geojson", "district", crs)
    neighborhoods = _read_boundaries(folder / "neighborhoods_limits.geojson", "neighborhood", crs)
    municipal = unary_union(districts.geometry.tolist())
    # District union is the study's municipal analysis extent, not a newly certified boundary.
    records = pd.read_csv(folder / "puntos_b2c.csv")
    required = {"ID", "Location", "Type", "Latitude", "Longitude"}
    if missing := required - set(records.columns):
        raise ValueError(f"{city}: missing fields in puntos_b2c.csv: {sorted(missing)}")
    hubs = records.loc[records["Type"].astype(str).str.strip().str.casefold().eq("microhub")].copy()
    hubs["Latitude"] = pd.to_numeric(hubs["Latitude"], errors="coerce")
    hubs["Longitude"] = pd.to_numeric(hubs["Longitude"], errors="coerce")
    if hubs.empty or hubs[["Latitude", "Longitude"]].isna().any().any():
        raise ValueError(f"{city}: missing microhubs or invalid coordinates")
    points = gpd.GeoDataFrame(
        hubs[["ID", "Location", "Latitude", "Longitude"]].rename(columns={"Location": "name"}),
        geometry=gpd.points_from_xy(hubs["Longitude"], hubs["Latitude"]), crs=4326,
    ).to_crs(crs)
    points["radius_m"] = radius_m
    buffers = points.copy()
    buffers["geometry"] = points.geometry.buffer(radius_m, resolution=64)
    union = unary_union(buffers.geometry.tolist())
    covered = municipal.intersection(union)
    uncovered = municipal.difference(union)
    municipal_gdf = gpd.GeoDataFrame({"name": [city], "geometry": [municipal]}, crs=crs)
    municipality = _stats(municipal_gdf, covered, city, "municipality")
    district_stats = _stats(districts, covered, city, "district")
    neighborhood_stats = _stats(neighborhoods, covered, city, "neighborhood")
    coverage_area = gpd.GeoDataFrame({"city": [city], "status": ["covered"], "geometry": [covered]}, crs=crs)
    uncovered_area = gpd.GeoDataFrame({"city": [city], "status": ["uncovered"], "geometry": [uncovered]}, crs=crs)
    # A single geopackage contains every QGIS layer. EPSG:4326 for portable exchange.
    gpkg = output / "microhub_coverage.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    layers = {
        "municipality": municipality,
        "district_coverage": district_stats,
        "neighborhood_coverage": neighborhood_stats,
        "microhubs": points,
        "microhub_buffers_3km": buffers,
        "covered_area": coverage_area,
        "uncovered_area": uncovered_area,
    }
    for layer, gdf in layers.items():
        gdf.to_crs(4326).to_file(gpkg, layer=layer, driver="GPKG")
    municipality.drop(columns="geometry").to_csv(output / "municipal_coverage.csv", index=False)
    district_stats.drop(columns="geometry").to_csv(output / "district_coverage.csv", index=False)
    neighborhood_stats.drop(columns="geometry").to_csv(output / "neighborhood_coverage.csv", index=False)
    record = municipality.drop(columns="geometry").iloc[0].to_dict()
    record["microhub_count"] = len(points)
    record["radius_m"] = radius_m
    print(f"{city}: {len(points)} microhubs; municipal coverage {record['coverage_pct']:.2f}%")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities", nargs="+", choices=CITIES, default=list(CITIES))
    parser.add_argument("--radius-m", type=float, default=RADIUS_M)
    args = parser.parse_args()
    if args.radius_m <= 0:
        parser.error("--radius-m must be positive")
    results = [analyze_city(city, args.radius_m) for city in args.cities]
    summary_path = RESULTS_DIR / "microhub_coverage_summary.csv"
    pd.DataFrame(results).to_csv(summary_path, index=False)
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
