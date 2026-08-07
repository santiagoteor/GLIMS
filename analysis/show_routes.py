#!/usr/bin/env python3
"""
draw_routes.py
==============

Dibuja las rutas de un archivo ``*_routes.csv`` del simulador GLIMS como
GeoJSON listo para QGIS (una capa de líneas + una capa de puntos).

Resuelve cada parada de la columna ``stop_sequence`` a coordenadas reales:

    * direct_delivery / cycling_last_mile / walking_last_mile /
      customer_collection  ->  las etiquetas son ``customer_id`` y se buscan
      en el archivo de demanda (results/<city>/demand/demand_<sc>_<size>.csv).
    * facility_supply  ->  las etiquetas son nombres ``Location`` de facilities
      y se buscan en records_classified.csv / centros_cc.csv.

El *depósito* (origen/fin de cada ruta de ida y vuelta) sale de la columna
``depot``:

    * direct_delivery / facility_supply  ->  centro logístico (centros_cc.csv)
    * piernas de última milla             ->  microhub / PUDO (records_classified)

La geometría se obtiene del servicio OSRM ``/route`` (por calles), usando el
puerto del perfil correcto según ``vehicle_type``. Si OSRM falla y no se
desactiva el fallback, se dibuja una línea recta entre paradas.

Ejemplos
--------
Listar las rutas disponibles en un archivo::

    python -m code.viz.draw_routes ruta/a/m3_routes.csv --list

Dibujar una ruta concreta::

    python -m code.viz.draw_routes ruta/a/m1_routes.csv \\
        --route-id "M1_l'Eixample_direct_delivery_DCT9 Amazon Logistics_1"

Dibujar todas las rutas del archivo::

    python -m code.viz.draw_routes ruta/a/m3_routes.csv --all

Resolver facilities sintéticas (facility_6, ...) con un override::

    python -m code.viz.draw_routes ruta/a/m3_routes.csv --all \\
        --facility-coords mis_facilities.csv
    # mis_facilities.csv:  name,lat,lon

Se ejecuta desde la raíz del proyecto (para que el import de ``code.*``
funcione), por ejemplo con ``python -m code.viz.draw_routes ...`` tras
colocar este archivo en ``code/viz/``. También funciona de forma autónoma
pasando ``--data-dir`` y ``--results-dir``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# --- Rutas y puertos del proyecto (con fallback a overrides de CLI) ----------
try:
    from code.common.paths import DATA_DIR as _PROJECT_DATA_DIR
    from code.common.paths import RESULTS_DIR as _PROJECT_RESULTS_DIR
except Exception:  # pragma: no cover
    _PROJECT_DATA_DIR = None
    _PROJECT_RESULTS_DIR = None

try:
    from code.common.constants import OSRM_PORTS as _PROJECT_OSRM_PORTS
except Exception:  # pragma: no cover
    _PROJECT_OSRM_PORTS = None


# --- Configuración de mapeos -------------------------------------------------

# vehicle_type -> perfil OSRM (elige el puerto).
VEHICLE_PROFILE = {
    "conventional_van": "driving",
    "electric_van": "driving",
    "cargo_bike": "cycling",
    "walking_courier": "walking",
    "customer_walking": "walking",
}

# Piernas cuyas paradas son customer_id (se resuelven con la demanda).
CUSTOMER_LEGS = {
    "direct_delivery",
    "cycling_last_mile",
    "walking_last_mile",
    "customer_collection",
}

# Piernas cuyas paradas son nombres de facility.
FACILITY_LEGS = {"facility_supply"}

# Piernas cuyo depósito es un centro logístico (el resto: facility).
CC_DEPOT_LEGS = {"direct_delivery", "facility_supply"}

STOP_SEPARATOR = " -> "


# --- Utilidades de carga -----------------------------------------------------

def parse_experiment_id(experiment_id: str) -> tuple[str, int]:
    """Extrae (scenario, instance_size) de un experiment_id."""
    match = re.search(r"_(low|medium|high)_(\d+)_", str(experiment_id))
    if not match:
        raise ValueError(
            f"No pude extraer escenario y tamaño del experiment_id "
            f"'{experiment_id}'. Pásalos con --scenario y --instance-size."
        )
    return match.group(1), int(match.group(2))


def load_demand_map(
    results_dir: Path, city: str, scenario: str, size: int
) -> dict[str, tuple[float, float]]:
    """customer_id (str) -> (lon, lat) desde el archivo de demanda."""
    path = results_dir / city / "demand" / f"demand_{scenario}_{size}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo de demanda: {path}")
    df = pd.read_csv(path)
    lon_col = "lon" if "lon" in df.columns else "Longitude"
    lat_col = "lat" if "lat" in df.columns else "Latitude"
    mapping: dict[str, tuple[float, float]] = {}
    for _, row in df.iterrows():
        cid = str(row["customer_id"]).strip()
        mapping[cid] = (float(row[lon_col]), float(row[lat_col]))
    return mapping


def _add_names_from_dataframe(
    df: pd.DataFrame, gazetteer: dict[str, tuple[float, float]]
) -> None:
    """Añade name->(lon,lat) usando cualquier columna de texto del DataFrame.

    No conocemos con certeza el nombre exacto de la columna de nombre en
    records_classified.csv, así que indexamos por todas las columnas de texto
    para que la búsqueda funcione sea cual sea.
    """
    lat_col = next((c for c in ("Latitude", "lat", "latitude") if c in df.columns), None)
    lon_col = next((c for c in ("Longitude", "lon", "longitude") if c in df.columns), None)
    if lat_col is None or lon_col is None:
        return
    text_cols = [c for c in df.columns if df[c].dtype == object]
    for _, row in df.iterrows():
        try:
            coord = (float(row[lon_col]), float(row[lat_col]))
        except (TypeError, ValueError):
            continue
        for col in text_cols:
            value = row[col]
            if isinstance(value, str):
                key = value.strip()
                if key and key not in gazetteer:
                    gazetteer[key] = coord


def load_facility_gazetteer(
    results_dir: Path,
    data_dir: Path,
    city: str,
    override_path: Path | None,
) -> dict[str, tuple[float, float]]:
    """Diccionario nombre -> (lon, lat) para CC, microhubs y PUDOs."""
    gazetteer: dict[str, tuple[float, float]] = {}

    centers_path = data_dir / city / "centros_cc.csv"
    if centers_path.exists():
        _add_names_from_dataframe(pd.read_csv(centers_path), gazetteer)
    else:
        print(f"[aviso] No encuentro centros_cc.csv en {centers_path}", file=sys.stderr)

    classified_path = (
        results_dir / city / "location_review" / "records_classified.csv"
    )
    if classified_path.exists():
        _add_names_from_dataframe(pd.read_csv(classified_path), gazetteer)
    else:
        print(
            f"[aviso] No encuentro records_classified.csv en {classified_path}",
            file=sys.stderr,
        )

    # Override manual (para facility_6 y demás nombres sintéticos).
    if override_path is not None:
        if not override_path.exists():
            raise FileNotFoundError(f"No existe el override: {override_path}")
        odf = pd.read_csv(override_path)
        name_col = next((c for c in ("name", "Name", "Location") if c in odf.columns), None)
        lat_col = next((c for c in ("lat", "Latitude") if c in odf.columns), None)
        lon_col = next((c for c in ("lon", "Longitude") if c in odf.columns), None)
        if not (name_col and lat_col and lon_col):
            raise ValueError(
                "El CSV de --facility-coords necesita columnas name, lat, lon."
            )
        for _, row in odf.iterrows():
            gazetteer[str(row[name_col]).strip()] = (
                float(row[lon_col]),
                float(row[lat_col]),
            )

    return gazetteer


# --- OSRM /route -------------------------------------------------------------

def resolve_osrm_ports(city: str, override: str | None) -> dict[str, int]:
    """Devuelve {perfil: puerto} para la ciudad."""
    if override:
        ports: dict[str, int] = {}
        for pair in override.split(","):
            profile, _, port = pair.partition("=")
            ports[profile.strip()] = int(port)
        return ports
    if _PROJECT_OSRM_PORTS and city in _PROJECT_OSRM_PORTS:
        return dict(_PROJECT_OSRM_PORTS[city])
    raise RuntimeError(
        "No pude determinar los puertos OSRM. Ejecuta desde la raíz del "
        "proyecto o pásalos con --osrm-ports 'driving=5000,cycling=5001,"
        "walking=5002'."
    )


def osrm_route_geometry(
    coords: list[tuple[float, float]], profile: str, port: int
) -> list[list[float]]:
    """Pide a OSRM la geometría por calles a través de coords en orden."""
    if requests is None:
        raise RuntimeError("El paquete 'requests' no está disponible.")
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
    url = f"http://localhost:{port}/route/v1/{profile}/{coord_str}"
    params = {"overview": "full", "geometries": "geojson", "continue_straight": "false"}
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(
            f"OSRM /route error: {payload.get('code')} - {payload.get('message', '')}"
        )
    return payload["routes"][0]["geometry"]["coordinates"]


# --- Construcción de geometría por ruta --------------------------------------

def resolve_stops(
    labels: list[str],
    leg: str,
    demand_map: dict[str, tuple[float, float]],
    gazetteer: dict[str, tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[str], list[str]]:
    """Devuelve (coords_resueltas, etiquetas_resueltas, etiquetas_no_resueltas)."""
    coords: list[tuple[float, float]] = []
    resolved: list[str] = []
    missing: list[str] = []
    use_demand = leg in CUSTOMER_LEGS
    for label in labels:
        key = label.strip()
        coord = None
        if use_demand:
            coord = demand_map.get(key)
            if coord is None:
                try:
                    coord = demand_map.get(str(int(key)))
                except ValueError:
                    coord = None
        else:
            coord = gazetteer.get(key)
        if coord is None:
            missing.append(key)
        else:
            coords.append(coord)
            resolved.append(key)
    return coords, resolved, missing


def resolve_depot(
    depot_name: str,
    leg: str,
    gazetteer: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """El depósito siempre es un nombre (CC o facility): se busca en el gazetteer."""
    return gazetteer.get(str(depot_name).strip())


def build_route_features(
    row: pd.Series,
    demand_map: dict[str, tuple[float, float]],
    gazetteer: dict[str, tuple[float, float]],
    osrm_ports: dict[str, int],
    geometry_mode: str,
    fallback_straight: bool,
) -> tuple[list[dict], list[dict], list[str]]:
    """Genera features de línea y de punto para una fila de ruta."""
    warnings: list[str] = []
    leg = str(row["leg"])
    vehicle_type = str(row["vehicle_type"])
    depot_name = str(row["depot"])
    route_id = str(row["route_id"])
    profile = VEHICLE_PROFILE.get(vehicle_type, "driving")

    labels = [
        part.strip()
        for part in str(row["stop_sequence"]).split(STOP_SEPARATOR)
        if part.strip()
    ]
    stop_coords, resolved_labels, missing = resolve_stops(
        labels, leg, demand_map, gazetteer
    )
    if missing:
        warnings.append(
            f"{route_id}: {len(missing)} parada(s) sin coordenadas: "
            f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}"
        )

    depot_coord = resolve_depot(depot_name, leg, gazetteer)
    if depot_coord is None:
        warnings.append(
            f"{route_id}: depósito '{depot_name}' sin coordenadas "
            f"(¿nombre sintético? usa --facility-coords)."
        )

    if not stop_coords:
        warnings.append(f"{route_id}: sin paradas resolubles, se omite la línea.")
        return [], [], warnings

    # Secuencia ordenada: depósito -> paradas -> depósito (ida y vuelta).
    ordered = []
    if depot_coord is not None:
        ordered.append(depot_coord)
    ordered.extend(stop_coords)
    if depot_coord is not None:
        ordered.append(depot_coord)

    # Geometría de la línea.
    geometry_source = geometry_mode
    if geometry_mode == "osrm":
        try:
            line_coords = osrm_route_geometry(ordered, profile, osrm_ports[profile])
        except Exception as exc:  # OSRM caído o error puntual
            if fallback_straight:
                warnings.append(f"{route_id}: OSRM falló ({exc}); línea recta.")
                line_coords = [[lon, lat] for lon, lat in ordered]
                geometry_source = "straight_fallback"
            else:
                warnings.append(f"{route_id}: OSRM falló ({exc}); línea omitida.")
                line_coords = None
    else:
        line_coords = [[lon, lat] for lon, lat in ordered]

    line_features: list[dict] = []
    if line_coords is not None:
        line_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": line_coords},
                "properties": {
                    "route_id": route_id,
                    "model": str(row.get("model", "")),
                    "leg": leg,
                    "vehicle_type": vehicle_type,
                    "profile": profile,
                    "depot": depot_name,
                    "geometry_source": geometry_source,
                    "stop_count": _safe(row, "stop_count"),
                    "distance_km": _safe(row, "distance_km"),
                    "duration_min": _safe(row, "duration_min"),
                    "package_load": _safe(row, "package_load"),
                    "routing_algorithm": str(row.get("routing_algorithm", "")),
                    "experiment_id": str(row.get("experiment_id", "")),
                },
            }
        )

    # Puntos: depósito + cada parada (con su orden de visita).
    point_features: list[dict] = []
    if depot_coord is not None:
        point_features.append(
            _point_feature(
                depot_coord, depot_name, "depot", 0, route_id, row, leg, vehicle_type
            )
        )
    for order, (coord, label) in enumerate(zip(stop_coords, resolved_labels), start=1):
        point_features.append(
            _point_feature(
                coord, label, "stop", order, route_id, row, leg, vehicle_type
            )
        )

    return line_features, point_features, warnings


def _point_feature(coord, label, kind, order, route_id, row, leg, vehicle_type) -> dict:
    lon, lat = coord
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "label": label,
            "kind": kind,
            "visit_order": order,
            "route_id": route_id,
            "model": str(row.get("model", "")),
            "leg": leg,
            "vehicle_type": vehicle_type,
        },
    }


def _safe(row: pd.Series, column: str):
    value = row.get(column)
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return value


# --- Programa principal ------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dibuja rutas del simulador GLIMS como GeoJSON para QGIS."
    )
    parser.add_argument("routes_file", type=Path, help="Ruta al *_routes.csv")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--route-id", action="append", default=None,
        help="route_id a dibujar (repetible).",
    )
    selection.add_argument("--all", action="store_true", help="Dibuja todas las rutas.")
    parser.add_argument("--list", action="store_true", help="Lista los route_id y sale.")
    parser.add_argument(
        "--geometry", choices=("osrm", "straight"), default="osrm",
        help="Geometría de las líneas (por defecto: osrm).",
    )
    parser.add_argument(
        "--no-fallback-straight", action="store_true",
        help="No caer a línea recta si OSRM falla.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--results-dir", type=Path, default=None)
    parser.add_argument("--scenario", choices=("low", "medium", "high"), default=None)
    parser.add_argument("--instance-size", type=int, default=None)
    parser.add_argument(
        "--facility-coords", type=Path, default=None,
        help="CSV name,lat,lon para resolver facilities sintéticas (facility_N).",
    )
    parser.add_argument(
        "--osrm-ports", default=None,
        help="Override de puertos: 'driving=5000,cycling=5001,walking=5002'.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    data_dir = args.data_dir or _PROJECT_DATA_DIR
    results_dir = args.results_dir or _PROJECT_RESULTS_DIR
    if data_dir is None or results_dir is None:
        raise SystemExit(
            "No pude determinar DATA_DIR / RESULTS_DIR. Ejecuta desde la raíz "
            "del proyecto o pasa --data-dir y --results-dir."
        )
    data_dir = Path(data_dir)
    results_dir = Path(results_dir)

    if not args.routes_file.exists():
        raise SystemExit(f"No existe el archivo de rutas: {args.routes_file}")
    routes_df = pd.read_csv(args.routes_file)
    if routes_df.empty:
        raise SystemExit("El archivo de rutas está vacío.")

    if args.list:
        print(f"Rutas disponibles en {args.routes_file.name}:")
        for _, row in routes_df.iterrows():
            print(
                f"  [{row.get('model', '?')}/{row.get('leg', '?')}] "
                f"{row['route_id']}"
            )
        return

    # Selección de filas.
    if args.route_id:
        selected = routes_df[routes_df["route_id"].isin(args.route_id)]
        if selected.empty:
            raise SystemExit(
                "Ningún route_id coincide. Usa --list para ver los disponibles."
            )
    elif args.all:
        selected = routes_df
    else:
        raise SystemExit(
            "Indica qué dibujar: --route-id <id> (repetible) o --all "
            "(o --list para ver las opciones)."
        )

    city = str(routes_df.iloc[0]["city"]).strip()
    if args.scenario and args.instance_size:
        scenario, size = args.scenario, args.instance_size
    else:
        experiment_id = str(routes_df.iloc[0]["experiment_id"])
        scenario, size = parse_experiment_id(experiment_id)

    print(f"Ciudad={city} | escenario={scenario} | tamaño={size}", file=sys.stderr)

    demand_map = load_demand_map(results_dir, city, scenario, size)
    gazetteer = load_facility_gazetteer(
        results_dir, data_dir, city, args.facility_coords
    )
    osrm_ports = (
        resolve_osrm_ports(city, args.osrm_ports)
        if args.geometry == "osrm"
        else {}
    )

    all_lines: list[dict] = []
    all_points: list[dict] = []
    all_warnings: list[str] = []
    for _, row in selected.iterrows():
        lines, points, warnings = build_route_features(
            row,
            demand_map,
            gazetteer,
            osrm_ports,
            args.geometry,
            not args.no_fallback_straight,
        )
        all_lines.extend(lines)
        all_points.extend(points)
        all_warnings.extend(warnings)

    # Salida.
    output_dir = args.output_dir or args.routes_file.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix or args.routes_file.stem
    lines_path = output_dir / f"{prefix}_lines.geojson"
    points_path = output_dir / f"{prefix}_points.geojson"

    _write_geojson(lines_path, all_lines)
    _write_geojson(points_path, all_points)

    print(
        f"\nHecho: {len(all_lines)} línea(s), {len(all_points)} punto(s).",
        file=sys.stderr,
    )
    print(f"  Líneas: {lines_path}", file=sys.stderr)
    print(f"  Puntos: {points_path}", file=sys.stderr)
    if all_warnings:
        print(f"\n{len(all_warnings)} aviso(s):", file=sys.stderr)
        for warning in all_warnings:
            print(f"  - {warning}", file=sys.stderr)


def _write_geojson(path: Path, features: list[dict]) -> None:
    collection = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    path.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()