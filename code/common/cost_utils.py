from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class CostBreakdown:
    """Detailed economic result for one logistics model."""

    route_distance_cost: float = 0.0
    route_labor_cost: float = 0.0
    facility_service_cost: float = 0.0
    facility_fixed_cost: float = 0.0
    warehouse_fixed_cost: float = 0.0
    warehouse_handling_cost: float = 0.0
    vehicle_fixed_cost: float = 0.0
    capital_allocation_cost: float = 0.0
    customer_time_cost: float = 0.0
    carbon_cost: float = 0.0

    @property
    def route_operating_cost(self) -> float:
        return self.route_distance_cost + self.route_labor_cost

    @property
    def total_cost(self) -> float:
        return sum(asdict(self).values())


def load_cost_parameters(csv_path: Path) -> dict[str, float]:
    """Load a unique name-value cost catalogue from CSV."""

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Cost parameter file was not found: {csv_path.resolve()}"
        )

    table = pd.read_csv(csv_path, encoding="utf-8-sig")
    required_columns = {"parameter", "value"}
    missing_columns = required_columns.difference(table.columns)

    if missing_columns:
        raise ValueError(
            "Cost parameter file is missing required columns: "
            f"{sorted(missing_columns)}"
        )

    names = table["parameter"].astype("string").str.strip()
    if names.isna().any() or names.eq("").any():
        raise ValueError("Every cost parameter must have a non-empty name.")

    duplicated = names[names.duplicated()].tolist()
    if duplicated:
        raise ValueError(f"Duplicated cost parameters: {duplicated}")

    values = pd.to_numeric(table["value"], errors="raise")
    if (values < 0).any():
        invalid = names[values < 0].tolist()
        raise ValueError(
            "Cost parameters cannot be negative. Invalid parameters: "
            f"{invalid}"
        )

    return dict(zip(names.astype(str), values.astype(float)))


def get_cost_parameter(
    parameters: dict[str, float],
    name: str,
    default: float = 0.0,
) -> float:
    """Return one cost parameter, defaulting to zero when it is optional."""

    return float(parameters.get(name, default))


def calculate_direct_route_operating_cost(
    *,
    distance_km: float,
    total_duration_min: float,
    route_count: int,
    route_start_time_per_route_min: float,
    cost_per_km: float,
    labor_cost_per_hour: float,
) -> tuple[float, float, float]:
    """Return distance, labour, and total direct route operating costs.

    ``cost_per_km`` is treated as an aggregate variable vehicle cost. It may
    already include energy, maintenance, tyres and distance-related wear, so
    those items must not be added again unless this aggregate parameter is
    replaced by a disaggregated vehicle-cost model.

    The economic boundary starts when each route departs its origin. Route
    preparation/loading time is excluded, while OSRM travel time and stop
    service time remain included.
    """

    distance_km = float(distance_km)
    total_duration_min = float(total_duration_min)
    route_count = int(route_count)
    route_start_time_per_route_min = float(route_start_time_per_route_min)

    in_route_duration_min = max(
        0.0,
        total_duration_min
        - route_count * route_start_time_per_route_min,
    )
    distance_cost = distance_km * float(cost_per_km)
    labor_cost = (in_route_duration_min / 60.0) * float(labor_cost_per_hour)

    return distance_cost, labor_cost, distance_cost + labor_cost


def calculate_additional_costs(
    *,
    package_count: int,
    route_count: int,
    used_facility_count: int,
    co2_kg: float,
    customer_travel_min: float,
    facility_commission_per_package: float = 0.0,
    facility_fixed_cost_per_day: float = 0.0,
    warehouse_fixed_cost_per_day: float = 0.0,
    warehouse_handling_cost_per_package: float = 0.0,
    vehicle_fixed_cost_per_route: float = 0.0,
    facility_capex_allocation_per_day: float = 0.0,
    vehicle_capex_allocation_per_route: float = 0.0,
    customer_time_cost_per_hour: float = 0.0,
    carbon_cost_per_kg: float = 0.0,
) -> dict[str, float]:
    """Calculate optional non-route components using a common formula."""

    package_count = int(package_count)
    route_count = int(route_count)
    used_facility_count = int(used_facility_count)

    return {
        "facility_service_cost": (
            package_count * float(facility_commission_per_package)
        ),
        "facility_fixed_cost": (
            used_facility_count * float(facility_fixed_cost_per_day)
        ),
        "warehouse_fixed_cost": float(warehouse_fixed_cost_per_day),
        "warehouse_handling_cost": (
            package_count * float(warehouse_handling_cost_per_package)
        ),
        "vehicle_fixed_cost": (
            route_count * float(vehicle_fixed_cost_per_route)
        ),
        "capital_allocation_cost": (
            used_facility_count * float(facility_capex_allocation_per_day)
            + route_count * float(vehicle_capex_allocation_per_route)
        ),
        "customer_time_cost": (
            float(customer_travel_min) / 60.0
            * float(customer_time_cost_per_hour)
        ),
        "carbon_cost": float(co2_kg) * float(carbon_cost_per_kg),
    }
