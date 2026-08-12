from __future__ import annotations

import argparse
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
GLIMS_DIR = BASE_DIR.parent.parent
DEFAULT_CONFIG = GLIMS_DIR / "configs" / "demand" / "demand_generation.json"
SUPPORTED_CITIES = ("barcelona", "madrid", "valencia")
SUPPORTED_SCENARIOS = ("low", "medium", "high")


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
        raise ValueError(f"{name} must be a non-empty value or list.")
    return tuple(value)


def load_generation_config(path: Path) -> DemandGenerationConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Demand generation configuration was not found: {path.resolve()}"
        )

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
        raise ValueError(
            f"Unsupported cities: {unknown_cities}. Expected {SUPPORTED_CITIES}."
        )

    unknown_scenarios = sorted(set(scenarios).difference(SUPPORTED_SCENARIOS))
    if unknown_scenarios:
        raise ValueError(
            f"Unsupported scenarios: {unknown_scenarios}. Expected {SUPPORTED_SCENARIOS}."
        )

    if any(size <= 0 for size in sizes):
        raise ValueError("Every requested size must be greater than zero.")
    if len(set(sizes)) != len(sizes):
        raise ValueError("sizes cannot contain duplicates.")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds cannot contain duplicates.")

    return DemandGenerationConfig(
        cities=cities,
        scenarios=scenarios,
        sizes=sizes,
        seeds=seeds,
        overwrite=overwrite,
    )


def _module_for_city(city: str):
    return importlib.import_module(
        f"code.preprocessing.generate_instances_demand_{city}"
    )


def _prepare_weighted_addresses(city: str, module):
    print(f"\n[{city}] Loading source data...")
    addresses = module.load_addresses(module.ADDRESSES_PATH)
    print(f"[{city}] Candidate addresses loaded: {len(addresses):,}")

    if city == "barcelona":
        addresses = module.add_seccio_censal_key(addresses)
    elif city == "madrid":
        addresses = module.add_census_section_key(addresses)

    population = module.load_population(module.POPULATION_PATH)
    addresses = module.merge_population(addresses, population)
    addresses = module.compute_sampling_weights(addresses)
    print(f"[{city}] Addresses with valid sampling weight: {len(addresses):,}")
    return addresses


def _demand_rng_seed(base_seed: int, scenario: str, size: int) -> int:
    # Stable deterministic sub-seed. It intentionally does not use Python's
    # hash(), whose value is randomized between interpreter processes.
    scenario_code = {"low": 11, "medium": 23, "high": 37}[scenario]
    return int((base_seed * 1_000_003 + scenario_code * 10_007 + size) % (2**32))


def _instance_columns(instance: pd.DataFrame) -> list[str]:
    preferred = [
        "customer_id",
        "lon",
        "lat",
        "demand",
        "scenario",
        "instance_size",
        "demand_seed",
        "demand_instance_id",
    ]
    remainder = [c for c in instance.columns if c not in preferred]
    return [c for c in preferred if c in instance.columns] + remainder


def _write_csv(frame: pd.DataFrame, path: Path, *, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        print(f"  SKIP existing: {path.name}")
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
    module = _module_for_city(city)
    sizes = tuple(sorted(int(size) for size in sizes))
    scenarios = tuple(scenarios)
    seeds = tuple(seeds)
    max_size = max(sizes)

    weighted_addresses = _prepare_weighted_addresses(city, module)
    if max_size > len(weighted_addresses):
        raise ValueError(
            f"[{city}] Requested {max_size:,} customers, but only "
            f"{len(weighted_addresses):,} candidate addresses with a valid "
            "population weight are available."
        )

    output_dir = module.RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    for seed in seeds:
        print(f"\n[{city}] Generating spatial master sample with seed={seed}...")

        # The original city-specific sampling functions use these module-level
        # variables. Replacing them only in memory preserves the original
        # scripts while keeping exactly their city-specific sampling method.
        module.RANDOM_SEED = int(seed)
        module.rng = np.random.default_rng(seed)

        master = module.sample_master_customers(weighted_addresses, max_size)
        master["demand_seed"] = int(seed)
        master_path = output_dir / f"master_customers_{max_size}_seed_{seed}.csv"
        if _write_csv(master, master_path, overwrite=overwrite):
            print(f"  Saved master: {master_path.name} ({len(master):,} customers)")

        for size in sizes:
            subset = master.iloc[:size].copy()

            for scenario in scenarios:
                # Make package-demand draws reproducible independently of the
                # order in which scenarios/sizes appear in the config.
                module.rng = np.random.default_rng(
                    _demand_rng_seed(seed, scenario, size)
                )
                instance = module.assign_demand(subset, scenario)
                instance["instance_size"] = int(size)
                instance["demand_seed"] = int(seed)
                instance_id = f"demand_{scenario}_{size}_seed_{seed}"
                instance["demand_instance_id"] = instance_id
                instance = instance[_instance_columns(instance)]

                instance_path = output_dir / f"{instance_id}.csv"
                if _write_csv(instance, instance_path, overwrite=overwrite):
                    print(
                        f"  Saved {instance_path.name}: {len(instance):,} customers | "
                        f"packages={int(instance['demand'].sum()):,}"
                    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reproducible GLIMS demand instances for Barcelona, Madrid, "
            "and/or Valencia without modifying the original city generators."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"JSON config path (default: {DEFAULT_CONFIG}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    config = load_generation_config(args.config)

    print("=" * 68)
    print("GLIMS DEMAND INSTANCE GENERATOR")
    print("=" * 68)
    print(f"Cities: {', '.join(config.cities)}")
    print(f"Scenarios: {', '.join(config.scenarios)}")
    print(f"Sizes: {', '.join(f'{x:,}' for x in config.sizes)}")
    print(f"Seeds: {', '.join(str(x) for x in config.seeds)}")
    print(f"Overwrite existing seeded files: {config.overwrite}")
    print("Original city-specific generator files are not modified.")
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
