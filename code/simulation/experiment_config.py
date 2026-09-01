from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from code.common.constants import CITIES, OSRM_PORTS


@dataclass(frozen=True)
class RoutingExperimentConfig:
    algorithm: str = "cws"
    cws_allow_route_reversal: bool = False
    ils_max_iterations: int = 100
    ils_max_no_improvement: int | None = 20
    ils_destruction_percentage_step: int = 10
    ils_max_destruction_percentage: int = 100
    ils_max_full_destruction_attempts: int = 2
    ils_biased_cws_alpha_min: float = 0.05
    ils_biased_cws_alpha_max: float = 0.25
    ils_biased_cws_sampling_batch_size: int = 8192
    ils_restricted_relocate: bool = True
    ils_relocate_candidate_fraction: float = 0.10
    ils_relocate_neighbor_routes: int = 5
    ils_relocate_max_insertions: int = 3
    ils_random_seed: int | list[int] | None = 42
    last_service_deadline_enabled: bool = False
    last_service_margin_min: float = 30.0


@dataclass(frozen=True)
class LastMeterAccessExperimentConfig:
    enabled: bool = False
    walking_speed_m_s: float = 1.2
    round_trip: bool = True
    models: list[str] | None = field(
        default_factory=lambda: ["M1", "M2"]
    )


@dataclass(frozen=True)
class TrafficExperimentConfig:
    static_profile: str = "baseline"
    static_multiplier_override: float | None = None
    time_profile: str = "weekday_schedule"
    simulation_date: str = "2026-08-03"
    shift_start: str = "09:00"
    shift_duration_min: float = 480.0


@dataclass(frozen=True)
class FacilityFilterExperimentConfig:
    enabled: bool = False
    initial_buffer_m: float = 500.0
    buffer_increment_m: float = 500.0
    maximum_buffer_m: float = 5000.0
    minimum_candidates: int = 5


@dataclass(frozen=True)
class FacilityAssignmentExperimentConfig:
    pudo_capacity_mode: str = "configured"
    microhub_capacity_mode: str = "configured"


@dataclass(frozen=True)
class OsrmCacheExperimentConfig:
    enabled: bool = False
    directory: str = ".glims_cache/osrm"


@dataclass(frozen=True)
class OutputExperimentConfig:
    save_route_details: bool = True
    save_route_stops: bool = True
    save_configuration: bool = True
    save_metadata: bool = True
    save_route_geometry: bool = False
    summary_detail: str = "full"
    audit_detail: str = "summary"
    performance_profile: str = "basic"
    show_progress: bool = False

@dataclass(frozen=True)
class ExperimentConfig:
    experiment_name: str = "glims_experiment"
    city: str = "madrid"
    zones: list[str] | None = None
    demand_scenario: str | list[str] = "medium"
    instance_size: int | list[int] = 100
    demand_seed: int | list[int] | None = None
    demand_instance_id: str | None = None
    osrm_profile: str = "driving"
    routing: RoutingExperimentConfig = field(
        default_factory=RoutingExperimentConfig
    )
    last_meter_access: LastMeterAccessExperimentConfig = field(
        default_factory=LastMeterAccessExperimentConfig
    )
    traffic: TrafficExperimentConfig = field(
        default_factory=TrafficExperimentConfig
    )
    facility_filter: FacilityFilterExperimentConfig = field(
        default_factory=FacilityFilterExperimentConfig
    )
    facility_assignment: FacilityAssignmentExperimentConfig = field(
        default_factory=FacilityAssignmentExperimentConfig
    )
    osrm_cache: OsrmCacheExperimentConfig = field(
        default_factory=OsrmCacheExperimentConfig
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
        demand_scenario=_normalize_scenario_value(
            raw.get("demand_scenario", "medium")
        ),
        instance_size=_normalize_instance_size_value(
            raw.get("instance_size", 100)
        ),
        demand_seed=_normalize_seed_value(
            raw.get("demand_seed"),
            field_name="demand_seed",
        ),
        demand_instance_id=(
            str(raw["demand_instance_id"])
            if raw.get("demand_instance_id") is not None
            else None
        ),
        osrm_profile=str(raw.get("osrm_profile", "driving")),
        routing=RoutingExperimentConfig(**raw.get("routing", {})),
        last_meter_access=LastMeterAccessExperimentConfig(
            **raw.get("last_meter_access", {})
        ),
        traffic=TrafficExperimentConfig(**raw.get("traffic", {})),
        facility_filter=FacilityFilterExperimentConfig(
            **raw.get("facility_filter", {})
        ),
        facility_assignment=FacilityAssignmentExperimentConfig(
            **raw.get("facility_assignment", {})
        ),
        osrm_cache=OsrmCacheExperimentConfig(
            **raw.get("osrm_cache", {})
        ),
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
    scenarios = (
        config.demand_scenario
        if isinstance(config.demand_scenario, list)
        else [config.demand_scenario]
    )
    if not scenarios:
        raise ValueError("demand_scenario list cannot be empty.")
    invalid_scenarios = sorted(
        set(scenarios).difference({"low", "medium", "high"})
    )
    if invalid_scenarios:
        raise ValueError(
            "demand_scenario values must be 'low', 'medium', or 'high'. "
            f"Invalid values: {invalid_scenarios}."
        )
    if len(set(scenarios)) != len(scenarios):
        raise ValueError("demand_scenario cannot contain duplicate values.")

    sizes = (
        config.instance_size
        if isinstance(config.instance_size, list)
        else [config.instance_size]
    )
    if not sizes:
        raise ValueError("instance_size list cannot be empty.")
    if any(
        isinstance(size, bool) or not isinstance(size, int) or size <= 0
        for size in sizes
    ):
        raise ValueError(
            "Every instance_size value must be an integer greater than zero."
        )
    if len(set(sizes)) != len(sizes):
        raise ValueError("instance_size cannot contain duplicate values.")
    if config.output.summary_detail not in {"compact", "full"}:
        raise ValueError(
            "output.summary_detail must be either 'compact' or 'full'."
        )
    if config.output.audit_detail not in {"summary", "full"}:
        raise ValueError(
            "output.audit_detail must be either 'summary' or 'full'."
        )
    if config.output.performance_profile not in {
        "off",
        "basic",
        "detailed",
    }:
        raise ValueError(
            "output.performance_profile must be 'off', 'basic', "
            "or 'detailed'."
        )
    if config.demand_instance_id is not None:
        instance_id = config.demand_instance_id
        if Path(instance_id).name != instance_id:
            raise ValueError(
                "demand_instance_id must be a filename/stem, not a path."
            )
    demand_seed = config.demand_seed
    if isinstance(demand_seed, list):
        if not demand_seed:
            raise ValueError("demand_seed list cannot be empty.")
        if any(
            isinstance(seed, bool) or not isinstance(seed, int)
            for seed in demand_seed
        ):
            raise ValueError(
                "Every value in demand_seed must be an integer."
            )
        if len(set(demand_seed)) != len(demand_seed):
            raise ValueError(
                "demand_seed cannot contain duplicate seeds."
            )
    elif (
        demand_seed is not None
        and (
            isinstance(demand_seed, bool)
            or not isinstance(demand_seed, int)
        )
    ):
        raise ValueError(
            "demand_seed must be an integer, a list of integers, or null."
        )

    if demand_seed is not None and config.demand_instance_id is not None:
        raise ValueError(
            "Use either demand_seed or demand_instance_id, not both. "
            "demand_instance_id is intended for explicit file selection."
        )
    if config.demand_instance_id is not None and (
        isinstance(config.demand_scenario, list)
        or isinstance(config.instance_size, list)
    ):
        raise ValueError(
            "demand_instance_id requires scalar demand_scenario and "
            "instance_size values; it cannot be combined with batch lists."
        )
    if config.osrm_profile not in OSRM_PORTS["madrid"]:
        raise ValueError(
            f"Unsupported OSRM profile: {config.osrm_profile!r}."
        )
    if config.routing.algorithm not in {"cws", "ils"}:
        raise ValueError("routing.algorithm must be 'cws' or 'ils'.")
    if config.routing.ils_max_iterations <= 0:
        raise ValueError("ils_max_iterations must be greater than zero.")
    if config.routing.ils_destruction_percentage_step <= 0:
        raise ValueError("ils_destruction_percentage_step must be greater than zero.")
    if not (0 < config.routing.ils_max_destruction_percentage <= 100):
        raise ValueError("ils_max_destruction_percentage must be between 0 (exclusive) and 100 (inclusive).")
    if config.routing.ils_max_full_destruction_attempts <= 0:
        raise ValueError(
            "ils_max_full_destruction_attempts must be greater than zero."
        )
    if not (0 < config.routing.ils_biased_cws_alpha_min < 1):
        raise ValueError("ils_biased_cws_alpha_min must be strictly between 0 and 1.")
    if not (0 < config.routing.ils_biased_cws_alpha_max < 1):
        raise ValueError("ils_biased_cws_alpha_max must be strictly between 0 and 1.")
    if config.routing.ils_biased_cws_alpha_min > config.routing.ils_biased_cws_alpha_max:
        raise ValueError("ils_biased_cws_alpha_min cannot exceed ils_biased_cws_alpha_max.")
    if config.routing.ils_biased_cws_sampling_batch_size <= 0:
        raise ValueError(
            "ils_biased_cws_sampling_batch_size must be greater than zero."
        )
    if not (0 < config.routing.ils_relocate_candidate_fraction <= 1):
        raise ValueError(
            "ils_relocate_candidate_fraction must be greater than 0 and at most 1."
        )
    if config.routing.ils_relocate_neighbor_routes <= 0:
        raise ValueError("ils_relocate_neighbor_routes must be greater than zero.")
    if config.routing.ils_relocate_max_insertions <= 0:
        raise ValueError("ils_relocate_max_insertions must be greater than zero.")
    ils_seed = config.routing.ils_random_seed
    if isinstance(ils_seed, list):
        if not ils_seed:
            raise ValueError("ils_random_seed list cannot be empty.")
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in ils_seed):
            raise ValueError(
                "Every value in ils_random_seed must be an integer."
            )
        if len(set(ils_seed)) != len(ils_seed):
            raise ValueError(
                "ils_random_seed cannot contain duplicate seeds."
            )
    elif (
        ils_seed is not None
        and (isinstance(ils_seed, bool) or not isinstance(ils_seed, int))
    ):
        raise ValueError(
            "ils_random_seed must be an integer, a list of integers, or null."
        )
    if isinstance(demand_seed, list) and isinstance(ils_seed, list):
        if len(demand_seed) != len(ils_seed):
            raise ValueError(
                "When demand_seed and ils_random_seed are both lists, "
                "they must have the same length for 1:1 pairing."
            )
    if config.routing.last_service_margin_min < 0:
        raise ValueError("last_service_margin_min cannot be negative.")
    if (
        config.routing.last_service_deadline_enabled
        and config.routing.last_service_margin_min
        >= config.traffic.shift_duration_min
    ):
        raise ValueError(
            "When supply_arrival_deadline_enabled is true, "
            "supply_arrival_margin_min must be smaller than "
            "traffic.shift_duration_min."
        )
    if config.last_meter_access.walking_speed_m_s <= 0:
        raise ValueError(
            "last_meter_access.walking_speed_m_s must be greater than zero."
        )
    last_meter_models = config.last_meter_access.models
    if last_meter_models is not None:
        if not isinstance(last_meter_models, list):
            raise ValueError(
                "last_meter_access.models must be a list of model codes or null."
            )
        allowed_models = {"M1", "M2", "M3", "M4", "M5"}
        normalized_models = [str(model).upper() for model in last_meter_models]
        invalid_models = sorted(set(normalized_models) - allowed_models)
        if invalid_models:
            raise ValueError(
                "Unsupported last_meter_access.models values: "
                f"{invalid_models}. Expected M1-M5."
            )
        if len(set(normalized_models)) != len(normalized_models):
            raise ValueError(
                "last_meter_access.models cannot contain duplicates."
            )

    if config.facility_filter.initial_buffer_m < 0:
        raise ValueError(
            "facility_filter.initial_buffer_m cannot be negative."
        )
    if config.facility_filter.buffer_increment_m <= 0:
        raise ValueError(
            "facility_filter.buffer_increment_m must be greater than zero."
        )
    if (
        config.facility_filter.maximum_buffer_m
        < config.facility_filter.initial_buffer_m
    ):
        raise ValueError(
            "facility_filter.maximum_buffer_m must be greater than or equal "
            "to facility_filter.initial_buffer_m."
        )
    if config.facility_filter.minimum_candidates <= 0:
        raise ValueError(
            "facility_filter.minimum_candidates must be greater than zero."
        )
    allowed_capacity_modes = {"configured", "unlimited"}
    if (
        config.facility_assignment.pudo_capacity_mode
        not in allowed_capacity_modes
    ):
        raise ValueError(
            "facility_assignment.pudo_capacity_mode must be "
            "'configured' or 'unlimited'."
        )
    if (
        config.facility_assignment.microhub_capacity_mode
        not in allowed_capacity_modes
    ):
        raise ValueError(
            "facility_assignment.microhub_capacity_mode must be "
            "'configured' or 'unlimited'."
        )
    if not str(config.osrm_cache.directory).strip():
        raise ValueError("osrm_cache.directory cannot be empty.")
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
        cws_allow_route_reversal=_pick(
            overrides.get("cws_allow_route_reversal"),
            base.routing.cws_allow_route_reversal,
        ),
        ils_max_iterations=_pick(
            overrides.get("ils_max_iterations"),
            base.routing.ils_max_iterations,
        ),
        ils_max_no_improvement=_pick(
            overrides.get("ils_max_no_improvement"),
            base.routing.ils_max_no_improvement,
        ),
        ils_destruction_percentage_step=_pick(
            overrides.get("ils_destruction_percentage_step"),
            base.routing.ils_destruction_percentage_step,
        ),
        ils_max_destruction_percentage=_pick(
            overrides.get("ils_max_destruction_percentage"),
            base.routing.ils_max_destruction_percentage,
        ),
        ils_max_full_destruction_attempts=_pick(
            overrides.get("ils_max_full_destruction_attempts"),
            base.routing.ils_max_full_destruction_attempts,
        ),
        ils_biased_cws_alpha_min=_pick(
            overrides.get("ils_biased_cws_alpha_min"),
            base.routing.ils_biased_cws_alpha_min,
        ),
        ils_biased_cws_alpha_max=_pick(
            overrides.get("ils_biased_cws_alpha_max"),
            base.routing.ils_biased_cws_alpha_max,
        ),
        ils_biased_cws_sampling_batch_size=_pick(
            overrides.get("ils_biased_cws_sampling_batch_size"),
            base.routing.ils_biased_cws_sampling_batch_size,
        ),
        ils_restricted_relocate=_pick(
            overrides.get("ils_restricted_relocate"),
            base.routing.ils_restricted_relocate,
        ),
        ils_relocate_candidate_fraction=_pick(
            overrides.get("ils_relocate_candidate_fraction"),
            base.routing.ils_relocate_candidate_fraction,
        ),
        ils_relocate_neighbor_routes=_pick(
            overrides.get("ils_relocate_neighbor_routes"),
            base.routing.ils_relocate_neighbor_routes,
        ),
        ils_relocate_max_insertions=_pick(
            overrides.get("ils_relocate_max_insertions"),
            base.routing.ils_relocate_max_insertions,
        ),
        ils_random_seed=_pick(
            overrides.get("ils_random_seed"),
            base.routing.ils_random_seed,
        ),
        last_service_deadline_enabled=(
            base.routing.last_service_deadline_enabled
        ),
        last_service_margin_min=base.routing.last_service_margin_min,
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
        save_route_stops=base.output.save_route_stops,
        save_configuration=base.output.save_configuration,
        save_metadata=base.output.save_metadata,
        save_route_geometry=_pick(
            overrides.get("save_route_geometry"),
            base.output.save_route_geometry,
        ),
        summary_detail=base.output.summary_detail,
        audit_detail=base.output.audit_detail,
        performance_profile=base.output.performance_profile,
        show_progress=base.output.show_progress,
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
        demand_seed=_pick(
            overrides.get("demand_seed"),
            base.demand_seed,
        ),
        demand_instance_id=_pick(
            overrides.get("demand_instance_id"),
            base.demand_instance_id,
        ),
        osrm_profile=_pick(
            overrides.get("profile"),
            base.osrm_profile,
        ),
        routing=routing,
        last_meter_access=base.last_meter_access,
        traffic=traffic,
        output=output,
        facility_filter=base.facility_filter,
        facility_assignment=base.facility_assignment,
        osrm_cache=base.osrm_cache,
    )
    validate_experiment_config(resolved)
    return resolved


def default_experiment_config() -> ExperimentConfig:
    return ExperimentConfig()


def _pick(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value




def _normalize_scenario_value(value: Any) -> str | list[str]:
    """Normalize scalar/list demand scenarios while preserving semantics."""

    if isinstance(value, list):
        return [str(item).strip().lower() for item in value]
    return str(value).strip().lower()


def _normalize_instance_size_value(value: Any) -> int | list[int]:
    """Normalize scalar/list instance sizes while preserving semantics."""

    if isinstance(value, bool):
        raise ValueError(
            "instance_size must be an integer or a list of integers."
        )
    if isinstance(value, list):
        normalized = []
        for size in value:
            if isinstance(size, bool):
                raise ValueError(
                    "Every instance_size value must be an integer."
                )
            try:
                normalized.append(int(size))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Every instance_size value must be an integer."
                ) from exc
        return normalized
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "instance_size must be an integer or a list of integers."
        ) from exc

def _normalize_seed_value(
    value: Any,
    *,
    field_name: str,
) -> int | list[int] | None:
    """Normalize a JSON seed value while preserving scalar/list semantics."""

    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError(
            f"{field_name} must be an integer, a list of integers, or null."
        )

    if isinstance(value, list):
        normalized = []
        for seed in value:
            if isinstance(seed, bool):
                raise ValueError(
                    f"Every value in {field_name} must be an integer."
                )
            try:
                normalized.append(int(seed))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Every value in {field_name} must be an integer."
                ) from exc
        return normalized

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be an integer, a list of integers, or null."
        ) from exc

def _normalize_zones(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("zones must be null or a JSON array of names.")
    zones = [str(item).strip() for item in value if str(item).strip()]
    return zones or None
