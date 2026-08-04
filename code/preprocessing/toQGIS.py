import argparse
import json
import math
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
GLIMS_DIR = BASE_DIR.parent.parent
CITY = "madrid"
DEFAULT_INPUT_DIR = GLIMS_DIR / "results" / CITY / "demand"
DEFAULT_OUTPUT_DIR = GLIMS_DIR / "QGIS" / CITY

LON_CANDIDATES = ["lon", "longitude", "longitud", "lng", "x_wgs84"]
LAT_CANDIDATES = ["lat", "latitude", "latitud", "y_wgs84"]
X_CANDIDATES = ["x_utm", "x_etrs89", "x", "coord_x", "utm_x"]
Y_CANDIDATES = ["y_utm", "y_etrs89", "y", "coord_y", "utm_y"]

VECTOR_EXTS = {".geojson", ".json", ".gpkg", ".shp", ".gml", ".kml"}

DEFAULT_SOURCE_CRS = "EPSG:25830"
DEFAULT_TARGET_CRS = "EPSG:4326"

def _norm(s: str) -> str:
    return str(s).strip().lower()


def _pick(columns, candidates):
    """Return the first column (original name) matching a candidate."""
    norm_map = {_norm(c): c for c in columns}
    for cand in candidates:
        if cand in norm_map:
            return norm_map[cand]
    return None


def _clean_value(v):
    """Make a value JSON-serializable (NaN/NA -> None, numpy -> python)."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):          # numpy scalar
        try:
            return v.item()
        except Exception:
            pass
    if isinstance(v, (pd.Timestamp,)):
        return str(v)
    return v


def _safe_layer_name(path: Path) -> str:
    """A clean layer name from a filename (no spaces/dots)."""
    stem = path.stem
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in stem)

def load_point_csv(path: Path, lon_col=None, lat_col=None,
                   source_crs=DEFAULT_SOURCE_CRS):
    """
    Read a point CSV. Returns a dict describing the layer:
        {name, kind:'point', df, lon, lat, x, y, crs}
    Prefers lon/lat (assumed WGS84). Falls back to x/y (source_crs).
    """
    df = pd.read_csv(path)

    lon = lon_col or _pick(df.columns, LON_CANDIDATES)
    lat = lat_col or _pick(df.columns, LAT_CANDIDATES)
    x = _pick(df.columns, X_CANDIDATES)
    y = _pick(df.columns, Y_CANDIDATES)

    if lon and lat:
        crs = "EPSG:4326"
        xcol, ycol = lon, lat
    elif x and y:
        crs = source_crs
        xcol, ycol = x, y
        print(f"    [info] no lon/lat found; using '{x}'/'{y}' as {source_crs}")
    else:
        raise ValueError(
            f"{path.name}: could not find coordinate columns. "
            f"Looked for lon/lat {LON_CANDIDATES}/{LAT_CANDIDATES} "
            f"or x/y {X_CANDIDATES}/{Y_CANDIDATES}. "
            "Pass --lon-col/--lat-col explicitly."
        )

    # drop rows without coordinates
    before = len(df)
    df = df.dropna(subset=[xcol, ycol]).copy()
    dropped = before - len(df)
    if dropped:
        print(f"    [info] dropped {dropped} rows without coordinates")

    return {
        "name": _safe_layer_name(path),
        "kind": "point",
        "df": df,
        "xcol": xcol,
        "ycol": ycol,
        "crs": crs,
    }


def point_layer_to_geojson(layer: dict) -> dict:
    df = layer["df"]
    xcol, ycol = layer["xcol"], layer["ycol"]
    prop_cols = [c for c in df.columns if c not in (xcol, ycol)]

    features = []
    for row in df.itertuples(index=False):
        rec = dict(zip(df.columns, row))
        lon = _clean_value(rec[xcol])
        lat = _clean_value(rec[ycol])
        if lon is None or lat is None:
            continue
        props = {c: _clean_value(rec[c]) for c in prop_cols}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    fc = {"type": "FeatureCollection", "features": features}

    if layer["crs"] not in ("EPSG:4326", "epsg:4326"):
        code = layer["crs"].split(":")[-1]
        fc["crs"] = {"type": "name",
                     "properties": {"name": f"urn:ogc:def:crs:EPSG::{code}"}}
    return fc


def write_geojson(fc: dict, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    return out_path


def _lazy_gpd():
    try:
        import geopandas as gpd  
        return gpd
    except ImportError as e:
        raise SystemExit(
            "This action needs geopandas. Install it with:\n"
            "    pip install geopandas pyogrio --break-system-packages\n"
            "(or use '-f geojson' for point CSVs, which needs only pandas)."
        ) from e


def point_layer_to_gdf(layer: dict, target_crs=None):
    gpd = _lazy_gpd()
    from shapely.geometry import Point
    df = layer["df"]
    xcol, ycol = layer["xcol"], layer["ycol"]
    geom = [Point(xy) for xy in zip(df[xcol], df[ycol])]
    gdf = gpd.GeoDataFrame(df.copy(), geometry=geom, crs=layer["crs"])
    if target_crs and str(target_crs).lower() != str(layer["crs"]).lower():
        gdf = gdf.to_crs(target_crs)
    return gdf


def load_vector_gdf(path: Path, target_crs=None):
    gpd = _lazy_gpd()
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        print(f"    [warning] {path.name} has no CRS; assuming EPSG:4326")
        gdf = gdf.set_crs("EPSG:4326")
    if target_crs and str(gdf.crs).lower() != str(target_crs).lower():
        gdf = gdf.to_crs(target_crs)
    return gdf


def describe_gdf(name, gdf):
    geom_types = ", ".join(sorted(set(gdf.geom_type.dropna())))
    print(f"    {name}: {len(gdf)} features | {geom_types} | CRS {gdf.crs}")


def gather_inputs(paths):
    """Expand dirs into files; classify each into 'csv' or 'vector'."""
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.csv")))
            files.extend(sorted(p.glob("*.geojson")))
        elif p.exists():
            files.append(p)
        else:
            print(f"[warning] not found, skipping: {p}")
    return files


def run(inputs, out_path, fmt, target_crs, source_crs, lon_col, lat_col):
    files = gather_inputs(inputs)
    if not files:
        raise SystemExit("No input files found.")

    print(f"Converting {len(files)} file(s) -> {fmt}")

    # --- Pure GeoJSON fast path for point CSVs only ---------------------
    only_csv = all(f.suffix.lower() == ".csv" for f in files)
    if fmt == "geojson" and only_csv and str(target_crs) == DEFAULT_TARGET_CRS:
        out_dir = Path(out_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            layer = load_point_csv(f, lon_col, lat_col, source_crs)
            fc = point_layer_to_geojson(layer)
            dest = write_geojson(fc, out_dir / f"{layer['name']}.geojson")
            print(f"  wrote {dest}  ({len(fc['features'])} features)")
        return

    gpd = _lazy_gpd()
    layers = {}
    for f in files:
        if f.suffix.lower() == ".csv":
            layer = load_point_csv(f, lon_col, lat_col, source_crs)
            gdf = point_layer_to_gdf(layer, target_crs)
            name = layer["name"]
        elif f.suffix.lower() in VECTOR_EXTS:
            gdf = load_vector_gdf(f, target_crs)
            name = _safe_layer_name(f)
        else:
            print(f"  [warning] unsupported extension, skipping: {f.name}")
            continue
        layers[name] = gdf
        describe_gdf(name, gdf)

    if fmt == "gpkg":
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            out_path.unlink()  # start fresh so layers don't duplicate
        for name, gdf in layers.items():
            gdf.to_file(out_path, layer=name, driver="GPKG")
        print(f"  wrote {out_path}  ({len(layers)} layer(s))")

    elif fmt == "geojson":
        out_dir = Path(out_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, gdf in layers.items():
            dest = out_dir / f"{name}.geojson"
            gdf.to_file(dest, driver="GeoJSON")
            print(f"  wrote {dest}")

    elif fmt == "shapefile":
        out_dir = Path(out_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        print("  [warning] Shapefile truncates field names to 10 chars "
              "and splits into several files; prefer gpkg if possible.")
        for name, gdf in layers.items():
            dest = out_dir / f"{name}.shp"
            gdf.to_file(dest, driver="ESRI Shapefile")
            print(f"  wrote {dest}")


def parse_args():
    ap = argparse.ArgumentParser(description="Convert GLIMS results / geodata to QGIS formats.")
    ap.add_argument("inputs", nargs="*",
                    help="CSV point files, vector files, or directories "
                         f"(default: {DEFAULT_INPUT_DIR}).")
    ap.add_argument("-o", "--output",
                    help="Output .gpkg file (for -f gpkg) or output directory "
                         "(for geojson/shapefile).")
    ap.add_argument("-f", "--format", choices=["gpkg", "geojson", "shapefile"],
                    default="gpkg", help="Output format (default: gpkg).")
    ap.add_argument("--target-crs", default=DEFAULT_TARGET_CRS,
                    help="CRS of the output (default: EPSG:4326 / WGS84).")
    ap.add_argument("--source-crs", default=DEFAULT_SOURCE_CRS,
                    help="CRS to assume for x/y columns lacking lon/lat "
                         f"(default: {DEFAULT_SOURCE_CRS}).")
    ap.add_argument("--lon-col", help="Force the longitude column name.")
    ap.add_argument("--lat-col", help="Force the latitude column name.")
    args = ap.parse_args()

    if not args.inputs:
        args.inputs = [str(DEFAULT_INPUT_DIR)]
    if not args.output:
        if args.format == "gpkg":
            args.output = str(DEFAULT_OUTPUT_DIR / f"{CITY}.gpkg")
        else:
            args.output = str(DEFAULT_OUTPUT_DIR)
    return args


def main():
    a = parse_args()
    run(a.inputs, a.output, a.format, a.target_crs, a.source_crs,
        a.lon_col, a.lat_col)
    print("Done.")


if __name__ == "__main__":
    main()