from __future__ import annotations

import argparse
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point


CITY = "barcelona"


BASE_DIR = Path(__file__).resolve().parent
GLIMS_DIR = BASE_DIR.parent.parent
RAW_DATA_DIR = GLIMS_DIR / "raw_data"
DEFAULT_CONFIG = GLIMS_DIR / "configs" / "demand" / "demand_generation.json"

RANDOM_SEED = 42

DEMAND_SCENARIOS = {
    "low": (1, 2),
    "medium": (2, 5),
    "high": (5, 10),
}

CUSTOMER_COUNTS = [100, 200, 300, 400, 600, 800, 1000, 10000]

GEOGRAPHIC_CRS = "EPSG:4326"

rng = np.random.default_rng(RANDOM_SEED)


def _norm(s: str) -> str:
    """Lowercase, no accents, no extra whitespace; for comparing headers."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _sniff_sep(path: Path, encoding: str) -> str:
    """Detects the separator by looking at the first line (; \\t or ,)."""
    with open(path, encoding=encoding, errors="replace") as f:
        first = f.readline()
    counts = {sep: first.count(sep) for sep in [";", "\t", ","]}
    return max(counts, key=counts.get)


def _read_csv_smart(path: Path, **kwargs) -> pd.DataFrame:
    """Reads a CSV trying several encodings (INE data is usually in latin-1)."""
    sep = _sniff_sep(path, "latin-1")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str, **kwargs)
            df.columns = df.columns.str.strip()
            return df
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(path, sep=sep, encoding="latin-1", dtype=str, **kwargs)
    df.columns = df.columns.str.strip()
    return df


def find_col(df: pd.DataFrame, *keywords, required: bool = True):
    """Returns the first column whose (normalized) name contains ALL the keywords."""
    kws = [_norm(k) for k in keywords]
    for c in df.columns:
        cn = _norm(c)
        if all(k in cn for k in kws):
            return c
    if required:
        raise KeyError(
            f"No column matches {keywords}. Available columns: {list(df.columns)}"
        )
    return None


def load_population_ine(path: Path, muni_code: str) -> pd.DataFrame:
    """
    Loads population by census section from an INE file (format
    shared by Madrid and Valencia). Returns [census_section, section_population].
    """
    df = _read_csv_smart(path)

    sections_col = find_col(df, "secciones")
    total_col = find_col(df, "total")
    period_col = find_col(df, "periodo", required=False)
    sex_col = find_col(df, "sexo", required=False)
    age_col = find_col(df, "edad", required=False)

    if sex_col is not None:
        df = df[df[sex_col].map(_norm).isin({"total", "ambos sexos", "ambos"})]
    if age_col is not None:
        df = df[df[age_col].map(_norm).isin({"todas las edades", "total"})]

    code = df[sections_col].astype(str).str.strip().str.split().str[0]

    keep = code.str.startswith(muni_code)
    df = df[keep].copy()
    code = code[keep]

    if period_col is not None:
        year = pd.to_numeric(
            df[period_col].astype(str).str.extract(r"(\d{4})")[0], errors="coerce"
        )
        latest = year.max()
        df = df[year == latest].copy()
        code = code[year == latest]
        print(f"  Using population/INE period: {int(latest)}")

    if df.empty:
        raise ValueError(
            "The population filter (Sex/Age/municipality/period) left 0 rows. "
            "Check the exact values of those columns in your file."
        )

    code_num = pd.to_numeric(code, errors="coerce")
    census_section = (code_num % 100000).astype("Int64")  # district*1000 + section

    value = pd.to_numeric(
        df[total_col].astype(str)
        .str.replace(".", "", regex=False)   # thousands separator
        .str.replace(",", ".", regex=False)  # decimal separator (if any)
        .str.strip(),
        errors="coerce",
    )

    out = pd.DataFrame({"census_section": census_section, "section_population": value})
    out = out.dropna(subset=["census_section", "section_population"])
    out = out.groupby("census_section", as_index=False)["section_population"].sum()
    out["census_section"] = out["census_section"].astype(int)
    return out


# City-specific address loading

def load_addresses_barcelona(path: Path) -> pd.DataFrame:
    ADDR_X_COL = "x_etrs89"
    ADDR_Y_COL = "y_etrs89"
    ADDR_CP_COL = "dist_post"
    ADDR_DISTRICTE_COL = "districte"
    ADDR_BARRI_COL = "barri"
    ADDR_SECC_CENS_COL = "secc_cens"
    METRIC_CRS = "EPSG:25831"  # ETRS89 / UTM 31N

    df = pd.read_csv(path)
    df = df.dropna(subset=[ADDR_X_COL, ADDR_Y_COL, ADDR_DISTRICTE_COL, ADDR_SECC_CENS_COL]).copy()

    geometry = [Point(xy) for xy in zip(df[ADDR_X_COL], df[ADDR_Y_COL])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=METRIC_CRS)

    gdf["census_section"] = (
        gdf[ADDR_DISTRICTE_COL].astype(int) * 1000 + gdf[ADDR_SECC_CENS_COL].astype(int)
    )

    gdf_wgs84 = gdf.to_crs(GEOGRAPHIC_CRS)
    gdf["lon"] = gdf_wgs84.geometry.x.values
    gdf["lat"] = gdf_wgs84.geometry.y.values

    out = pd.DataFrame({
        "census_section": gdf["census_section"],
        "lon": gdf["lon"],
        "lat": gdf["lat"],
        "districte": gdf[ADDR_DISTRICTE_COL],
        "barri": gdf[ADDR_BARRI_COL],
        "postal_code": gdf[ADDR_CP_COL],
        "codi_carrer": gdf.get("codi_carrer"),
        "numpost": gdf.get("numpost"),
        "lletra": gdf.get("lletra"),
        "x_etrs89": gdf[ADDR_X_COL],
        "y_etrs89": gdf[ADDR_Y_COL],
    })
    return out


def load_population_barcelona(path: Path) -> pd.DataFrame:
    """Loads the municipal register and keeps only the most recent reference date."""
    POP_SEP = ";"
    POP_DATE_COL = "Data_Referencia"
    POP_SECCIO_COL = "Seccio_Censal"
    POP_VALOR_COL = "Valor"

    df = pd.read_csv(path, sep=POP_SEP)
    df[POP_DATE_COL] = pd.to_datetime(df[POP_DATE_COL], dayfirst=True, errors="coerce")
    latest_date = df[POP_DATE_COL].max()
    df_latest = df.loc[df[POP_DATE_COL] == latest_date].copy()
    print(f"  Using municipal register reference date: {latest_date.date()}")

    df_latest = df_latest.groupby(POP_SECCIO_COL, as_index=False)[POP_VALOR_COL].sum()
    df_latest = df_latest.rename(
        columns={POP_SECCIO_COL: "census_section", POP_VALOR_COL: "section_population"}
    )
    return df_latest[["census_section", "section_population"]]


def load_addresses_madrid(path: Path) -> pd.DataFrame:
    METRIC_CRS = "EPSG:25830"  # ETRS89 / UTM 30N (Madrid)

    df = _read_csv_smart(path)

    x_col = find_col(df, "coordenada x")
    y_col = find_col(df, "coordenada y")
    district_col = find_col(df, "codigo de distrito")
    section_col = find_col(df, "seccion censal")
    postal_col = find_col(df, "codigo postal", required=False)
    neighborhood_col = find_col(df, "nombre del barrio", required=False)
    district_name_col = find_col(df, "nombre del distrito", required=False)
    street_col = find_col(df, "codigo de via", required=False)
    number_literal_col = find_col(df, "literal de numeracion", required=False)
    full_address_col = find_col(df, "direccion completa", required=False)
    number_code_col = find_col(df, "codigo de numero", required=False)

    def _opt(col):
        return df[col].astype(str).str.strip() if col else np.nan

    work = pd.DataFrame({
        "x_m": pd.to_numeric(df[x_col], errors="coerce") / 100.0,   # cm -> m
        "y_m": pd.to_numeric(df[y_col], errors="coerce") / 100.0,   # cm -> m
        "district_code": pd.to_numeric(df[district_col], errors="coerce"),
        "local_section": pd.to_numeric(df[section_col], errors="coerce"),
        "postal_code": _opt(postal_col),
        "neighborhood_name": _opt(neighborhood_col),
        "district_name": _opt(district_name_col),
        "street_code": _opt(street_col),
        "street_number": _opt(number_literal_col),
        "full_address": _opt(full_address_col),
        "number_code": _opt(number_code_col),
    })

    work = work.dropna(subset=["x_m", "y_m", "district_code", "local_section"])
    work = work[(work["x_m"] > 0) & (work["y_m"] > 0)].copy()
    work["district_code"] = work["district_code"].astype(int)
    work["local_section"] = work["local_section"].astype(int)
    work["census_section"] = work["district_code"] * 1000 + work["local_section"]

    geometry = [Point(xy) for xy in zip(work["x_m"], work["y_m"])]
    gdf = gpd.GeoDataFrame(work, geometry=geometry, crs=METRIC_CRS)
    gdf_wgs84 = gdf.to_crs(GEOGRAPHIC_CRS)
    gdf["lon"] = gdf_wgs84.geometry.x.values
    gdf["lat"] = gdf_wgs84.geometry.y.values
    gdf["x_utm"] = gdf["x_m"]
    gdf["y_utm"] = gdf["y_m"]

    return pd.DataFrame(gdf.drop(columns=["geometry", "x_m", "y_m"]))


def load_addresses_valencia(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)

    sc = pd.to_numeric(df["secc_cens"], errors="coerce")
    district = sc // 100
    section = sc % 100
    census_section = district * 1000 + section

    work = pd.DataFrame({
        "census_section": census_section,
        "districte": pd.to_numeric(df["districte"], errors="coerce"),
        "barri": pd.to_numeric(df["barri"], errors="coerce"),
        "street_code": pd.to_numeric(df["codi_carrer"], errors="coerce").astype("Int64"),
        "street_number": df["numpost"].astype(str).str.strip(),
        "lon": pd.to_numeric(df["longitud_wgs84"], errors="coerce"),
        "lat": pd.to_numeric(df["latitud_wgs84"], errors="coerce"),
        "x_utm": pd.to_numeric(df["x_etrs89"], errors="coerce"),
        "y_utm": pd.to_numeric(df["y_etrs89"], errors="coerce"),
    })

    work = work.dropna(subset=["census_section", "lon", "lat"])
    work["census_section"] = work["census_section"].astype(int)
    return work


# Generic pipeline (common to all three cities)

def merge_population(addresses: pd.DataFrame, population: pd.DataFrame) -> pd.DataFrame:
    """Merges addresses with actual population by census_section."""
    merged = addresses.merge(population, on="census_section", how="left")

    n_sin_poblacion = merged["section_population"].isna().sum()
    if n_sin_poblacion > 0:
        print(
            f"  [warning] {n_sin_poblacion} addresses did not match a census section "
            "in the register and are discarded from the candidate customer pool"
        )
    merged = merged.dropna(subset=["section_population"]).copy()

    n_secciones_padron = population["census_section"].nunique()
    n_secciones_con_direcciones = merged["census_section"].nunique()
    n_secciones_sin_direcciones = n_secciones_padron - n_secciones_con_direcciones
    if n_secciones_sin_direcciones > 0:
        print(
            f"  [warning] {n_secciones_sin_direcciones} sections in the register have "
            "no candidate address (they contribute no customers)"
        )
    return merged


def compute_sampling_weights(addresses: pd.DataFrame) -> pd.DataFrame:
    """
    address_weight = section_population / number of candidate addresses in that section.
    The sum of weights within a section equals its actual population.
    """
    out = addresses.copy()
    n_por_seccion = out.groupby("census_section")["census_section"].transform("count")
    out["sampling_weight"] = out["section_population"] / n_por_seccion
    out = out[(out["sampling_weight"] > 0) & out["sampling_weight"].notna()].copy()
    return out


def sample_master_customers(addresses: pd.DataFrame, total: int, extra_cols: list) -> pd.DataFrame:
    if total > len(addresses):
        raise ValueError(
            f"Requested {total} customers but only {len(addresses)} "
            "addresses with a valid weight remain available."
        )

    addresses = addresses.reset_index(drop=True)
    weights = addresses["sampling_weight"].to_numpy()
    probs = weights / weights.sum()

    sampled_idx = rng.choice(addresses.index.to_numpy(), size=total, replace=False, p=probs)
    sample = addresses.loc[sampled_idx].copy()
    sample = sample.sample(frac=1, random_state=RANDOM_SEED).reset_index(drop=True)

    out = pd.DataFrame({
        "customer_id": range(len(sample)),
        "lon": sample["lon"].values,
        "lat": sample["lat"].values,
    })
    for col in extra_cols:
        if col in ("customer_id", "lon", "lat"):
            continue
        out[col] = sample[col].values if col in sample.columns else np.nan
    return out


def assign_demand(customers_df: pd.DataFrame, scenario: str) -> pd.DataFrame:
    low, high = DEMAND_SCENARIOS[scenario]
    df = customers_df.copy()
    df["demand"] = rng.integers(low, high + 1, size=len(df))
    df["scenario"] = scenario
    return df



CITY_CONFIGS = {
    "barcelona": {
        "addresses_path": RAW_DATA_DIR / "adreces_postals_elementals.csv",
        "population_path": RAW_DATA_DIR / "2026_pad_mdbas.csv",
        "results_dir": GLIMS_DIR / "results" / "barcelona" / "demand",
        "load_addresses": load_addresses_barcelona,
        "load_population": lambda path: load_population_barcelona(path),
        "rename_out": {"census_section": "seccio_censal", "section_population": "poblacion_seccion"},
        "master_cols": [
            "customer_id", "codi_carrer", "numpost", "lletra", "districte", "barri",
            "seccio_censal", "poblacion_seccion", "postal_code", "lon", "lat",
            "x_etrs89", "y_etrs89", "demand_seed",
        ],
        "instance_cols": [
            "customer_id", "lon", "lat", "demand", "scenario", "instance_size",
            "demand_seed", "demand_instance_id",
            "districte", "barri", "seccio_censal", "poblacion_seccion", "postal_code",
            "codi_carrer", "numpost", "lletra", "x_etrs89", "y_etrs89",
        ],
    },
    "madrid": {
        "addresses_path": RAW_DATA_DIR / "madrid_direcciones_postales.csv",
        "population_path": RAW_DATA_DIR / "madrid_poblacion.csv",
        "results_dir": GLIMS_DIR / "results" / "madrid" / "demand",
        "load_addresses": load_addresses_madrid,
        "load_population": lambda path: load_population_ine(path, "28079"),
        "rename_out": {},
        "master_cols": [
            "customer_id", "lon", "lat", "district_code", "district_name",
            "neighborhood_name", "census_section", "section_population", "postal_code",
            "full_address", "street_code", "street_number", "number_code",
            "x_utm", "y_utm", "demand_seed",
        ],
        "instance_cols": [
            "customer_id", "lon", "lat", "demand", "scenario", "instance_size",
            "demand_seed", "demand_instance_id",
            "district_code", "district_name", "neighborhood_name", "census_section",
            "section_population", "postal_code", "full_address", "street_code",
            "street_number", "number_code", "x_utm", "y_utm",
        ],
    },
    "valencia": {
        "addresses_path": GLIMS_DIR / "data" / "valencia" / "direcciones_valencia.csv",
        "population_path": GLIMS_DIR / "raw_data" / "Valencia" / "valencia_poblacion.csv",
        "results_dir": GLIMS_DIR / "results" / "valencia" / "demand",
        "load_addresses": load_addresses_valencia,
        "load_population": lambda path: load_population_ine(path, "46250"),
        "rename_out": {},
        "master_cols": [
            "customer_id", "lon", "lat", "districte", "barri", "census_section",
            "section_population", "street_code", "street_number", "x_utm", "y_utm",
            "demand_seed",
        ],
        "instance_cols": [
            "customer_id", "lon", "lat", "demand", "scenario", "instance_size",
            "demand_seed", "demand_instance_id",
            "districte", "barri", "census_section", "section_population",
            "street_code", "street_number", "x_utm", "y_utm",
        ],
    },
}

SUPPORTED_CITIES = tuple(CITY_CONFIGS.keys())
SUPPORTED_SCENARIOS = tuple(DEMAND_SCENARIOS.keys())

# JSON config: multiple cities/scenarios/sizes/seeds in a single run

@dataclass(frozen=True)
class DemandGenerationConfig:
    cities: tuple[str, ...]
    scenarios: tuple[str, ...]
    sizes: tuple[int, ...]
    seeds: tuple[int, ...]
    overwrite: bool = False


def _as_tuple(value, *, name: str) -> tuple:
    if isinstance(value, (str, int)):
        return (value,)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty value or a non-empty list.")
    return tuple(value)


def load_generation_config(path: Path) -> DemandGenerationConfig:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path.resolve()}")

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    cities = tuple(str(x).lower() for x in _as_tuple(raw.get("cities", []), name="cities"))
    scenarios = tuple(
        str(x).lower() for x in _as_tuple(raw.get("scenarios", []), name="scenarios")
    )
    sizes = tuple(int(x) for x in _as_tuple(raw.get("sizes", []), name="sizes"))
    seeds = tuple(int(x) for x in _as_tuple(raw.get("seeds", []), name="seeds"))
    overwrite = bool(raw.get("overwrite", False))

    unknown_cities = sorted(set(cities).difference(SUPPORTED_CITIES))
    if unknown_cities:
        raise ValueError(f"Unsupported cities: {unknown_cities}. Options: {SUPPORTED_CITIES}.")

    unknown_scenarios = sorted(set(scenarios).difference(SUPPORTED_SCENARIOS))
    if unknown_scenarios:
        raise ValueError(f"Unsupported scenarios: {unknown_scenarios}. Options: {SUPPORTED_SCENARIOS}.")

    if any(size <= 0 for size in sizes):
        raise ValueError("All sizes must be greater than zero.")
    if len(set(sizes)) != len(sizes):
        raise ValueError("sizes cannot contain duplicate values.")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds cannot contain duplicate values.")

    return DemandGenerationConfig(
        cities=cities, scenarios=scenarios, sizes=sizes, seeds=seeds, overwrite=overwrite,
    )


def _default_single_city_config(city: str) -> DemandGenerationConfig:
    """Implicit config when running with --city (or the CITY variable) without --config."""
    return DemandGenerationConfig(
        cities=(city,),
        scenarios=SUPPORTED_SCENARIOS,
        sizes=tuple(CUSTOMER_COUNTS),
        seeds=(RANDOM_SEED,),
        overwrite=True,
    )


def _demand_rng_seed(base_seed: int, scenario: str, size: int) -> int:
    scenario_code = {"low": 11, "medium": 23, "high": 37}[scenario]
    return int((base_seed * 1_000_003 + scenario_code * 10_007 + size) % (2**32))


def _write_csv(frame: pd.DataFrame, path: Path, *, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        print(f"  SKIPPED (already exists): {path.name}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return True


def generate_for_city(
    city: str,
    *,
    scenarios: Iterable[str],
    sizes: Iterable[int],
    seeds: Iterable[int],
    overwrite: bool,
) -> None:
    global rng

    if city not in CITY_CONFIGS:
        raise ValueError(f"City '{city}' not recognized. Options: {SUPPORTED_CITIES}")
    cfg = CITY_CONFIGS[city]

    print(f"\n[{city}] Loading actual postal addresses...")
    addresses = cfg["load_addresses"](cfg["addresses_path"])
    print(f"[{city}] Candidate addresses loaded: {len(addresses):,}")

    print(f"[{city}] Loading population by census section...")
    population = cfg["load_population"](cfg["population_path"])

    print(f"[{city}] Merging addresses with actual population...")
    addresses = merge_population(addresses, population)

    print(f"[{city}] Computing sampling weights...")
    addresses = compute_sampling_weights(addresses)
    print(f"[{city}] Addresses with a valid sampling weight: {len(addresses):,}")

    if cfg["rename_out"]:
        addresses = addresses.rename(columns=cfg["rename_out"])

    sizes = tuple(sorted(int(s) for s in sizes))
    scenarios = tuple(scenarios)
    seeds = tuple(seeds)
    max_size = max(sizes)

    if max_size > len(addresses):
        raise ValueError(
            f"[{city}] Requested {max_size:,} customers, but there are only "
            f"{len(addresses):,} candidate addresses with a valid population weight."
        )

    output_dir = cfg["results_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    master_extra_cols = [c for c in cfg["master_cols"] if c not in ("customer_id", "lon", "lat", "demand_seed")]

    for seed in seeds:
        print(f"\n[{city}] Generating spatial master sample with seed={seed}...")

        rng = np.random.default_rng(seed)

        master = sample_master_customers(addresses, max_size, master_extra_cols)
        master["demand_seed"] = int(seed)
        master = master[cfg["master_cols"]]

        master_path = output_dir / f"master_customers_{max_size}_seed_{seed}.csv"
        if _write_csv(master, master_path, overwrite=overwrite):
            print(f"  Master saved: {master_path.name} ({len(master):,} customers)")

        for size in sizes:
            subset = master.iloc[:size].copy()

            for scenario in scenarios:
                rng = np.random.default_rng(_demand_rng_seed(seed, scenario, size))

                instance = assign_demand(subset, scenario)
                instance["instance_size"] = int(size)
                instance["demand_seed"] = int(seed)
                instance_id = f"demand_{scenario}_{size}_seed_{seed}"
                instance["demand_instance_id"] = instance_id
                instance = instance[cfg["instance_cols"]]

                instance_path = output_dir / f"{instance_id}.csv"
                if _write_csv(instance, instance_path, overwrite=overwrite):
                    total_demand = int(instance["demand"].sum())
                    print(
                        f"  Saved {instance_path.name}: {len(instance):,} customers | "
                        f"total demand = {total_demand:,}"
                    )




def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generates GLIMS demand instances for Barcelona, Madrid and/or Valencia."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to a configuration JSON with cities/scenarios/sizes/seeds/overwrite "
            "to process one or more cities (and seeds) in a single run. "
            f"If not given and {DEFAULT_CONFIG} exists, that one is used by default."
        ),
    )
    parser.add_argument(
        "--city",
        choices=sorted(SUPPORTED_CITIES),
        default=None,
        help=(
            f"City to process when --config is not used (default: CITY variable = '{CITY}'). "
            "Ignored if --config is passed."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.config is not None:
        config = load_generation_config(args.config)
    elif args.city is None and DEFAULT_CONFIG.exists():
        config = load_generation_config(DEFAULT_CONFIG)
    else:
        config = _default_single_city_config((args.city or CITY).lower().strip())

    print("=" * 68)
    print("GLIMS DEMAND INSTANCE GENERATOR")
    print("=" * 68)
    print(f"Cities: {', '.join(config.cities)}")
    print(f"Scenarios: {', '.join(config.scenarios)}")
    print(f"Sizes: {', '.join(f'{x:,}' for x in config.sizes)}")
    print(f"Seeds: {', '.join(str(x) for x in config.seeds)}")
    print(f"Overwrite existing files: {config.overwrite}")
    print("=" * 68)

    for city in config.cities:
        generate_for_city(
            city,
            scenarios=config.scenarios,
            sizes=config.sizes,
            seeds=config.seeds,
            overwrite=config.overwrite,
        )

    print("\nDemand generation completed.")


if __name__ == "__main__":
    main()