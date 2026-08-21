# Variant of osmnx_simulator.py that replaces OSMnx/NetworkX shortest-path
# computation with calls to a running OSRM server.

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from code.common.constants import CITIES, OSRM_PORTS
from code.common.cost_utils import load_cost_parameters
from code.common.paths import DATA_DIR, RESULTS_DIR
from code.routing.config import RoutingAlgorithmConfig
from code.routing.osrm_client import (
    check_osrm_server,
    configure_osrm_matrix_cache,
    get_osrm_host,
)
from code.simulation.experiment_config import (
    ExperimentConfig,
    default_experiment_config,
    load_experiment_config,
    resolve_experiment_config,
)
from code.simulation.experiment_output import (
    build_metadata,
    create_experiment_directory,
    hash_payload,
    save_json,
    slugify,
)
from code.simulation.data_loader import resolve_demand_instance_path
from code.simulation.facility_filter import FacilityFilterSettings
from code.simulation.exporters import (
    build_facility_summary_frame,
    build_route_stops_frame,
    build_routing_plan_metrics_frame,
    strip_route_export_columns,
)
from code.simulation.runner import simulate_city
from code.simulation.summary_report import build_summary_export
from code.simulation.traffic import load_traffic_profile
from code.traffic.provider import TimeTrafficProvider
from code.traffic.schedule import build_shift


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the OSRM-based logistics simulator."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to an experiment JSON configuration file.",
    )
    parser.add_argument(
        "--city",
        choices=CITIES + ["all"],
        default=None,
        help="CLI override for the city configured in the JSON file.",
    )
    parser.add_argument(
        "--zones",
        nargs="+",
        default=None,
        help="CLI override for the simulation zones.",
    )
    parser.add_argument(
        "--demand-scenario",
        choices=("low", "medium", "high"),
        default=None,
    )
    parser.add_argument("--instance-size", type=int, default=None)
    parser.add_argument(
        "--demand-seed",
        type=int,
        default=None,
        help="Select demand_<scenario>_<size>_seed_<seed>.csv.",
    )
    parser.add_argument(
        "--demand-instance-id",
        default=None,
        help="Explicit demand CSV filename or stem inside results/<city>/demand.",
    )
    parser.add_argument(
        "--profile",
        choices=tuple(OSRM_PORTS["madrid"]),
        default=None,
        help="OSRM profile override.",
    )
    parser.add_argument(
        "--routing-algorithm",
        choices=("cws", "ils"),
        default=None,
    )
    parser.add_argument(
        "--cws-allow-route-reversal",
        dest="cws_allow_route_reversal",
        action="store_true",
        default=None,
        help="Allow CWS to reverse partial routes before endpoint merges.",
    )
    parser.add_argument("--ils-max-iterations", type=int, default=None)
    parser.add_argument(
        "--ils-max-no-improvement",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--ils-perturbation-moves",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--save-route-geometry",
        dest="save_route_geometry",
        action="store_true",
        default=None,
        help="Guardar la geometría OSRM de cada ruta como WKT en los CSV.",
    )
    parser.add_argument(
        "--ils-biased-cws-alpha-min",
        dest="ils_biased_cws_alpha_min",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--ils-biased-cws-alpha-max",
        dest="ils_biased_cws_alpha_max",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--ils-restricted-relocate",
        dest="ils_restricted_relocate",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-ils-restricted-relocate",
        dest="ils_restricted_relocate",
        action="store_false",
    )
    parser.add_argument(
        "--ils-relocate-candidate-fraction",
        dest="ils_relocate_candidate_fraction",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--ils-relocate-neighbor-routes",
        dest="ils_relocate_neighbor_routes",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--ils-relocate-max-insertions",
        dest="ils_relocate_max_insertions",
        type=int,
        default=None,
    )
    parser.add_argument("--ils-random-seed", type=int, default=None)
    parser.add_argument("--traffic-profile", default=None)
    parser.add_argument("--traffic-multiplier", type=float, default=None)
    parser.add_argument("--simulation-date", default=None)
    parser.add_argument("--shift-start", default=None)
    parser.add_argument("--shift-duration-min", type=float, default=None)
    parser.add_argument("--time-traffic-profile", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    base_config = (
        load_experiment_config(args.config)
        if args.config is not None
        else default_experiment_config()
    )
    config = resolve_experiment_config(
        base=base_config,
        overrides=vars(args),
    )

    expanded_configs = _expand_seed_configs(config)

    if len(expanded_configs) > 1:
        print(
            f"Running {len(expanded_configs)} independent experiment "
            "replicates:"
        )

    total = len(expanded_configs)
    for index, seed_config in enumerate(expanded_configs, start=1):
        demand_seed = seed_config.demand_seed
        ils_seed = seed_config.routing.ils_random_seed

        if total > 1:
            print(
                f"\n===== Replicate {index}/{total} | "
                f"demand_seed={demand_seed} | "
                f"ils_random_seed={ils_seed} ====="
            )

        _run_resolved_config(seed_config)


def _expand_seed_configs(
    config: ExperimentConfig,
) -> list[ExperimentConfig]:
    """
    Expand scalar/list demand and ILS seeds into independent configurations.

    Rules:
    - scalar demand + scalar ILS: one run
    - scalar demand + ILS list: fixed demand, vary ILS
    - demand list + scalar ILS: vary demand, fixed ILS
    - demand list + ILS list: zip 1:1 by position
    """

    demand_value = config.demand_seed
    ils_value = config.routing.ils_random_seed

    demand_is_list = isinstance(demand_value, list)
    ils_is_list = isinstance(ils_value, list)

    if ils_is_list and config.routing.algorithm != "ils":
        raise SystemExit(
            "Error: a list of ils_random_seed values can only be used "
            "when routing.algorithm is 'ils'."
        )

    if demand_is_list and ils_is_list:
        if len(demand_value) != len(ils_value):
            # validate_experiment_config should catch this first, but keep
            # execution safe for programmatically-created configs too.
            raise SystemExit(
                "Error: demand_seed and ils_random_seed lists must have "
                "the same length for 1:1 pairing."
            )
        seed_pairs = list(zip(demand_value, ils_value))

    elif demand_is_list:
        seed_pairs = [
            (demand_seed, ils_value)
            for demand_seed in demand_value
        ]

    elif ils_is_list:
        seed_pairs = [
            (demand_value, ils_seed)
            for ils_seed in ils_value
        ]

    else:
        return [config]

    return [
        replace(
            config,
            demand_seed=demand_seed,
            routing=replace(
                config.routing,
                ils_random_seed=ils_seed,
            ),
        )
        for demand_seed, ils_seed in seed_pairs
    ]


def _run_resolved_config(config: ExperimentConfig) -> None:
    """Run one experiment with one concrete ILS seed."""

    configure_osrm_matrix_cache(
        enabled=config.osrm_cache.enabled,
        directory=config.osrm_cache.directory,
    )

    routing_config = RoutingAlgorithmConfig(
        algorithm=config.routing.algorithm,
        cws_allow_route_reversal=config.routing.cws_allow_route_reversal,
        ils_max_iterations=config.routing.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            config.routing.ils_max_no_improvement
        ),
        ils_destruction_percentage_step=(
            config.routing.ils_destruction_percentage_step
        ),
        ils_max_destruction_percentage=(
            config.routing.ils_max_destruction_percentage
        ),
        ils_max_full_destruction_attempts=(
            config.routing.ils_max_full_destruction_attempts
        ),
        ils_biased_cws_alpha_min=config.routing.ils_biased_cws_alpha_min,
        ils_biased_cws_alpha_max=config.routing.ils_biased_cws_alpha_max,
        ils_biased_cws_sampling_batch_size=(
            config.routing.ils_biased_cws_sampling_batch_size
        ),
        ils_restricted_relocate=config.routing.ils_restricted_relocate,
        ils_relocate_candidate_fraction=config.routing.ils_relocate_candidate_fraction,
        ils_relocate_neighbor_routes=config.routing.ils_relocate_neighbor_routes,
        ils_relocate_max_insertions=config.routing.ils_relocate_max_insertions,
        ils_random_seed=config.routing.ils_random_seed,
        last_service_deadline_enabled=(
            config.routing.last_service_deadline_enabled
        ),
        last_service_margin_min=config.routing.last_service_margin_min,
        last_meter_access_enabled=config.last_meter_access.enabled,
        last_meter_walking_speed_m_s=(
            config.last_meter_access.walking_speed_m_s
        ),
        last_meter_round_trip=config.last_meter_access.round_trip,
        last_meter_models=(
            tuple(config.last_meter_access.models)
            if config.last_meter_access.models
            else None
        ),
    )

    try:
        simulation_date = datetime.strptime(
            config.traffic.simulation_date,
            "%Y-%m-%d",
        ).date()
        shift_start_clock = datetime.strptime(
            config.traffic.shift_start,
            "%H:%M",
        ).time()
    except ValueError as exc:
        raise SystemExit(
            "Error: simulation_date must use YYYY-MM-DD and "
            "shift_start must use HH:MM."
        ) from exc

    shift_start, shift_end = build_shift(
        simulation_date=simulation_date,
        start_time=shift_start_clock,
        duration_min=config.traffic.shift_duration_min,
    )
    cost_parameters = load_cost_parameters(
        DATA_DIR / "cost_parameters.csv"
    )
    cities = CITIES if config.city == "all" else [config.city]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for active_city in cities:
        time_traffic_provider = TimeTrafficProvider.from_city_csv(
            traffic_folder=DATA_DIR / "traffic",
            city=active_city,
            profile_name=config.traffic.time_profile,
        )
        _run_city_experiment(
            config=config,
            active_city=active_city,
            routing_config=routing_config,
            time_traffic_provider=time_traffic_provider,
            shift_start=shift_start,
            shift_end=shift_end,
            cost_parameters=cost_parameters,
        )



SUMMARY_AUDIT_OUTPUTS = {
    "routing_integrity_summary",
    "route_customer_summary",
    "unroutable_customers",
    "osrm_snapping_audit",
}


def _select_audit_outputs(
    audit_details: dict[str, pd.DataFrame],
    *,
    audit_detail: str,
    performance_profile: str,
) -> dict[str, pd.DataFrame]:
    """Select persisted audit/profiling outputs by verbosity."""

    selected: dict[str, pd.DataFrame] = {}

    for audit_name, audit_df in audit_details.items():
        if audit_name == "performance_profile":
            if performance_profile == "off":
                continue
            if (
                performance_profile == "basic"
                and "category" in audit_df.columns
            ):
                audit_df = audit_df.loc[
                    ~audit_df["category"].eq("routing_detail")
                ].copy()
            selected[audit_name] = audit_df
            continue

        if audit_detail == "full" or audit_name in SUMMARY_AUDIT_OUTPUTS:
            selected[audit_name] = audit_df

    return selected


def _run_city_experiment(
    *,
    config: ExperimentConfig,
    active_city: str,
    routing_config: RoutingAlgorithmConfig,
    time_traffic_provider: TimeTrafficProvider,
    shift_start: datetime,
    shift_end: datetime,
    cost_parameters: dict[str, float],
) -> None:
    started_at = datetime.now()
    experiment_id, experiment_folder = create_experiment_directory(
        results_root=RESULTS_DIR,
        config=config,
        city=active_city,
        started_at=started_at,
    )
    metadata_path = experiment_folder / "metadata.json"

    provenance_config = config.to_dict()
    provenance_config["resolved_city"] = active_city
    config_hash = hash_payload(provenance_config)
    demand_file = resolve_demand_instance_path(
        active_city,
        config.demand_scenario,
        config.instance_size,
        demand_seed=config.demand_seed,
        demand_instance_id=config.demand_instance_id,
    )

    if config.output.save_configuration:
        resolved_config = config.to_dict()
        resolved_config["resolved_city"] = active_city
        resolved_config["experiment_id"] = experiment_id
        save_json(experiment_folder / "config.json", resolved_config)

    if config.output.save_metadata:
        save_json(
            metadata_path,
            build_metadata(
                experiment_id=experiment_id,
                started_at=started_at,
                finished_at=None,
                status="running",
                config_hash=config_hash,
                demand_file=demand_file,
            ),
        )

    try:
        active_host = get_osrm_host(
            active_city,
            config.osrm_profile,
        )
        traffic_profile = load_traffic_profile(
            csv_path=DATA_DIR / "traffic_profiles.csv",
            profile_name=config.traffic.static_profile,
            city=active_city,
            multiplier_override=(
                config.traffic.static_multiplier_override
            ),
        )

        print("\n" + "=" * 60)
        print("OSRM-BASED LOGISTICS SIMULATION")
        print("=" * 60)
        print(f"Experiment: {experiment_id}")
        print(f"City: {active_city}")
        print(f"Simulation zones: {config.zones}")
        print(f"OSRM host: {active_host}")
        print(f"OSRM profile: {config.osrm_profile}")
        print(
            "OSRM matrix cache: "
            f"{'ENABLED' if config.osrm_cache.enabled else 'DISABLED'}"
            + (
                f" | {config.osrm_cache.directory}"
                if config.osrm_cache.enabled
                else ""
            )
        )
        print(f"Routing algorithm: {routing_config.algorithm.upper()}")
        print(
            "Traffic profile used during route construction: "
            f"{traffic_profile.name} "
            f"(x{traffic_profile.duration_multiplier:.3f}, "
            f"source={traffic_profile.source})"
        )
        print(
            "Time-dependent evaluation: "
            f"{time_traffic_provider.profile_name} | "
            f"{shift_start.isoformat(timespec='minutes')} -> "
            f"{shift_end.isoformat(timespec='minutes')}"
        )
        print(f"Output folder: {experiment_folder.resolve()}")
        print("=" * 60)

        check_osrm_server(
            city=active_city,
            host=active_host,
            profile=config.osrm_profile,
        )

        neighborhood_status_rows = []
        neighborhoods_folder = experiment_folder / "neighborhoods"
        neighborhoods_folder.mkdir(parents=True, exist_ok=True)

        def _write_neighborhood_status() -> None:
            pd.DataFrame(neighborhood_status_rows).to_csv(
                experiment_folder / "neighborhood_status.csv",
                index=False,
                encoding="utf-8-sig",
            )

        def _enrich_summary(
            frame: pd.DataFrame,
            model_details: dict[str, pd.DataFrame],
        ) -> pd.DataFrame:
            return build_summary_export(
                frame,
                model_details=model_details,
                summary_detail=config.output.summary_detail,
                experiment_id=experiment_id,
                routing_algorithm=routing_config.algorithm,
                routing_seed=routing_config.ils_random_seed,
                cws_allow_route_reversal=(
                    routing_config.cws_allow_route_reversal
                ),
                traffic_profile_name=traffic_profile.name,
                traffic_duration_multiplier=(
                    traffic_profile.duration_multiplier
                ),
                traffic_source=traffic_profile.source,
                time_traffic_profile=time_traffic_provider.profile_name,
                simulation_date=shift_start.date().isoformat(),
                simulation_day_of_week=(
                    shift_start.strftime("%A").lower()
                ),
                traffic_city_file=(
                    time_traffic_provider.source_path.name
                ),
                shift_start=shift_start,
                shift_end=shift_end,
                last_service_deadline_enabled=(
                    routing_config.last_service_deadline_enabled
                ),
                last_service_margin_min=(
                    routing_config.last_service_margin_min
                ),
            )

        def _save_completed_neighborhood(
            *,
            neighborhood_name,
            zone_type,
            results,
            model_details,
            audit_details,
            runtime_seconds,
        ) -> None:
            finished = datetime.now()
            started = finished - timedelta(seconds=float(runtime_seconds))
            zone_slug = slugify(str(neighborhood_name))
            zone_folder = neighborhoods_folder / zone_slug
            routes_folder = zone_folder / "routes"
            routing_folder = zone_folder / "routing"
            facilities_folder = zone_folder / "facilities"
            audit_folder = zone_folder / "audit"

            routes_folder.mkdir(parents=True, exist_ok=True)
            routing_folder.mkdir(parents=True, exist_ok=True)
            facilities_folder.mkdir(parents=True, exist_ok=True)
            audit_folder.mkdir(parents=True, exist_ok=True)

            zone_summary = _enrich_summary(results, model_details)
            zone_summary.to_csv(
                zone_folder / "summary.csv",
                index=False,
                encoding="utf-8-sig",
            )

            if config.output.save_route_details:
                if config.output.save_route_stops:
                    route_stops = build_route_stops_frame(model_details)
                    if not route_stops.empty:
                        route_stops.assign(
                            experiment_id=experiment_id,
                        ).to_csv(
                            routes_folder / "route_stops.csv",
                            index=False,
                            encoding="utf-8-sig",
                        )

                facility_summary = build_facility_summary_frame(
                    model_details
                )
                if not facility_summary.empty:
                    facility_summary.assign(
                        experiment_id=experiment_id,
                    ).to_csv(
                        facilities_folder / "facility_summary.csv",
                        index=False,
                        encoding="utf-8-sig",
                    )

                routing_metrics = build_routing_plan_metrics_frame(
                    model_details
                )
                if not routing_metrics.empty:
                    routing_metrics.assign(
                        experiment_id=experiment_id,
                        selected_traffic_profile=traffic_profile.name,
                    ).to_csv(
                        routing_folder / "routing_plan_metrics.csv",
                        index=False,
                        encoding="utf-8-sig",
                    )

                for model_code, detail_df in model_details.items():
                    enriched = detail_df.assign(
                        experiment_id=experiment_id,
                        routing_algorithm=routing_config.algorithm,
                        cws_allow_route_reversal=(
                            routing_config.cws_allow_route_reversal
                        ),
                        selected_traffic_profile=traffic_profile.name,
                    )
                    strip_route_export_columns(enriched).to_csv(
                        routes_folder / f"{model_code.lower()}_routes.csv",
                        index=False,
                        encoding="utf-8-sig",
                    )

            selected_audits = _select_audit_outputs(
                audit_details,
                audit_detail=config.output.audit_detail,
                performance_profile=config.output.performance_profile,
            )
            for audit_name, audit_df in selected_audits.items():
                enriched_audit = audit_df.assign(
                    experiment_id=experiment_id,
                    routing_algorithm=routing_config.algorithm,
                )
                enriched_audit.to_csv(
                    audit_folder / f"{audit_name}.csv",
                    index=False,
                    encoding="utf-8-sig",
                )

            if config.output.save_metadata:
                save_json(
                    zone_folder / "metadata.json",
                    build_metadata(
                        experiment_id=f"{experiment_id}:{zone_slug}",
                        started_at=started,
                        finished_at=finished,
                        status="completed",
                        config_hash=config_hash,
                        demand_file=demand_file,
                    ),
                )

            neighborhood_status_rows.append(
                {
                    "neighborhood": neighborhood_name,
                    "zone_type": zone_type,
                    "status": "completed",
                    "runtime_seconds": float(runtime_seconds),
                    "error": "",
                    "folder": str(zone_folder.relative_to(experiment_folder)),
                }
            )
            _write_neighborhood_status()

            print(
                f"Incremental neighborhood output saved to: "
                f"{zone_folder.resolve()}"
            )

        def _save_failed_neighborhood(
            *,
            neighborhood_name,
            zone_type,
            error,
            runtime_seconds,
        ) -> None:
            finished = datetime.now()
            started = finished - timedelta(seconds=float(runtime_seconds))
            zone_slug = slugify(str(neighborhood_name))
            zone_folder = neighborhoods_folder / zone_slug
            zone_folder.mkdir(parents=True, exist_ok=True)

            if config.output.save_metadata:
                save_json(
                    zone_folder / "metadata.json",
                    build_metadata(
                        experiment_id=f"{experiment_id}:{zone_slug}",
                        started_at=started,
                        finished_at=finished,
                        status="failed",
                        error=f"{type(error).__name__}: {error}",
                        config_hash=config_hash,
                        demand_file=demand_file,
                    ),
                )

            neighborhood_status_rows.append(
                {
                    "neighborhood": neighborhood_name,
                    "zone_type": zone_type,
                    "status": "failed",
                    "runtime_seconds": float(runtime_seconds),
                    "error": f"{type(error).__name__}: {error}",
                    "folder": str(zone_folder.relative_to(experiment_folder)),
                }
            )
            _write_neighborhood_status()

            print(
                f"Failure metadata saved for neighborhood "
                f"{neighborhood_name!r}; continuing with the next zone."
            )

        results_df, model_detail_frames, audit_frames = simulate_city(
            city=active_city,
            demand_scenario=config.demand_scenario,
            instance_size=config.instance_size,
            active_zones=config.zones,
            demand_seed=config.demand_seed,
            demand_instance_id=config.demand_instance_id,
            osrm_host=active_host,
            osrm_profile=config.osrm_profile,
            routing_config=routing_config,
            traffic_profile=traffic_profile,
            time_traffic_provider=time_traffic_provider,
            shift_start=shift_start,
            shift_end=shift_end,
            cost_parameters=cost_parameters,
            add_geometry=config.output.save_route_geometry,
            facility_filter_settings=FacilityFilterSettings(
                enabled=config.facility_filter.enabled,
                initial_buffer_m=config.facility_filter.initial_buffer_m,
                buffer_increment_m=config.facility_filter.buffer_increment_m,
                maximum_buffer_m=config.facility_filter.maximum_buffer_m,
                minimum_candidates=config.facility_filter.minimum_candidates,
            ),
            pudo_capacity_mode=config.facility_assignment.pudo_capacity_mode,
            microhub_capacity_mode=(
                config.facility_assignment.microhub_capacity_mode
            ),
            show_progress=config.output.show_progress,
            zone_result_callback=_save_completed_neighborhood,
            zone_failure_callback=_save_failed_neighborhood,
            continue_on_zone_error=True,
        )

        results_df = _enrich_summary(
            results_df,
            model_detail_frames,
        )
        results_df.to_csv(
            experiment_folder / "summary.csv",
            index=False,
            encoding="utf-8-sig",
        )

        if config.output.save_route_details:
            routes_folder = experiment_folder / "routes"
            routing_folder = experiment_folder / "routing"
            facilities_folder = experiment_folder / "facilities"

            if config.output.save_route_stops:
                route_stops = build_route_stops_frame(
                    model_detail_frames
                )
                if not route_stops.empty:
                    route_stops_path = (
                        routes_folder / "route_stops.csv"
                    )
                    route_stops.assign(
                        experiment_id=experiment_id,
                    ).to_csv(
                        route_stops_path,
                        index=False,
                        encoding="utf-8-sig",
                    )
                    print(
                        "Route stop details saved to: "
                        f"{route_stops_path.resolve()}"
                    )

            facility_summary = build_facility_summary_frame(
                model_detail_frames
            )
            if not facility_summary.empty:
                facility_summary_path = (
                    facilities_folder / "facility_summary.csv"
                )
                facility_summary.assign(
                    experiment_id=experiment_id,
                ).to_csv(
                    facility_summary_path,
                    index=False,
                    encoding="utf-8-sig",
                )
                print(
                    "Facility summary saved to: "
                    f"{facility_summary_path.resolve()}"
                )

            routing_metrics = build_routing_plan_metrics_frame(
                model_detail_frames
            )
            if not routing_metrics.empty:
                routing_metrics_path = (
                    routing_folder / "routing_plan_metrics.csv"
                )
                routing_metrics.assign(
                    experiment_id=experiment_id,
                    selected_traffic_profile=traffic_profile.name,
                ).to_csv(
                    routing_metrics_path,
                    index=False,
                    encoding="utf-8-sig",
                )
                print(
                    "Routing plan metrics saved to: "
                    f"{routing_metrics_path.resolve()}"
                )

            for model_code, detail_df in model_detail_frames.items():
                detail_df = detail_df.assign(
                    experiment_id=experiment_id,
                    routing_algorithm=routing_config.algorithm,
                    cws_allow_route_reversal=routing_config.cws_allow_route_reversal,
                    selected_traffic_profile=traffic_profile.name,
                )
                detail_path = (
                    routes_folder / f"{model_code.lower()}_routes.csv"
                )
                strip_route_export_columns(detail_df).to_csv(
                    detail_path,
                    index=False,
                    encoding="utf-8-sig",
                )
                print(
                    f"{model_code} route details saved to: "
                    f"{detail_path.resolve()}"
                )

        if config.output.save_route_stops:
            completed_zones = [
                row
                for row in neighborhood_status_rows
                if row.get("status") == "completed"
            ]
            if len(completed_zones) == 1:
                nested_route_stops = (
                    experiment_folder
                    / completed_zones[0]["folder"]
                    / "routes"
                    / "route_stops.csv"
                )
                if nested_route_stops.exists():
                    nested_route_stops.unlink()
                    print(
                        "Removed duplicate single-zone route_stops.csv "
                        f"from: {nested_route_stops.parent.resolve()}"
                    )

        audit_folder = experiment_folder / "audit"
        audit_folder.mkdir(parents=True, exist_ok=True)

        selected_audits = _select_audit_outputs(
            audit_frames,
            audit_detail=config.output.audit_detail,
            performance_profile=config.output.performance_profile,
        )
        for audit_name, audit_df in selected_audits.items():
            audit_df = audit_df.assign(
                experiment_id=experiment_id,
                routing_algorithm=routing_config.algorithm,
            )

            audit_path = audit_folder / f"{audit_name}.csv"
            audit_df.to_csv(
                audit_path,
                index=False,
                encoding="utf-8-sig",
            )

            print(
                f"Audit output saved to: "
                f"{audit_path.resolve()}"
            )

        finished_at = datetime.now()
        completed_zone_count = sum(
            row["status"] == "completed"
            for row in neighborhood_status_rows
        )
        failed_zone_count = sum(
            row["status"] == "failed"
            for row in neighborhood_status_rows
        )

        if failed_zone_count == 0:
            experiment_status = "completed"
        elif completed_zone_count > 0:
            experiment_status = "partial"
        else:
            experiment_status = "failed"

        if config.output.save_metadata:
            save_json(
                metadata_path,
                build_metadata(
                    experiment_id=experiment_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    status=experiment_status,
                    error=(
                        f"{failed_zone_count} neighborhood(s) failed."
                        if failed_zone_count
                        else None
                    ),
                    config_hash=config_hash,
                    demand_file=demand_file,
                ),
            )
        print(
            f"\nExperiment completed. Results saved to: "
            f"{experiment_folder.resolve()}"
        )
    except Exception as exc:
        finished_at = datetime.now()
        if config.output.save_metadata:
            save_json(
                metadata_path,
                build_metadata(
                    experiment_id=experiment_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    config_hash=config_hash,
                    demand_file=demand_file,
                ),
            )
        raise


if __name__ == "__main__":
    main()
    
