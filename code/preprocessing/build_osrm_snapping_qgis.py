#!/usr/bin/env python3
"""
Build QGIS layers for auditing OSRM snapping.

This is a visualization/audit utility only. It does NOT modify routing,
unreachable-customer logic, travel times, CWS/ILS, or experiment results.

Input:
    audit/osrm_snapping_audit.csv

Output GeoPackage layers:
    snap_original_points  - original demand/customer coordinates
    snap_osrm_points      - coordinates snapped by OSRM
    snap_links            - straight audit line from original to snapped point

By default only M1 and M2 are exported, because they use the driving profile
and are the main focus of the current last-meter access inspection.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "model",
    "customer_id",
    "original_latitude",
    "original_longitude",
    "snapped_latitude",
    "snapped_longitude",
    "snap_distance_m",
}

DEFAULT_MODELS = ["M1", "M2"]


def _load_geospatial():
    try:
        import geopandas as gpd
        from shapely.geometry import LineString, Point
    except ImportError as exc:
        raise SystemExit(
            "This utility requires geopandas and shapely.\n"
            "Install the project requirements (or install geopandas/shapely) "
            "before running it."
        ) from exc
    return gpd, Point, LineString


def _validate_columns(df: pd.DataFrame, path: Path) -> None:
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise SystemExit(
            f"{path} is missing required columns: {', '.join(missing)}"
        )


def _prepare_rows(
    df: pd.DataFrame,
    models: list[str],
    min_snap_distance_m: float,
) -> pd.DataFrame:
    out = df.copy()

    out["model"] = out["model"].astype(str).str.upper()
    wanted = {m.upper() for m in models}
    out = out[out["model"].isin(wanted)].copy()

    numeric = [
        "original_latitude",
        "original_longitude",
        "snapped_latitude",
        "snapped_longitude",
        "snap_distance_m",
    ]
    for col in numeric:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    if "snap_available" in out.columns:
        available = out["snap_available"]
        if available.dtype == bool:
            out = out[available].copy()
        else:
            out = out[
                available.astype(str).str.strip().str.lower().isin(
                    {"true", "1", "yes", "y"}
                )
            ].copy()

    out = out.dropna(
        subset=[
            "original_latitude",
            "original_longitude",
            "snapped_latitude",
            "snapped_longitude",
            "snap_distance_m",
        ]
    ).copy()

    out = out[out["snap_distance_m"] >= float(min_snap_distance_m)].copy()

    # Useful continuous attribute plus neutral bins for QGIS inspection.
    # These bins are descriptive only; they are NOT routing thresholds.
    out["snap_band"] = pd.cut(
        out["snap_distance_m"],
        bins=[-float("inf"), 10, 25, 50, 100, float("inf")],
        labels=["0-10 m", "10-25 m", "25-50 m", "50-100 m", ">100 m"],
        right=True,
    ).astype(str)

    return out


def build_layers(df: pd.DataFrame):
    gpd, Point, LineString = _load_geospatial()

    attributes = [
        c for c in df.columns
        if c not in {"geometry"}
    ]

    original = gpd.GeoDataFrame(
        df[attributes].copy(),
        geometry=[
            Point(lon, lat)
            for lon, lat in zip(
                df["original_longitude"], df["original_latitude"]
            )
        ],
        crs="EPSG:4326",
    )

    snapped = gpd.GeoDataFrame(
        df[attributes].copy(),
        geometry=[
            Point(lon, lat)
            for lon, lat in zip(
                df["snapped_longitude"], df["snapped_latitude"]
            )
        ],
        crs="EPSG:4326",
    )

    links = gpd.GeoDataFrame(
        df[attributes].copy(),
        geometry=[
            LineString(
                [
                    (olon, olat),
                    (slon, slat),
                ]
            )
            for olon, olat, slon, slat in zip(
                df["original_longitude"],
                df["original_latitude"],
                df["snapped_longitude"],
                df["snapped_latitude"],
            )
        ],
        crs="EPSG:4326",
    )

    return original, snapped, links


def write_gpkg(
    original,
    snapped,
    links,
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    original.to_file(
        output,
        layer="snap_original_points",
        driver="GPKG",
        engine="pyogrio",
    )
    snapped.to_file(
        output,
        layer="snap_osrm_points",
        driver="GPKG",
        engine="pyogrio",
    )
    links.to_file(
        output,
        layer="snap_links",
        driver="GPKG",
        engine="pyogrio",
    )


def print_summary(df: pd.DataFrame) -> None:
    print()
    print("=" * 72)
    print("OSRM snapping audit — QGIS export")
    print("=" * 72)

    if df.empty:
        print("No rows matched the selected filters.")
        return

    summary = (
        df.groupby(["model", "osrm_profile"], dropna=False)["snap_distance_m"]
        .agg(["count", "median", "mean", "max"])
        .reset_index()
    )

    for row in summary.itertuples(index=False):
        print(
            f"{row.model:>3} | {str(row.osrm_profile):<10} | "
            f"n={int(row.count):4d} | "
            f"median={row.median:7.2f} m | "
            f"mean={row.mean:7.2f} m | "
            f"max={row.max:7.2f} m"
        )

    print()
    print("Largest snap distances:")
    cols = [
        c for c in
        ["model", "neighborhood", "customer_id", "snap_distance_m"]
        if c in df.columns
    ]
    print(
        df.nlargest(min(15, len(df)), "snap_distance_m")[cols]
        .to_string(index=False)
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Create QGIS layers showing original demand points, OSRM snapped "
            "points, and the straight-line snapping displacement."
        )
    )
    parser.add_argument(
        "audit_csv",
        type=Path,
        help="Path to audit/osrm_snapping_audit.csv.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Output GeoPackage. Default: "
            "<audit_csv_parent>/osrm_snapping_audit.gpkg"
        ),
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Models to export (default: M1 M2).",
    )
    parser.add_argument(
        "--min-snap-distance-m",
        type=float,
        default=0.0,
        help=(
            "Only export rows with snap_distance_m >= this value. "
            "Default: 0 (all selected rows)."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    audit_csv = args.audit_csv.resolve()

    if not audit_csv.exists():
        raise SystemExit(f"Input not found: {audit_csv}")

    output = (
        args.output.resolve()
        if args.output
        else audit_csv.parent / "osrm_snapping_audit.gpkg"
    )

    df = pd.read_csv(audit_csv)
    _validate_columns(df, audit_csv)
    df = _prepare_rows(df, args.models, args.min_snap_distance_m)

    if df.empty:
        raise SystemExit(
            "No snapping rows remain after filtering. "
            "Check --models and --min-snap-distance-m."
        )

    original, snapped, links = build_layers(df)
    write_gpkg(original, snapped, links, output)
    print_summary(df)

    print()
    print(f"Saved: {output}")
    print("Layers:")
    print("  - snap_original_points")
    print("  - snap_osrm_points")
    print("  - snap_links")
    print()
    print(
        "Interpret snap_links as an audit of the displacement between the "
        "original demand coordinate and the network position selected by OSRM. "
        "It is not yet modeled as walking time or routing cost."
    )


if __name__ == "__main__":
    main()
