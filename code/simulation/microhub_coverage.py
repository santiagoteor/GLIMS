"""Demand-based geographic microhub coverage; diagnostic, not an assignment constraint."""
from __future__ import annotations
import numpy as np
import pandas as pd


def calculate_microhub_demand_coverage(customers: pd.DataFrame, microhubs: pd.DataFrame, radius_m: float = 3000.0) -> dict:
    if radius_m <= 0:
        raise ValueError("Coverage radius must be positive")
    for label, frame in (("customers", customers), ("microhubs", microhubs)):
        missing = {"Latitude", "Longitude"} - set(frame.columns)
        if missing:
            raise ValueError(f"{label} missing columns: {sorted(missing)}")
    if "Demand" not in customers:
        raise ValueError("customers missing Demand column")
    if microhubs.empty:
        raise ValueError("No city microhubs available for coverage calculation")
    lat1 = np.radians(customers["Latitude"].to_numpy(dtype=float))[:, None]
    lon1 = np.radians(customers["Longitude"].to_numpy(dtype=float))[:, None]
    lat2 = np.radians(microhubs["Latitude"].to_numpy(dtype=float))[None, :]
    lon2 = np.radians(microhubs["Longitude"].to_numpy(dtype=float))[None, :]
    h = np.sin((lat2-lat1)/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin((lon2-lon1)/2)**2
    distances = 6371008.8 * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    covered = (distances <= radius_m).any(axis=1)
    demand = customers["Demand"].to_numpy(dtype=float)
    n = len(customers)
    packages = float(demand.sum())
    covered_n = int(covered.sum())
    covered_packages = float(demand[covered].sum())
    return {
        "microhub_coverage_radius_m": float(radius_m),
        "microhub_customers_total": n,
        "microhub_customers_covered": covered_n,
        "microhub_customers_uncovered": n-covered_n,
        "microhub_customer_coverage_pct": 100*covered_n/n if n else 0.0,
        "microhub_packages_total": packages,
        "microhub_packages_covered": covered_packages,
        "microhub_packages_uncovered": packages-covered_packages,
        "microhub_package_coverage_pct": 100*covered_packages/packages if packages else 0.0,
        "microhub_nearest_distance_m_mean": float(distances.min(axis=1).mean()) if n else None,
        "microhub_coverage_is_enforced": False,
    }
