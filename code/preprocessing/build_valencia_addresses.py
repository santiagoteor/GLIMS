

from __future__ import annotations

import geopandas as gpd
import pandas as pd


PORTALS_PATH   = "data/valencia/portals_valencia_full.geojson"
DISTRICTS_PATH = "raw_data/Valencia/distritos_valencia.geoJSON"
BARRIOS_PATH   = "raw_data/Valencia/barrios_valencia.geoJSON"
SECCIONS_PATH  = "raw_data/Valencia/secc_cens_valencia.geoJSON"

OUTPUT_PATH = "data/valencia/direcciones_valencia.csv"

# Source columns holding the administrative CODE of each layer.
DISTRICT_CODE_COL = "coddistrit"    # -> districte
BARRIO_CODE_COL   = "coddistbar"    # -> barri  (city-unique: district+barrio)
SECCION_CODE_COL  = "coddistsecc"   # -> secc_cens (district+section)

# Portal attribute columns.
STREET_CODE_COL = "codvia"          # -> codi_carrer
NUMBER_COL      = "numportal"       # -> numpost

# CRS used for the x_etrs89 / y_etrs89 columns (ETRS89 / UTM zone 30N).
ETRS89_UTM30N = 25830

FINAL_COLUMNS = [
    "codi_carrer", "numpost", "llepost", "tipusnum", "districte", "barri",
    "secc_est", "secc_cens", "dist_post", "x_ed50", "y_ed50",
    "x_etrs89", "y_etrs89", "longitud_wgs84", "latitud_wgs84",
]


def load_wgs84(path: str, name: str) -> gpd.GeoDataFrame:
    """Load a GeoJSON and return it in EPSG:4326, inferring CRS if missing."""
    gdf = gpd.read_file(path)

    if gdf.crs is None:
        minx, miny, maxx, maxy = gdf.total_bounds
        if abs(maxx) <= 180 and abs(maxy) <= 90:
            gdf = gdf.set_crs(4326)
            print(f"  {name}: no CRS declared, looks like lon/lat -> assuming EPSG:4326")
        else:
            gdf = gdf.set_crs(ETRS89_UTM30N)
            print(f"  {name}: no CRS declared, projected coords -> assuming EPSG:{ETRS89_UTM30N}")
    else:
        print(f"  {name}: CRS = {gdf.crs.to_string()}")

    return gdf.to_crs(4326)


def attach_code(
    points: gpd.GeoDataFrame,
    polygons: gpd.GeoDataFrame,
    code_col: str,
    out_col: str,
) -> pd.Series:
    """Point-in-polygon join; return the polygon code for each point (by row)."""
    right = polygons[[code_col, "geometry"]].rename(columns={code_col: out_col})
    joined = gpd.sjoin(points[["_row", "geometry"]], right, how="left", predicate="within")

    # A point on a shared border can match >1 polygon; keep the first.
    joined = joined.drop_duplicates(subset="_row", keep="first")
    return joined.set_index("_row")[out_col]



def main() -> None:
    print("Loading layers and normalizing CRS to EPSG:4326:")
    portals   = load_wgs84(PORTALS_PATH, "portals")
    districts = load_wgs84(DISTRICTS_PATH, "districts")
    barrios   = load_wgs84(BARRIOS_PATH, "barrios")
    sections  = load_wgs84(SECCIONS_PATH, "sections")

    portals = portals[portals.geometry.type == "Point"].reset_index(drop=True)
    portals["_row"] = range(len(portals))

    print(f"\nPortals: {len(portals)}")

    print("Spatial joins (point in polygon)...")
    districte = attach_code(portals, districts, DISTRICT_CODE_COL, "districte")
    barri     = attach_code(portals, barrios,   BARRIO_CODE_COL,   "barri")
    secc_cens = attach_code(portals, sections,  SECCION_CODE_COL,  "secc_cens")

    lon = portals.geometry.x
    lat = portals.geometry.y

    etrs = portals.to_crs(ETRS89_UTM30N)
    x_etrs = etrs.geometry.x
    y_etrs = etrs.geometry.y

    out = pd.DataFrame(index=portals["_row"])
    out["codi_carrer"]    = portals[STREET_CODE_COL].values
    out["numpost"]        = portals[NUMBER_COL].values
    out["llepost"]        = ""      # no clean source in Valencia data
    out["tipusnum"]       = ""      # no source
    out["districte"]      = districte
    out["barri"]          = barri
    out["secc_est"]       = ""      # Valencia has no separate statistical section
    out["secc_cens"]      = secc_cens
    out["dist_post"]      = ""      # no postal code in these layers
    out["x_ed50"]         = ""      # ED50 not provided by the source
    out["y_ed50"]         = ""
    out["x_etrs89"]       = x_etrs.values
    out["y_etrs89"]       = y_etrs.values
    out["longitud_wgs84"] = lon.values
    out["latitud_wgs84"]  = lat.values

    out = out[FINAL_COLUMNS]

    admin_cols = ["districte", "barri", "secc_cens"]
    for col in admin_cols:
        missing = out[col].isna().sum()
        if missing:
            print(f"  [!] {missing} portals with no {col} (fell outside those polygons)")


    before = len(out)
    out = out.dropna(subset=admin_cols, how="any")
    dropped = before - len(out)
    if dropped:
        print(f"  Dropped {dropped} portals missing district, barrio or section "
              f"({dropped / before:.1%} of {before}).")

    out.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(f"\nWrote {len(out)} rows -> {OUTPUT_PATH}")

if __name__ == "__main__":
    main()