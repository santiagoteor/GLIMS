from pathlib import Path

import pandas as pd

from code.common.data_utils import validate_required_columns
from code.common.paths import DATA_DIR, RESULTS_DIR
import geopandas as gpd

def load_city_data(
    city: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load logistics centers, neighborhood limits, and model parameters."""

    city_folder = DATA_DIR / city

    centers_path = city_folder / "centros_cc.csv"
    boundaries_path = city_folder / "limites_zonas.geojson"
    parameters_path = DATA_DIR / "model_parameters.csv"

    required_paths = {
        "logistics centers": centers_path,
        "neighborhood boundaries": boundaries_path,
        "model parameters": parameters_path,
    }

    missing_paths = [
        f"{description}: {path.resolve()}"
        for description, path in required_paths.items()
        if not path.exists()
    ]

    if missing_paths:
        raise FileNotFoundError(
            "Required simulation files were not found:\n- "
            + "\n- ".join(missing_paths)
        )

    centers = pd.read_csv(centers_path)
    boundaries = gpd.read_file(boundaries_path)
    if boundaries.crs is not None and boundaries.crs.to_epsg() != 4326:
        boundaries = boundaries.to_crs(epsg=4326)   
    parameters = pd.read_csv(parameters_path)

    validate_required_columns(
        centers,
        required_columns={"Location", "Latitude", "Longitude"},
        dataset_name=f"{city} logistics centers",
    )
    validate_required_columns(
        boundaries,
        required_columns={"zona", "tipo", "geometry"},
        dataset_name=f"{city} zones",
    )
    validate_required_columns(
        parameters,
        required_columns={"modelo", "nox_km_estimado_cliente"},
        dataset_name="model parameters",
    )

    return centers, boundaries, parameters


def load_classified_locations(city: str) -> pd.DataFrame:
    classified_path = (
        RESULTS_DIR
        / city
        / "location_review"
        / "records_classified.csv"
    )

    if not classified_path.exists():
        raise FileNotFoundError(
            "Classified location data was not found: "
            f"{classified_path.resolve()}. "
            "Run code.analysis.review_b2c_locations first."
        )

    classified_locations = pd.read_csv(classified_path)

    required_columns = {
        "Record_Service_Type_Code",
        "Latitude",
        "Longitude",
    }

    missing_columns = required_columns.difference(
        classified_locations.columns
    )

    if missing_columns:
        raise ValueError(
            "Classified location data is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    return classified_locations



def resolve_demand_instance_path(
    city: str,
    scenario: str,
    instance_size: int,
    *,
    demand_seed: int | None = None,
    demand_instance_id: str | None = None,
) -> Path:
    """Return the exact demand CSV selected by the experiment config."""

    demand_folder = RESULTS_DIR / city / "demand"

    if demand_instance_id is not None:
        filename = str(demand_instance_id)
        if not filename.lower().endswith(".csv"):
            filename += ".csv"
        return demand_folder / filename

    if demand_seed is not None:
        return (
            demand_folder
            / f"demand_{scenario}_{instance_size}_seed_{int(demand_seed)}.csv"
        )

    return demand_folder / f"demand_{scenario}_{instance_size}.csv"


def load_demand_instance(
    city: str,
    scenario: str,
    instance_size: int,
    *,
    demand_seed: int | None = None,
    demand_instance_id: str | None = None,
) -> pd.DataFrame:
    """Load one immutable demand instance and normalize its routing schema.

    Backward compatibility is preserved: when neither ``demand_seed`` nor
    ``demand_instance_id`` is provided, the legacy
    ``demand_<scenario>_<size>.csv`` filename is used.
    """

    demand_path = resolve_demand_instance_path(
        city,
        scenario,
        instance_size,
        demand_seed=demand_seed,
        demand_instance_id=demand_instance_id,
    )

    if not demand_path.exists():
        raise FileNotFoundError(
            "Demand instance was not found: "
            f"{demand_path.resolve()}"
        )

    demand = pd.read_csv(demand_path)
    validate_required_columns(
        demand,
        required_columns={"customer_id", "lat", "lon", "demand"},
        dataset_name=f"{city} demand instance",
    )

    demand = demand.rename(
        columns={"lat": "Latitude", "lon": "Longitude", "demand": "Demand"}
    ).copy()
    demand["Latitude"] = pd.to_numeric(demand["Latitude"], errors="coerce")
    demand["Longitude"] = pd.to_numeric(demand["Longitude"], errors="coerce")
    demand["Demand"] = pd.to_numeric(demand["Demand"], errors="coerce")
    demand = demand.dropna(subset=["Latitude", "Longitude", "Demand"])

    if demand.empty:
        raise ValueError(f"Demand instance is empty after validation: {demand_path}")
    if (demand["Demand"] <= 0).any():
        raise ValueError("Every demand record must contain a positive package demand.")

    if "scenario" in demand.columns:
        observed_scenarios = set(demand["scenario"].dropna().astype(str))
        if observed_scenarios and observed_scenarios != {scenario}:
            raise ValueError(
                f"Demand instance {demand_path.name} contains scenario values "
                f"{sorted(observed_scenarios)}, expected {scenario!r}."
            )
    if "instance_size" in demand.columns:
        observed_sizes = set(
            pd.to_numeric(demand["instance_size"], errors="coerce")
            .dropna()
            .astype(int)
        )
        if observed_sizes and observed_sizes != {int(instance_size)}:
            raise ValueError(
                f"Demand instance {demand_path.name} contains instance_size "
                f"values {sorted(observed_sizes)}, expected {instance_size}."
            )

    resolved_seed = int(demand_seed) if demand_seed is not None else None
    if resolved_seed is None and "demand_seed" in demand.columns:
        seed_values = (
            pd.to_numeric(demand["demand_seed"], errors="coerce")
            .dropna()
            .astype(int)
            .unique()
        )
        if len(seed_values) == 1:
            resolved_seed = int(seed_values[0])

    demand.attrs["demand_source_file"] = demand_path.name
    demand.attrs["demand_instance_id"] = demand_path.stem
    demand.attrs["demand_seed"] = resolved_seed
    return demand
