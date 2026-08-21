import argparse
import colorsys
import hashlib
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from code.common.paths import PROJECT_ROOT, RESULTS_DIR

CITY = "madrid"
DEFAULT_INPUT_DIR = RESULTS_DIR / CITY / "demand"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "QGIS" / CITY

MODEL_COL = "model"
ROUTE_ID_COL = "route_id"
EXPERIMENT_COL = "experiment_id"
LEG_COL = "leg"

ROUTE_MARKER_COLS = {"geometry_wkt"}
STOP_MARKER_COLS = {"stop_id", "latitude", "longitude"}

DEFAULT_CRS = "EPSG:4326"

ROUTE_LINE_WIDTH_MM = 0.7
STOP_SIZE_MM = 2.6
FALLBACK_COLOR = "120,120,120,255"


# --------------------------------------------------------------------------
# File discovery and classification
# --------------------------------------------------------------------------

def gather_csvs(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.csv")))
        elif p.exists():
            files.append(p)
        else:
            print(f"[warning] not found, skipping: {p}")
    return files


def classify_csv(path: Path):
    """Returns 'routes', 'stops' or None by looking only at the header."""
    try:
        header = pd.read_csv(path, nrows=0).columns
    except Exception as e:
        print(f"  [warning] could not read header of {path.name}: {e}")
        return None
    cols = set(header)
    if ROUTE_MARKER_COLS.issubset(cols):
        return "routes"
    if STOP_MARKER_COLS.issubset(cols):
        return "stops"
    return None


def load_and_concat(files, kind):
    frames = []
    for f in files:
        df = pd.read_csv(f)
        frames.append(df)
        print(f"  [{kind}] {f.name}: {len(df)} rows")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


# --------------------------------------------------------------------------
# Conversion to GeoDataFrame
# --------------------------------------------------------------------------

def routes_to_gdf(df: pd.DataFrame):
    import geopandas as gpd
    from shapely import wkt

    df = df.copy()
    before = len(df)
    df = df.dropna(subset=["geometry_wkt"])
    dropped = before - len(df)
    if dropped:
        print(f"    [info] {dropped} routes without geometry_wkt dropped")

    geometry = df["geometry_wkt"].apply(wkt.loads)
    df = df.drop(columns=["geometry_wkt"])
    return gpd.GeoDataFrame(df, geometry=geometry, crs=DEFAULT_CRS)


def stops_to_gdf(df: pd.DataFrame):
    import geopandas as gpd
    from shapely.geometry import Point

    df = df.copy()
    before = len(df)
    df = df.dropna(subset=["latitude", "longitude"])
    dropped = before - len(df)
    if dropped:
        print(f"    [info] {dropped} stops without lat/lon dropped")

    geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
    return gpd.GeoDataFrame(df, geometry=geometry, crs=DEFAULT_CRS)


# --------------------------------------------------------------------------
# Deterministic color per route
# --------------------------------------------------------------------------

def route_color_rgb(route_id: str):
    """Stable color (same route_id -> always the same color)."""
    h = int(hashlib.md5(str(route_id).encode("utf-8")).hexdigest(), 16)
    hue = (h % 360) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.68, 0.88)
    return f"{int(r * 255)},{int(g * 255)},{int(b * 255)},255"


def model_color_rgb(model: str):
    """A fixed color per model (for --no-by-route mode)."""
    palette = {
        "M1": "230,57,70,255",
        "M2": "42,157,143,255",
        "M3": "38,70,140,255",
        "M4": "233,150,20,255",
        "M5": "142,68,173,255",
    }
    return palette.get(model, FALLBACK_COLOR)


# --------------------------------------------------------------------------
# Building QML styles (categorized by route)
# --------------------------------------------------------------------------

def _escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_line_qml(route_ids_colors, by_route: bool, single_color: str):
    """categorizedSymbol renderer (by route_id) or singleSymbol for lines."""
    if not by_route:
        return _single_symbol_qml_line(single_color)

    symbols_xml = []
    categories_xml = []
    for idx, (rid, color) in enumerate(route_ids_colors):
        sym_id = str(idx)
        symbols_xml.append(_line_symbol_xml(sym_id, color))
        categories_xml.append(
            f'<category value="{_escape(rid)}" symbol="{sym_id}" '
            f'render="true" label="{_escape(rid)}"/>'
        )
    # default symbol for unexpected values (NULL / other)
    default_id = str(len(route_ids_colors))
    symbols_xml.append(_line_symbol_xml(default_id, FALLBACK_COLOR))
    categories_xml.append(
        f'<category value="" symbol="{default_id}" render="true" label="other"/>'
    )

    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology">
  <renderer-v2 type="categorizedSymbol" attr="{ROUTE_ID_COL}" symbollevels="0" enableorderby="0" forceraster="0">
    <categories>
      {''.join(categories_xml)}
    </categories>
    <symbols>
      {''.join(symbols_xml)}
    </symbols>
  </renderer-v2>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
</qgis>"""


def _single_symbol_qml_line(color: str):
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology">
  <renderer-v2 type="singleSymbol" symbollevels="0" enableorderby="0" forceraster="0">
    <symbols>
      {_line_symbol_xml("0", color)}
    </symbols>
    <rotation/>
    <sizescale/>
  </renderer-v2>
</qgis>"""


def _line_symbol_xml(sym_id: str, color: str):
    return f"""<symbol type="line" name="{sym_id}" alpha="1" force_rhr="0" clip_to_extent="1">
        <layer class="SimpleLine" locked="0" pass="0" enabled="1">
          <Option type="Map">
            <Option type="QString" name="line_color" value="{color}"/>
            <Option type="QString" name="line_width" value="{ROUTE_LINE_WIDTH_MM}"/>
            <Option type="QString" name="line_width_unit" value="MM"/>
            <Option type="QString" name="line_style" value="solid"/>
            <Option type="QString" name="capstyle" value="round"/>
            <Option type="QString" name="joinstyle" value="round"/>
          </Option>
        </layer>
      </symbol>"""


def build_point_qml(route_ids_colors, by_route: bool, single_color: str):
    if not by_route:
        return _single_symbol_qml_point(single_color)

    symbols_xml = []
    categories_xml = []
    for idx, (rid, color) in enumerate(route_ids_colors):
        sym_id = str(idx)
        symbols_xml.append(_point_symbol_xml(sym_id, color, STOP_SIZE_MM))
        categories_xml.append(
            f'<category value="{_escape(rid)}" symbol="{sym_id}" '
            f'render="true" label="{_escape(rid)}"/>'
        )
    default_id = str(len(route_ids_colors))
    symbols_xml.append(_point_symbol_xml(default_id, FALLBACK_COLOR, STOP_SIZE_MM))
    categories_xml.append(
        f'<category value="" symbol="{default_id}" render="true" label="other"/>'
    )

    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology">
  <renderer-v2 type="categorizedSymbol" attr="{ROUTE_ID_COL}" symbollevels="0" enableorderby="0" forceraster="0">
    <categories>
      {''.join(categories_xml)}
    </categories>
    <symbols>
      {''.join(symbols_xml)}
    </symbols>
  </renderer-v2>
</qgis>"""


def _single_symbol_qml_point(color: str):
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology">
  <renderer-v2 type="singleSymbol" symbollevels="0" enableorderby="0" forceraster="0">
    <symbols>
      {_point_symbol_xml("0", color, STOP_SIZE_MM)}
    </symbols>
    <rotation/>
    <sizescale/>
  </renderer-v2>
</qgis>"""


def _point_symbol_xml(sym_id: str, color: str, size_mm: float):
    return f"""<symbol type="marker" name="{sym_id}" alpha="1" force_rhr="0" clip_to_extent="1">
        <layer class="SimpleMarker" locked="0" pass="0" enabled="1">
          <Option type="Map">
            <Option type="QString" name="name" value="circle"/>
            <Option type="QString" name="color" value="{color}"/>
            <Option type="QString" name="outline_color" value="35,35,35,255"/>
            <Option type="QString" name="outline_width" value="0.2"/>
            <Option type="QString" name="outline_width_unit" value="MM"/>
            <Option type="QString" name="size" value="{size_mm}"/>
            <Option type="QString" name="size_unit" value="MM"/>
            <Option type="QString" name="scale_method" value="area"/>
          </Option>
        </layer>
      </symbol>"""


# --------------------------------------------------------------------------
# Writing to GeoPackage + embedded style (layer_styles table)
# --------------------------------------------------------------------------

def ensure_layer_styles_table(gpkg_path: Path):
    con = sqlite3.connect(gpkg_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS layer_styles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                f_table_catalog TEXT(256),
                f_table_schema TEXT(256),
                f_table_name TEXT(256),
                f_geometry_column TEXT(256),
                styleName TEXT(30),
                styleQML TEXT,
                styleSLD TEXT,
                useAsDefault BOOLEAN,
                description TEXT,
                owner TEXT,
                ui TEXT(30),
                update_time DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.commit()
    finally:
        con.close()


def write_layer_style(gpkg_path: Path, layer_name: str, qml: str):
    con = sqlite3.connect(gpkg_path)
    try:
        con.execute(
            "DELETE FROM layer_styles WHERE f_table_name = ?", (layer_name,)
        )
        con.execute(
            """
            INSERT INTO layer_styles
                (f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                 styleName, styleQML, useAsDefault, description, owner)
            VALUES ('', '', ?, 'geom', ?, ?, 1, 'auto-generated', '')
            """,
            (layer_name, f"{layer_name}_style", qml),
        )
        con.commit()
    finally:
        con.close()


def route_palette(gdf, by_route: bool, model: str):
    """Sorted list of unique [(route_id, color_rgb_str), ...]."""
    if not by_route:
        return []
    ids = sorted(gdf[ROUTE_ID_COL].dropna().unique().tolist())
    return [(rid, route_color_rgb(rid)) for rid in ids]


def limit_routes_per_model(routes_df: pd.DataFrame, stops_df: pd.DataFrame, max_routes):
    """Keep at most `max_routes` unique route_id values (and their matching stops).

    Selection is deterministic (sorted route_id order), so repeated runs with the
    same input produce the same sample. Pass max_routes=None to keep every route
    (default behavior, unchanged from before).
    """
    if max_routes is None:
        return routes_df, stops_df

    available_ids = []
    if len(routes_df) and ROUTE_ID_COL in routes_df.columns:
        available_ids = sorted(routes_df[ROUTE_ID_COL].dropna().unique().tolist())
    elif len(stops_df) and ROUTE_ID_COL in stops_df.columns:
        available_ids = sorted(stops_df[ROUTE_ID_COL].dropna().unique().tolist())

    if not available_ids:
        return routes_df, stops_df

    selected_ids = set(available_ids[:max_routes])
    if len(selected_ids) < len(available_ids):
        print(f"    [info] sampling {len(selected_ids)} of {len(available_ids)} routes")

    routes_out = (
        routes_df[routes_df[ROUTE_ID_COL].isin(selected_ids)]
        if len(routes_df) and ROUTE_ID_COL in routes_df.columns
        else routes_df
    )
    stops_out = (
        stops_df[stops_df[ROUTE_ID_COL].isin(selected_ids)]
        if len(stops_df) and ROUTE_ID_COL in stops_df.columns
        else stops_df
    )
    return routes_out, stops_out


def write_model_layers(gpkg_path: Path, model: str, routes_gdf, stops_gdf, by_route: bool):
    layer_written = False

    if routes_gdf is not None and len(routes_gdf):
        name = f"{model}_routes"
        routes_gdf.to_file(gpkg_path, layer=name, driver="GPKG")
        palette = route_palette(routes_gdf, by_route, model)
        qml = build_line_qml(palette, by_route, model_color_rgb(model))
        ensure_layer_styles_table(gpkg_path)
        write_layer_style(gpkg_path, name, qml)
        print(f"    layer {name}: {len(routes_gdf)} routes"
              f"{' (colored by route_id)' if by_route else ''}")
        layer_written = True

    if stops_gdf is not None and len(stops_gdf):
        name = f"{model}_stops"
        stops_gdf.to_file(gpkg_path, layer=name, driver="GPKG")
        palette = route_palette(stops_gdf, by_route, model)
        qml = build_point_qml(palette, by_route, model_color_rgb(model))
        ensure_layer_styles_table(gpkg_path)
        write_layer_style(gpkg_path, name, qml)
        print(f"    layer {name}: {len(stops_gdf)} stops")
        layer_written = True

    return layer_written


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(inputs, out_dir: Path, by_route: bool, routes_per_model=None):
    files = gather_csvs(inputs)
    if not files:
        raise SystemExit("No input CSV files found.")

    route_files, stop_files, skipped = [], [], []
    for f in files:
        kind = classify_csv(f)
        if kind == "routes":
            route_files.append(f)
        elif kind == "stops":
            stop_files.append(f)
        else:
            skipped.append(f)

    if skipped:
        print("[info] ignored files (not recognized as routes or stops):")
        for f in skipped:
            print(f"    - {f.name}")

    print(f"Route files: {len(route_files)} | Stop files: {len(stop_files)}")

    routes_df = load_and_concat(route_files, "routes")
    stops_df = load_and_concat(stop_files, "stops")

    if routes_df.empty and stops_df.empty:
        raise SystemExit("No route or stop data to export.")

    experiment_ids = sorted(
        set(routes_df.get(EXPERIMENT_COL, pd.Series(dtype=str)).dropna().unique().tolist())
        | set(stops_df.get(EXPERIMENT_COL, pd.Series(dtype=str)).dropna().unique().tolist())
    )
    if not experiment_ids:
        experiment_ids = ["default"]

    out_dir.mkdir(parents=True, exist_ok=True)

    for exp_id in experiment_ids:
        exp_routes = routes_df[routes_df.get(EXPERIMENT_COL) == exp_id] if not routes_df.empty else routes_df
        exp_stops = stops_df[stops_df.get(EXPERIMENT_COL) == exp_id] if not stops_df.empty else stops_df

        models = sorted(
            set(exp_routes[MODEL_COL].dropna().unique().tolist() if not exp_routes.empty else [])
            | set(exp_stops[MODEL_COL].dropna().unique().tolist() if not exp_stops.empty else [])
        )
        if not models:
            print(f"[warning] experiment_id={exp_id}: no usable 'model' column, skipping")
            continue

        gpkg_path = out_dir / f"{exp_id}.gpkg"
        if gpkg_path.exists():
            gpkg_path.unlink()

        print(f"\n=== experiment_id={exp_id} -> {gpkg_path.name} ===")
        for model in models:
            m_routes = exp_routes[exp_routes[MODEL_COL] == model] if not exp_routes.empty else exp_routes
            m_stops = exp_stops[exp_stops[MODEL_COL] == model] if not exp_stops.empty else exp_stops
            m_routes, m_stops = limit_routes_per_model(m_routes, m_stops, routes_per_model)

            routes_gdf = routes_to_gdf(m_routes) if len(m_routes) else None
            stops_gdf = stops_to_gdf(m_stops) if len(m_stops) else None

            print(f"  model {model}:")
            write_model_layers(gpkg_path, model, routes_gdf, stops_gdf, by_route)

        print(f"  written: {gpkg_path}")


def parse_args():
    ap = argparse.ArgumentParser(
        description="Exports routes (linestrings) and stops (points) to per-model "
                    "GeoPackages, ready for QGIS with embedded styling."
    )
    ap.add_argument("inputs", nargs="*",
                     help="Route/stop CSVs or folders containing them "
                          f"(default: {DEFAULT_INPUT_DIR}).")
    ap.add_argument("-o", "--output-dir",
                     help=f"Output folder (default: {DEFAULT_OUTPUT_DIR}).")
    ap.add_argument("--by-route", dest="by_route", action="store_true", default=True,
                     help="Color each route differently within each model layer "
                          "(default).")
    ap.add_argument("--no-by-route", dest="by_route", action="store_false",
                     help="A single color per model, without differentiating routes.")
    ap.add_argument("--routes-per-model", type=int, default=None, metavar="N",
                     help="If set, keep at most N routes per model (and their "
                          "matching stops) instead of all of them — e.g. "
                          "--routes-per-model 5 for a quick preview. The N routes "
                          "are chosen deterministically (sorted route_id), so the "
                          "same sample is produced on every run. Default: show all "
                          "routes.")
    args = ap.parse_args()

    if not args.inputs:
        args.inputs = [str(DEFAULT_INPUT_DIR)]
    args.output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    return args


def main():
    a = parse_args()
    run(a.inputs, a.output_dir, a.by_route, a.routes_per_model)
    print("\nDone.")


if __name__ == "__main__":
    main()