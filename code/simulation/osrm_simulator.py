# Variant of osmnx_simulator.py that replaces OSMnx/NetworkX shortest-path
# computation with calls to a running OSRM server.

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from code.common.constants import CITIES, OSRM_PORTS
from code.common.cost_utils import load_cost_parameters
from code.common.paths import DATA_DIR, RESULTS_DIR
from code.routing.config import RoutingAlgorithmConfig
from code.routing.osrm_client import check_osrm_server, get_osrm_host
from code.simulation.experiment_config import (
    ExperimentConfig,
    default_experiment_config,
    load_experiment_config,
    resolve_experiment_config,
)
from code.simulation.experiment_output import (
    build_metadata,
    create_experiment_directory,
    save_json,
)
from code.simulation.facility_filter import FacilityFilterSettings
from code.simulation.runner import simulate_city
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

    routing_config = RoutingAlgorithmConfig(
        algorithm=config.routing.algorithm,
        cws_allow_route_reversal=config.routing.cws_allow_route_reversal,
        ils_max_iterations=config.routing.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            config.routing.ils_max_no_improvement
        ),
        ils_perturbation_moves=(
            config.routing.ils_perturbation_moves
        ),
        ils_random_seed=config.routing.ils_random_seed,
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

        results_df, model_detail_frames = simulate_city(
            city=active_city,
            demand_scenario=config.demand_scenario,
            instance_size=config.instance_size,
            active_zones=config.zones,
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
        )

        results_df["experiment_id"] = experiment_id
        results_df["routing_algorithm"] = routing_config.algorithm
        results_df["cws_allow_route_reversal"] = routing_config.cws_allow_route_reversal
        results_df["traffic_profile"] = traffic_profile.name
        results_df["traffic_duration_multiplier"] = (
            traffic_profile.duration_multiplier
        )
        results_df["traffic_source"] = traffic_profile.source
        results_df["time_traffic_profile"] = (
            time_traffic_provider.profile_name
        )
        results_df["simulation_date"] = shift_start.date().isoformat()
        results_df["simulation_day_of_week"] = shift_start.strftime("%A").lower()
        results_df["traffic_city_file"] = time_traffic_provider.source_path.name
        results_df["shift_start_time"] = shift_start.time().isoformat(
            timespec="minutes"
        )
        results_df["shift_end_time"] = shift_end.time().isoformat(
            timespec="minutes"
        )
        results_df.to_csv(
            experiment_folder / "summary.csv",
            index=False,
            encoding="utf-8-sig",
        )

        if config.output.save_route_details:
            routes_folder = experiment_folder / "routes"
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
                detail_df.to_csv(
                    detail_path,
                    index=False,
                    encoding="utf-8-sig",
                )
                print(
                    f"{model_code} route details saved to: "
                    f"{detail_path.resolve()}"
                )

        finished_at = datetime.now()
        if config.output.save_metadata:
            save_json(
                metadata_path,
                build_metadata(
                    experiment_id=experiment_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="completed",
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
                ),
            )
        raise


if __name__ == "__main__":
    main()
