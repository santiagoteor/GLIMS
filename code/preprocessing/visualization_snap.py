import argparse
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BAND_LABELS = ["< 25 m", "25-50 m", "50-75 m", "75-100 m", "> 100 m"]
BAND_COLORS = {
    "< 25 m": "#FF0000",
    "25-50 m": "#eaff00",
    "50-75 m": "#00ff00",
    "75-100 m": "#0400ff",
    "> 100 m": "#000000",
}

COLOR_SURFACE = "#fcfcfb"
COLOR_PRIMARY_INK = "#0b0b0b"
COLOR_SECONDARY_INK = "#52514e"
COLOR_MUTED = "#898781"
COLOR_GRIDLINE = "#e1e0d9"
COLOR_BASELINE = "#c3c2b7"


def classify_band(values: pd.Series, bins: list[float]) -> pd.Series:
    """Classifies `values` into fixed bands using the thresholds in `bins`
    (e.g. [25, 50, 75, 100] -> "< 25 m", "25-50 m", ..., "> 100 m")."""
    edges = [-np.inf, *bins, np.inf]
    labels = (
        [f"< {bins[0]:g} m"]
        + [f"{lo:g}-{hi:g} m" for lo, hi in zip(bins[:-1], bins[1:])]
        + [f"> {bins[-1]:g} m"]
    )
    band = pd.cut(values, bins=edges, labels=labels, right=False)
    return band.astype(str), labels


def load_layers(path: Path, requested_layers: list[str] | None) -> dict[str, gpd.GeoDataFrame]:
    available = gpd.list_layers(path)["name"].tolist()
    if not available:
        sys.exit(f"No layers found in '{path}'.")

    if requested_layers:
        missing = [l for l in requested_layers if l not in available]
        if missing:
            sys.exit(f"Layer(s) not found: {', '.join(missing)}. "
                      f"Available layers: {', '.join(available)}")
        layer_names = requested_layers
    else:
        layer_names = available

    return {name: gpd.read_file(path, layer=name) for name in layer_names}


def add_band_columns(gdf: gpd.GeoDataFrame, column: str, bins: list[float]) -> tuple[gpd.GeoDataFrame, list[str]]:
    if column not in gdf.columns:
        return gdf, []
    values = pd.to_numeric(gdf[column], errors="coerce")
    band, labels = classify_band(values, bins)
    gdf = gdf.copy()
    gdf["snap_band"] = band
    gdf["snap_band_color"] = band.map(dict(zip(labels, [BAND_COLORS[l] if l in BAND_COLORS else "#898781"
                                                          for l in labels])))
    return gdf, labels


def write_geopackage(layers: dict[str, gpd.GeoDataFrame], output_path: Path):
    if output_path.exists():
        output_path.unlink()
    for name, gdf in layers.items():
        gdf.to_file(output_path, layer=name, driver="GPKG")


def pick_map_layer(layers: dict[str, gpd.GeoDataFrame]) -> tuple[str, gpd.GeoDataFrame] | None:
    """Prefers a point layer for the spatial panel; falls back to whatever is available."""
    for name, gdf in layers.items():
        if gdf.geom_type.isin(["Point", "MultiPoint"]).all():
            return name, gdf
    for name, gdf in layers.items():
        if not gdf.empty:
            return name, gdf
    return None


def plot_bands(layers: dict[str, gpd.GeoDataFrame], column: str, labels: list[str],
                output: str | None, show: bool):
    all_values = pd.concat(
        [pd.to_numeric(gdf[column], errors="coerce") for gdf in layers.values() if column in gdf.columns],
        ignore_index=True,
    ).dropna()

    counts = {}
    for name, gdf in layers.items():
        if "snap_band" not in gdf.columns:
            continue
        vc = gdf["snap_band"].value_counts()
        for label in labels:
            counts[label] = counts.get(label, 0) + int(vc.get(label, 0))
        break  

    n_total = sum(counts.values())

    print("=" * 60)
    print(f"Column analyzed : {column}")
    print(f"N rows          : {n_total}")
    print(f"Mean            : {all_values.mean():.3f}")
    print(f"Median          : {all_values.median():.3f}")
    print("Distance bands  :")
    for label in labels:
        n = counts.get(label, 0)
        pct = 100 * n / n_total if n_total else 0
        print(f"  {label:<10} : {n:>5} ({pct:5.1f}%)")
    print("=" * 60)

    map_choice = pick_map_layer(layers)

    fig, (ax_hist, ax_map) = plt.subplots(
        nrows=1, ncols=2, figsize=(13, 6),
        gridspec_kw={"width_ratios": [1, 1.3]},
    )
    fig.patch.set_facecolor(COLOR_SURFACE)

    ax_hist.set_facecolor(COLOR_SURFACE)
    bin_edges = np.histogram_bin_edges(all_values, bins=min(60, max(15, n_total // 15)))
    reference_gdf = next(gdf for gdf in layers.values() if "snap_band" in gdf.columns)
    for label in labels:
        band_values = pd.to_numeric(
            reference_gdf.loc[reference_gdf["snap_band"] == label, column], errors="coerce"
        )
        ax_hist.hist(band_values, bins=bin_edges, color=BAND_COLORS.get(label, "#898781"),
                     edgecolor=COLOR_SURFACE, linewidth=0.4, zorder=2, label=label)

    ax_hist.set_title(f"Distribution of {column} by band", color=COLOR_PRIMARY_INK,
                       fontsize=13, loc="left", pad=12)
    ax_hist.set_xlabel(f"{column} (meters)", color=COLOR_SECONDARY_INK)
    ax_hist.set_ylabel("Number of records", color=COLOR_SECONDARY_INK)
    ax_hist.tick_params(colors=COLOR_MUTED)
    ax_hist.grid(axis="y", color=COLOR_GRIDLINE, linewidth=0.8, zorder=0)
    for spine in ("top", "right", "left"):
        ax_hist.spines[spine].set_visible(False)
    ax_hist.spines["bottom"].set_color(COLOR_BASELINE)
    ax_hist.legend(frameon=False, labelcolor=COLOR_SECONDARY_INK, loc="upper right", fontsize=9)

    ax_map.set_facecolor(COLOR_SURFACE)
    if map_choice is not None:
        map_name, map_gdf = map_choice
        for label in labels:
            subset = map_gdf[map_gdf["snap_band"] == label]
            if subset.empty:
                continue
            subset.plot(ax=ax_map, color=BAND_COLORS.get(label, "#898781"),
                        markersize=16, alpha=0.85, linewidth=1.2, label=label, zorder=2)
        ax_map.set_title(f"Map of '{map_name}' by band", color=COLOR_PRIMARY_INK,
                          fontsize=13, loc="left", pad=12)
        ax_map.legend(frameon=False, labelcolor=COLOR_SECONDARY_INK, loc="best", fontsize=9)
    else:
        ax_map.text(0.5, 0.5, "No layer available to map", ha="center", va="center",
                     color=COLOR_MUTED)
    ax_map.tick_params(colors=COLOR_MUTED, labelsize=8)
    ax_map.set_xlabel("Longitude", color=COLOR_SECONDARY_INK)
    ax_map.set_ylabel("Latitude", color=COLOR_SECONDARY_INK)
    for spine in ("top", "right"):
        ax_map.spines[spine].set_visible(False)
    ax_map.spines["bottom"].set_color(COLOR_BASELINE)
    ax_map.spines["left"].set_color(COLOR_BASELINE)
    ax_map.set_aspect("equal", adjustable="datalim")

    fig.tight_layout()

    if output:
        fig.savefig(output, dpi=150, facecolor=fig.get_facecolor())
        print(f"Chart saved to: {output}")

    if show:
        plt.show()

    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gpkg", help="Path to the input GeoPackage file.")
    parser.add_argument("--column", default="snap_distance_m",
                         help="Numeric column to classify into bands (default: snap_distance_m).")
    parser.add_argument("--bins", default="25,50,75,100",
                         help="Comma-separated band thresholds in meters (default: 25,50,75,100 -> "
                              "'< 25 m', '25-50 m', '50-75 m', '75-100 m', '> 100 m').")
    parser.add_argument("--layers", default=None,
                         help="Comma-separated list of layers to process (default: all layers "
                              "in the GeoPackage).")
    parser.add_argument("--output-gpkg", default=None,
                         help="Path to save the updated GeoPackage (default: "
                              "'<input>_by_band.gpkg').")
    parser.add_argument("--output-png", default=None,
                         help="Path to save the chart as an image (default: "
                              "'<input>_by_band.png').")
    parser.add_argument("--no-show", dest="show", action="store_false",
                         help="Don't open the interactive chart window.")
    args = parser.parse_args()

    gpkg_path = Path(args.gpkg)
    if not gpkg_path.exists():
        sys.exit(f"File not found: {gpkg_path}")

    bins = [float(b) for b in args.bins.split(",")]
    requested_layers = args.layers.split(",") if args.layers else None

    layers = load_layers(gpkg_path, requested_layers)

    labels = None
    for name in list(layers.keys()):
        layers[name], layer_labels = add_band_columns(layers[name], args.column, bins)
        if layer_labels:
            labels = layer_labels
    if labels is None:
        sys.exit(f"Column '{args.column}' was not found in any of the processed layers.")

    output_gpkg = Path(args.output_gpkg) if args.output_gpkg else gpkg_path.with_name(
        f"{gpkg_path.stem}_by_band.gpkg")
    write_geopackage(layers, output_gpkg)
    print(f"Updated GeoPackage saved to: {output_gpkg}")

    output_png = args.output_png or str(gpkg_path.with_name(f"{gpkg_path.stem}_by_band.png"))
    plot_bands(layers, args.column, labels, output=output_png, show=args.show)


if __name__ == "__main__":
    main()