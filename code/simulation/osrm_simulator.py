# Variant of osmnx_simulator.py that replaces OSMnx/NetworkX shortest-path
# computation with calls to a running OSRM server (http://project-osrm.org).
#
# Prerequisites: an OSRM instance must be reachable at OSRM_HOST, serving the
# /table service for OSRM_PROFILE. Typical local setup with Docker:
#
#   osrm-extract -p /opt/car.lua /data/spain-latest.osm.pbf
#   osrm-contract /data/spain-latest.osrm
#   docker run -t -i -p 5000:5000 -v "${PWD}:/data" osrm/osrm-backend \
#       osrm-routed --algorithm mld /data/spain-latest.osrm
#
# Unlike osmnx_simulator.py, there is no in-process graph to download or
# snap points to: OSRM snaps coordinates to the network internally on every
# /table or /route request.

from code.common.paths import DATA_DIR, RESULTS_DIR
from code.common.cost_utils import load_cost_parameters
from code.common.constants import (
    CITIES,
    OSRM_PORTS,
)

import argparse
from code.routing.config import RoutingAlgorithmConfig
from code.simulation.runner import simulate_city
from code.simulation.traffic import load_traffic_profile

from code.routing.osrm_client import (
    check_osrm_server,
    get_osrm_host,
)

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the OSRM-based logistics simulator."
    )

    parser.add_argument(
        "--city",
        choices=CITIES + ["all"],
        default="madrid",
        help="City to simulate ('all' runs every city)",
    )
    
    parser.add_argument(
        "--zones",
        nargs="+",
        default=None,
        help=(
            "Names of zones to simulate"
            "If it is omitted, all the zones of the file are processed"
        ),
    )

    parser.add_argument(
        "--demand-scenario",
        choices=("low", "medium", "high"),
        default="medium",
        help="Demand scenario to load from results/<city>/demand.",
    )

    parser.add_argument(
        "--instance-size",
        type=int,
        default=100,
        help="Number encoded in demand_<scenario>_<size>.csv.",
    )

    parser.add_argument(
        "--profile",
        choices=tuple(OSRM_PORTS["madrid"]),
        default="driving",
        help=(
            "OSRM profile used for logistics-center trunk routing. "
            "Default: driving."
        ),
    )
    
    parser.add_argument(
        "--routing-algorithm",
        choices=["cws", "ils"],
        default="cws",
        help=(
            "Routing algorithm used to construct capacity-aware routes. "
            "Default: cws."
        ),
    )
    
    parser.add_argument(
        "--ils-max-iterations",
        type=int,
        default=100,
        help="Maximum number of ILS iterations. Default: 100.",
    )

    parser.add_argument(
        "--ils-max-no-improvement",
        type=int,
        default=20,
        help=(
            "Stop ILS after this number of iterations without improvement. "
            "Default: 20."
        ),
    )

    parser.add_argument(
        "--ils-perturbation-moves",
        type=int,
        default=2,
        help="Number of perturbation moves per ILS iteration. Default: 2.",
    )

    parser.add_argument(
        "--ils-random-seed",
        type=int,
        default=42,
        help="Random seed used by ILS. Default: 42.",
    )
    
    parser.add_argument(
        "--traffic-profile",
        default="baseline",
        help=(
            "Traffic profile loaded from data/traffic_profiles.csv. "
            "Default: baseline."
        ),
    )

    parser.add_argument(
        "--traffic-multiplier",
        type=float,
        default=None,
        help=(
            "Optional multiplier overriding the selected CSV traffic profile. "
            "Useful for sensitivity tests."
        ),
    )

    return parser.parse_args()

      
if __name__ == "__main__":
    args = parse_arguments()

    routing_config = RoutingAlgorithmConfig(
        algorithm=args.routing_algorithm,
        ils_max_iterations=args.ils_max_iterations,
        ils_max_iterations_without_improvement=args.ils_max_no_improvement,
        ils_perturbation_moves=args.ils_perturbation_moves,
        ils_random_seed=args.ils_random_seed,
    )

    if args.city == "all" and args.zones is not None:

        raise SystemExit(
            "Error: --zones cannot be used together with --city all."
        )

    active_profile = args.profile
    active_zones = args.zones

    cities = CITIES if args.city == "all" else [args.city]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cost_parameters = load_cost_parameters(
        DATA_DIR / "cost_parameters.csv"
    )

    for active_city in cities:
        active_host = get_osrm_host(active_city, active_profile)
        traffic_profile = load_traffic_profile(
            csv_path=DATA_DIR / "traffic_profiles.csv",
            profile_name=args.traffic_profile,
            city=active_city,
            multiplier_override=args.traffic_multiplier,
        )

        print("\n" + "=" * 60)
        print("OSRM-BASED LOGISTICS SIMULATION")
        print("=" * 60)
        print(f"City: {active_city}")
        print(f"Simulation zones: {active_zones}")
        print(f"OSRM host: {active_host}")
        print(f"OSRM profile: {active_profile}")
        print(f"Routing algorithm: {routing_config.algorithm.upper()}")
        print(
            "Traffic profile: "
            f"{traffic_profile.name} "
            f"(x{traffic_profile.duration_multiplier:.3f}, "
            f"source={traffic_profile.source})"
        )
        print("=" * 60)

        check_osrm_server(
            city=active_city,
            host=active_host,
            profile=active_profile,
        )

        print("Loading data...")

        results_df, model_detail_frames = simulate_city(
            city=active_city,
            demand_scenario=args.demand_scenario,
            instance_size=args.instance_size,
            active_zones=active_zones,
            osrm_host=active_host,
            osrm_profile=active_profile,
            routing_config=routing_config,
            traffic_profile=traffic_profile,
            cost_parameters=cost_parameters,
        )

        results_df["routing_algorithm"] = routing_config.algorithm
        results_df["traffic_profile"] = traffic_profile.name
        results_df["traffic_duration_multiplier"] = (
            traffic_profile.duration_multiplier
        )
        results_df["traffic_source"] = traffic_profile.source

        model_detail_frames = {
            model_code: detail_df.assign(
                routing_algorithm=routing_config.algorithm,
                selected_traffic_profile=traffic_profile.name,
            )
            for model_code, detail_df in model_detail_frames.items()
        }

        zone_suffix = ""

        if active_zones:
            normalized_zones = "_".join(
                str(zone).strip().replace(" ", "_")
                for zone in active_zones
            )
            zone_suffix = f"_{normalized_zones}"

        output_filename = (
            f"resultados_osrm_{active_city}{zone_suffix}_"
            f"{args.demand_scenario}_{args.instance_size}_"
            f"{routing_config.algorithm}_{traffic_profile.name}.csv"
        )

        output_path = RESULTS_DIR / output_filename

        results_df.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        detail_output_folder = (
            RESULTS_DIR
            / active_city
            / "simulation_details"
            / (
                f"{args.demand_scenario}_{args.instance_size}_"
                f"{routing_config.algorithm}_{traffic_profile.name}"
            )
        )
        detail_output_folder.mkdir(parents=True, exist_ok=True)

        for model_code, detail_df in model_detail_frames.items():
            detail_path = detail_output_folder / f"{model_code.lower()}_routes.csv"
            detail_df.to_csv(
                detail_path,
                index=False,
                encoding="utf-8-sig",
            )
            print(f"{model_code} route details saved to: {detail_path.resolve()}")

        print(f"\nResults saved to: {output_path.resolve()}")