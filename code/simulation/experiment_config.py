from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from code.common.constants import CITIES, OSRM_PORTS


@dataclass(frozen=True)
class RoutingExperimentConfig:
    algorithm: str = "cws"
    ils_max_iterations: int = 100
    ils_max_no_improvement: int | None = 20
    ils_perturbation_moves: int = 2
    ils_random_seed: int | None = 42


@dataclass(frozen=True)
class TrafficExperimentConfig:
    static_profile: str = "baseline"
    static_multiplier_override: float | None = None
    time_profile: str = "weekday_schedule"
    simulation_date: str = "2026-08-03"
    shift_start: str = "09:00"
    shift_duration_min: float = 480.0


@dataclass(frozen=True)
class OutputExperimentConfig:
    save_route_details: bool = True
    save_configuration: bool = True
    save_metadata: bool = True
    save_route_geometry: bool = False


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_name: str = "glims_experiment"
    city: str = "madrid"
    zones: list[str] | None = None
    demand_scenario: str = "medium"
    instance_size: int = 100
    osrm_profile: str = "driving"
    routing: RoutingExperimentConfig = field(
        default_factory=RoutingExperimentConfig
    )
    traffic: TrafficExperimentConfig = field(
        default_factory=TrafficExperimentConfig
    )
    output: OutputExperimentConfig = field(
        default_factory=OutputExperimentConfig
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_experiment_config(path: Path) -> ExperimentConfig:
    if not path.exists():
        raise FileNotFoundError(
            f"Experiment configuration was not found: {path.resolve()}"
        )

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    if not isinstance(raw, dict):
        raise ValueError("Experiment configuration must be a JSON object.")

    config = ExperimentConfig(
        experiment_name=str(
            raw.get("experiment_name", "glims_experiment")
        ),
        city=str(raw.get("city", "madrid")),
        zones=_normalize_zones(raw.get("zones")),
        demand_scenario=str(raw.get("demand_scenario", "medium")),
        instance_size=int(raw.get("instance_size", 100)),
        osrm_profile=str(raw.get("osrm_profile", "driving")),
        routing=RoutingExperimentConfig(**raw.get("routing", {})),
        traffic=TrafficExperimentConfig(**raw.get("traffic", {})),
        output=OutputExperimentConfig(**raw.get("output", {})),
    )
    validate_experiment_config(config)
    return config


def validate_experiment_config(config: ExperimentConfig) -> None:
    if config.city not in {*CITIES, "all"}:
        raise ValueError(
            f"Unsupported city {config.city!r}. Expected one of "
            f"{sorted([*CITIES, 'all'])}."
        )
    if config.city == "all" and config.zones:
        raise ValueError("zones cannot be used when city is 'all'.")
    if config.demand_scenario not in {"low", "medium", "high"}:
        raise ValueError(
            "demand_scenario must be 'low', 'medium', or 'high'."
        )
    if config.instance_size <= 0:
        raise ValueError("instance_size must be greater than zero.")
    if config.osrm_profile not in OSRM_PORTS["madrid"]:
        raise ValueError(
            f"Unsupported OSRM profile: {config.osrm_profile!r}."
        )
    if config.routing.algorithm not in {"cws", "ils"}:
        raise ValueError("routing.algorithm must be 'cws' or 'ils'.")
    if config.routing.ils_max_iterations <= 0:
        raise ValueError("ils_max_iterations must be greater than zero.")
    if config.routing.ils_perturbation_moves < 0:
        raise ValueError("ils_perturbation_moves cannot be negative.")
    if config.traffic.shift_duration_min <= 0:
        raise ValueError("shift_duration_min must be greater than zero.")
    if (
        config.traffic.static_multiplier_override is not None
        and config.traffic.static_multiplier_override <= 0
    ):
        raise ValueError(
            "static_multiplier_override must be greater than zero."
        )


def resolve_experiment_config(
    *,
    base: ExperimentConfig,
    overrides: dict[str, Any],
) -> ExperimentConfig:
    routing = RoutingExperimentConfig(
        algorithm=_pick(overrides.get("routing_algorithm"), base.routing.algorithm),
        ils_max_iterations=_pick(
            overrides.get("ils_max_iterations"),
            base.routing.ils_max_iterations,
        ),
        ils_max_no_improvement=_pick(
            overrides.get("ils_max_no_improvement"),
            base.routing.ils_max_no_improvement,
        ),
        ils_perturbation_moves=_pick(
            overrides.get("ils_perturbation_moves"),
            base.routing.ils_perturbation_moves,
        ),
        ils_random_seed=_pick(
            overrides.get("ils_random_seed"),
            base.routing.ils_random_seed,
        ),
    )
    traffic = TrafficExperimentConfig(
        static_profile=_pick(
            overrides.get("traffic_profile"),
            base.traffic.static_profile,
        ),
        static_multiplier_override=_pick(
            overrides.get("traffic_multiplier"),
            base.traffic.static_multiplier_override,
        ),
        time_profile=_pick(
            overrides.get("time_traffic_profile"),
            base.traffic.time_profile,
        ),
        simulation_date=_pick(
            overrides.get("simulation_date"),
            base.traffic.simulation_date,
        ),
        shift_start=_pick(
            overrides.get("shift_start"),
            base.traffic.shift_start,
        ),
        shift_duration_min=_pick(
            overrides.get("shift_duration_min"),
            base.traffic.shift_duration_min,
        ),
    )
    output = OutputExperimentConfig(
        save_route_details=base.output.save_route_details,
        save_configuration=base.output.save_configuration,
        save_metadata=base.output.save_metadata,
        save_route_geometry=_pick(
            overrides.get("save_route_geometry"),
            base.output.save_route_geometry,
        ),
    )
    resolved = ExperimentConfig(
        experiment_name=base.experiment_name,
        city=_pick(overrides.get("city"), base.city),
        zones=_pick(overrides.get("zones"), base.zones),
        demand_scenario=_pick(
            overrides.get("demand_scenario"),
            base.demand_scenario,
        ),
        instance_size=_pick(
            overrides.get("instance_size"),
            base.instance_size,
        ),
        osrm_profile=_pick(
            overrides.get("profile"),
            base.osrm_profile,
        ),
        routing=routing,
        traffic=traffic,
        output=output,
    )
    validate_experiment_config(resolved)
    return resolved


def default_experiment_config() -> ExperimentConfig:
    return ExperimentConfig()


def _pick(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value


def _normalize_zones(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("zones must be null or a JSON array of names.")
    zones = [str(item).strip() for item in value if str(item).strip()]
    return zones or None
