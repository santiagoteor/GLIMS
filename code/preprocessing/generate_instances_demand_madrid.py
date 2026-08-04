import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point


BASE_DIR = Path(__file__).resolve().parent         
GLIMS_DIR = BASE_DIR.parent.parent                 
RAW_DATA_DIR = GLIMS_DIR / "raw_data"
RESULTS_DIR = GLIMS_DIR / "results" / "madrid" / "demand"

ADDRESSES_PATH = RAW_DATA_DIR / "madrid_direcciones_postales.csv"
POPULATION_PATH = RAW_DATA_DIR / "madrid_poblacion.csv"

MADRID_MUNI_CODE = "28079"

RANDOM_SEED = 42

DEMAND_SCENARIOS = {
    "low": (1, 2),
    "medium": (2, 5),
    "high": (5, 10),
}

CUSTOMER_COUNTS = [100, 200, 300, 400, 600, 800, 1000, 10000]

METRIC_CRS = "EPSG:25830"   # ETRS89 / UTM 30N (Madrid)
GEOGRAPHIC_CRS = "EPSG:4326"

rng = np.random.default_rng(RANDOM_SEED)


def _norm(s: str) -> str:
    """Lowercase, accent-free and trimmed, used to compare column names."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _sniff_sep(path: Path, encoding: str) -> str:
    """Detect the separator by inspecting the first line (; \\t or ,)."""
    with open(path, encoding=encoding, errors="replace") as f:
        first = f.readline()
    counts = {sep: first.count(sep) for sep in [";", "\t", ","]}
    return max(counts, key=counts.get)


def _read_csv_smart(path: Path, **kwargs) -> pd.DataFrame:
    """Read a CSV trying encodings (Madrid data is usually latin-1)."""
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
    """Return the first column whose (normalized) name contains ALL keywords.

    Keywords are kept in Spanish on purpose: they must match the real
    headers in the source CSV files.
    """
    kws = [_norm(k) for k in keywords]
    for c in df.columns:
        cn = _norm(c)
        if all(k in cn for k in kws):
            return c
    if required:
        raise KeyError(
            f"No column matching {keywords}. Available columns: {list(df.columns)}"
        )
    return None


def load_addresses(path: Path) -> gpd.GeoDataFrame:
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

    work = pd.DataFrame(
        {
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
        }
    )

    work = work.dropna(subset=["x_m", "y_m", "district_code", "local_section"])
    work = work[(work["x_m"] > 0) & (work["y_m"] > 0)].copy()
    work["district_code"] = work["district_code"].astype(int)
    work["local_section"] = work["local_section"].astype(int)

    geometry = [Point(xy) for xy in zip(work["x_m"], work["y_m"])]
    gdf = gpd.GeoDataFrame(work, geometry=geometry, crs=METRIC_CRS)
    return gdf


def load_population(path: Path) -> pd.DataFrame:
    """
    Load population per census section from the INE file.
    Returns a DataFrame with [census_section, section_population].
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

    df = df[code.str.startswith(MADRID_MUNI_CODE)].copy()
    code = code[code.str.startswith(MADRID_MUNI_CODE)]

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
    census_section = (code_num % 100000).astype("Int64")

    value = pd.to_numeric(
        df[total_col].astype(str)
        .str.replace(".", "", regex=False)   
        .str.replace(",", ".", regex=False) 
        .str.strip(),
        errors="coerce",
    )

    out = pd.DataFrame({"census_section": census_section, "section_population": value})
    out = out.dropna(subset=["census_section", "section_population"])
    out = out.groupby("census_section", as_index=False)["section_population"].sum()
    out["census_section"] = out["census_section"].astype(int)
    return out


def add_census_section_key(addresses: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rebuild the composite code census_section = district*1000 + local_section."""
    out = addresses.copy()
    out["census_section"] = out["district_code"] * 1000 + out["local_section"]
    return out


def merge_population(addresses: gpd.GeoDataFrame, population: pd.DataFrame) -> gpd.GeoDataFrame:
    """Merge addresses with real population by census_section."""
    merged = addresses.merge(population, on="census_section", how="left")

    n_no_population = merged["section_population"].isna().sum()
    if n_no_population > 0:
        print(
            f"  [warning] {n_no_population} addresses did not match a census section "
            "in the population data and are dropped from the candidate pool"
        )
    merged = merged.dropna(subset=["section_population"]).copy()

    n_sections_pop = population["census_section"].nunique()
    n_sections_with_addresses = merged["census_section"].nunique()
    n_sections_without_addresses = n_sections_pop - n_sections_with_addresses
    if n_sections_without_addresses > 0:
        print(
            f"  [warning] {n_sections_without_addresses} sections with population have no "
            "candidate address (they contribute no customers)"
        )
    return merged


def compute_sampling_weights(addresses: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    address_weight = section_population / number of candidate addresses in that
    section. The sum of weights within a section equals its real population.
    """
    out = addresses.copy()
    n_per_section = out.groupby("census_section")["census_section"].transform("count")
    out["sampling_weight"] = out["section_population"] / n_per_section
    out = out[(out["sampling_weight"] > 0) & out["sampling_weight"].notna()].copy()
    return out


def sample_master_customers(addresses: gpd.GeoDataFrame, total: int) -> pd.DataFrame:
    if total > len(addresses):
        raise ValueError(
            f"Requested {total} customers but only {len(addresses)} addresses "
            "with a valid weight remain."
        )

    weights = addresses["sampling_weight"].to_numpy()
    probs = weights / weights.sum()

    sampled_idx = rng.choice(addresses.index.to_numpy(), size=total, replace=False, p=probs)
    sample = addresses.loc[sampled_idx].copy()
    sample = sample.sample(frac=1, random_state=RANDOM_SEED).reset_index(drop=True)

    sample_wgs84 = sample.to_crs(GEOGRAPHIC_CRS)

    out = pd.DataFrame(
        {
            "customer_id": range(len(sample)),
            "lon": sample_wgs84.geometry.x.values,
            "lat": sample_wgs84.geometry.y.values,
            "district_code": sample["district_code"].values,
            "district_name": sample["district_name"].values,
            "neighborhood_name": sample["neighborhood_name"].values,
            "census_section": sample["census_section"].values,
            "section_population": sample["section_population"].values,
            "postal_code": sample["postal_code"].values,
            "full_address": sample["full_address"].values,
            "street_code": sample["street_code"].values,
            "street_number": sample["street_number"].values,
            "number_code": sample["number_code"].values,
            "x_utm": sample.geometry.x.values,   # ETRS89 / UTM 30N (m)
            "y_utm": sample.geometry.y.values,
        }
    )
    return out



def assign_demand(customers_df: pd.DataFrame, scenario: str) -> pd.DataFrame:
    low, high = DEMAND_SCENARIOS[scenario]
    df = customers_df.copy()
    df["demand"] = rng.integers(low, high + 1, size=len(df))
    df["scenario"] = scenario
    return df


def build_instances(master_df: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    for n in CUSTOMER_COUNTS:
        if n > len(master_df):
            print(f"[warning] requested {n} customers but the master pool only has {len(master_df)}")
            continue

        subset = master_df.iloc[:n].copy()  

        for scenario in DEMAND_SCENARIOS:
            instance = assign_demand(subset, scenario)
            instance["instance_size"] = n

            cols = [
                "customer_id",
                "lon",
                "lat",
                "demand",
                "scenario",
                "instance_size",
                "district_code",
                "district_name",
                "neighborhood_name",
                "census_section",
                "section_population",
                "postal_code",
                "full_address",
                "street_code",
                "street_number",
                "number_code",
                "x_utm",
                "y_utm",
            ]
            instance = instance[cols]

            fname = output_dir / f"demand_{scenario}_{n}.csv"
            instance.to_csv(fname, index=False)
            total_demand = instance["demand"].sum()
            print(f"Saved: {fname} | {n} customers | total demand = {total_demand}")


def main() -> None:
    print("Loading real Madrid postal addresses...")
    addresses = load_addresses(ADDRESSES_PATH)
    print(f"  {len(addresses)} addresses loaded")

    print("Rebuilding the composite census section code (district*1000+section)...")
    addresses = add_census_section_key(addresses)

    print("Loading population per census section (INE)...")
    population = load_population(POPULATION_PATH)
    print(f"  {len(population)} census sections with population")

    print("Merging addresses with real population...")
    addresses = merge_population(addresses, population)
    print(f"  {len(addresses)} addresses with an assigned section population")

    print("Computing sampling weights (population split across each section's addresses)...")
    addresses = compute_sampling_weights(addresses)
    print(f"  {len(addresses)} addresses with a valid sampling weight")

    n_max = max(CUSTOMER_COUNTS)
    print(f"Sampling master pool of {n_max} customers (weighted by real population)...")
    master = sample_master_customers(addresses, n_max)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    master_path = RESULTS_DIR / f"master_customers_{n_max}.csv"
    master.to_csv(master_path, index=False)
    print(f"Master pool saved to: {master_path}")

    print("Generating instances by scenario and size...")
    build_instances(master, RESULTS_DIR)

    print("Done.")

if __name__ == "__main__":
    main()