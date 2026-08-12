from datetime import datetime

import pandas as pd

from code.common.constants import (
    DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
)
from code.common.data_utils import get_parameters
from code.routing.config import RoutingAlgorithmConfig
from code.routing.osrm_router import (
    CapacityAwareOsrmRouter,
    calculate_facility_supply_route,
    select_logistics_center,
)
from code.routing.osrm_client import (
    get_osrm_cache_stats,
    reset_osrm_cache_stats,
)
from code.simulation.data_loader import load_city_data, load_classified_locations, load_demand_instance
from code.simulation.audit import audit_customer_routes, build_unroutable_customer_rows
from code.simulation.demand import calculate_demand_weighted_centroid
from code.simulation.exporters import build_route_detail_rows
from code.simulation.last_mile import (
    calculate_customer_collection_travel,
    calculate_microhub_last_mile,
    calculate_pudo_last_mile,
)
from code.simulation.models import (
    simulate_m1,
    simulate_m2,
    simulate_m3,
    simulate_m4,
    simulate_m5,
)
from code.simulation.operational_points import (
    OperationalPoint,
    select_operational_point,
)
from code.simulation.traffic import TrafficProfile
from code.traffic.provider import TimeTrafficProvider
from code.simulation.facility_filter import (
    FacilityFilterSettings,
    filter_facilities_for_zone,
)
from code.simulation.zones import (
    assign_customers_to_nearest_facility,
    build_facility_summary,
    filter_points_by_neighborhood,
    load_facility_candidates,
    select_zones,
    warn_zone_overlaps,
)
from time import perf_counter


def _performance_row(
    *,
    city: str,
    neighborhood_name: str,
    stage: str,
    seconds: float,
    category: str,
    model: str | None = None,
    detail: str | None = None,
) -> dict:
    return {
        "city": city,
        "neighborhood": neighborhood_name,
        "category": category,
        "model": model,
        "stage": stage,
        "seconds": float(seconds),
        "detail": detail,
    }


def simulate_neighborhood(
    city: str,
    neighborhood_name: str,
    demand_points: pd.DataFrame,
    demand_centroid: OperationalPoint,
    microhub_point: OperationalPoint,
    pudo_point: OperationalPoint,
    assigned_pudos: pd.DataFrame,
    assigned_microhubs: pd.DataFrame,
    centers: pd.DataFrame,
    parameters: dict,
    cost_parameters: dict[str, float],
    *,
    osrm_host: str,
    osrm_profile: str,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
    time_traffic_provider: TimeTrafficProvider,
    shift_start: datetime,
    shift_end: datetime,
    add_geometry: bool = False,
    show_progress: bool = False,
):
    """Run the five models using demand stops and classified facilities."""

    performance_rows = []

    def timed_stage(stage: str, category: str, model: str | None = None):
        class _Stage:
            def __enter__(self_inner):
                self_inner.started = perf_counter()
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                performance_rows.append(
                    _performance_row(
                        city=city,
                        neighborhood_name=neighborhood_name,
                        stage=stage,
                        seconds=perf_counter() - self_inner.started,
                        category=category,
                        model=model,
                        detail="failed" if exc_type is not None else "completed",
                    )
                )
        return _Stage()

    original_demand_points = demand_points.reset_index(drop=True).copy()
    customer_count = len(demand_points)
    package_count = int(demand_points["Demand"].sum())
    client_demands = demand_points["Demand"].to_numpy(dtype=float)

    if customer_count == 0 or package_count == 0:
        return (
            [],
            {model: [] for model in ("M1", "M2", "M3", "M4", "M5")},
            {
                "customer_route_audit": [],
                "route_customer_summary": [],
                "routing_integrity_summary": [],
                "unroutable_customers": [],
                "performance_profile": [],
            },
        )

    route_planner = CapacityAwareOsrmRouter(city)

    # M1/M2: direct routes from the selected warehouse to demand stops.
    direct_cc = select_logistics_center(
        centers,
        demand_centroid.latitude,
        demand_centroid.longitude,
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    direct_cc_lat = float(direct_cc["Latitude"])
    direct_cc_lon = float(direct_cc["Longitude"])

    with timed_stage("direct_routing_m1", "routing", "M1"):
        m1_plan = route_planner.build_capacity_plan(
            depot_latitude=direct_cc_lat,
            depot_longitude=direct_cc_lon,
            clients=demand_points,
            transport_mode="driving",
            vehicle_capacity=parameters["FURGONETA_CONV"]["capacidad"],
            client_demands=client_demands,
            route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
            routing_algorithm=routing_config.algorithm,
            cws_allow_route_reversal=routing_config.cws_allow_route_reversal,
            ils_max_iterations=routing_config.ils_max_iterations,
            ils_max_iterations_without_improvement=(
                routing_config.ils_max_iterations_without_improvement
            ),
            ils_destruction_percentage_step=routing_config.ils_destruction_percentage_step,
            ils_max_destruction_percentage=routing_config.ils_max_destruction_percentage,
            ils_biased_cws_alpha_min=routing_config.ils_biased_cws_alpha_min,
            ils_biased_cws_alpha_max=routing_config.ils_biased_cws_alpha_max,
            ils_restricted_relocate=routing_config.ils_restricted_relocate,
            ils_relocate_candidate_fraction=routing_config.ils_relocate_candidate_fraction,
            ils_relocate_neighbor_routes=routing_config.ils_relocate_neighbor_routes,
            ils_relocate_max_insertions=routing_config.ils_relocate_max_insertions,
            ils_random_seed=routing_config.ils_random_seed,
            traffic_profile=traffic_profile,
            time_traffic_provider=time_traffic_provider,
            traffic_zone=neighborhood_name,
            shift_start=shift_start,
            shift_end=shift_end,
            show_progress=show_progress,
            exclude_unroutable_clients=True,
        )

    # Apply the M1 driving-routability exclusion consistently to all models.
    # The route indices in m1_plan refer to this filtered customer order.
    if m1_plan.unroutable_client_positions:
        excluded_positions = set(m1_plan.unroutable_client_positions)
        keep_mask = pd.Series(
            [
                position not in excluded_positions
                for position in range(len(demand_points))
            ],
            index=demand_points.index,
        )

        demand_points = demand_points.loc[keep_mask].reset_index(drop=True)

        if len(assigned_pudos) == len(keep_mask):
            assigned_pudos = assigned_pudos.loc[keep_mask.to_numpy()].reset_index(drop=True)
        else:
            raise ValueError(
                "assigned_pudos no longer matches the demand-point ordering "
                "needed for unroutable-customer exclusion."
            )

        if len(assigned_microhubs) == len(keep_mask):
            assigned_microhubs = assigned_microhubs.loc[keep_mask.to_numpy()].reset_index(drop=True)
        else:
            raise ValueError(
                "assigned_microhubs no longer matches the demand-point ordering "
                "needed for unroutable-customer exclusion."
            )

        customer_count = len(demand_points)
        package_count = int(demand_points["Demand"].sum())
        client_demands = demand_points["Demand"].to_numpy(dtype=float)

        print(
            "Applied driving-routability exclusion consistently across M1-M5: "
            f"{m1_plan.unroutable_customer_count} customers / "
            f"{m1_plan.unroutable_package_count:g} packages excluded; "
            f"{customer_count} customers / {package_count} packages remain."
        )

    m1_capacity = float(parameters["FURGONETA_CONV"]["capacidad"])
    m2_capacity = float(parameters["FURGONETA_ELEC"]["capacidad"])

    direct_routing_reusable = abs(m1_capacity - m2_capacity) <= 1e-9

    print(
        "M2 routing reuse check: "
        f"M1 capacity={m1_capacity:g}, "
        f"M2 capacity={m2_capacity:g}."
    )

    if direct_routing_reusable:
        m2_plan = m1_plan
        print(
            "M2 reusing M1 routing plan: YES "
            "(identical direct-routing inputs and constraints)."
        )
        performance_rows.append(
            _performance_row(
                city=city,
                neighborhood_name=neighborhood_name,
                stage="direct_routing_m2",
                seconds=0.0,
                category="routing",
                model="M2",
                detail="reused_m1_plan",
            )
        )
    else:
        print(
            "M2 reusing M1 routing plan: NO "
            "(vehicle capacities differ; running independent M2 routing)."
        )

        with timed_stage("direct_routing_m2", "routing", "M2"):
            m2_plan = route_planner.build_capacity_plan(
                depot_latitude=direct_cc_lat,
                depot_longitude=direct_cc_lon,
                clients=demand_points,
                transport_mode="driving",
                vehicle_capacity=m2_capacity,
                client_demands=client_demands,
                route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
                routing_algorithm=routing_config.algorithm,
                cws_allow_route_reversal=routing_config.cws_allow_route_reversal,
                ils_max_iterations=routing_config.ils_max_iterations,
                ils_max_iterations_without_improvement=(
                    routing_config.ils_max_iterations_without_improvement
                ),
                ils_destruction_percentage_step=routing_config.ils_destruction_percentage_step,
                ils_max_destruction_percentage=routing_config.ils_max_destruction_percentage,
                ils_biased_cws_alpha_min=routing_config.ils_biased_cws_alpha_min,
                ils_biased_cws_alpha_max=routing_config.ils_biased_cws_alpha_max,
                ils_restricted_relocate=routing_config.ils_restricted_relocate,
                ils_relocate_candidate_fraction=routing_config.ils_relocate_candidate_fraction,
                ils_relocate_neighbor_routes=routing_config.ils_relocate_neighbor_routes,
                ils_relocate_max_insertions=routing_config.ils_relocate_max_insertions,
                ils_random_seed=routing_config.ils_random_seed,
                traffic_profile=traffic_profile,
                time_traffic_provider=time_traffic_provider,
                traffic_zone=neighborhood_name,
                shift_start=shift_start,
                shift_end=shift_end,
                show_progress=show_progress,
            )

        # Direct models have no separate trunk leg: CWS includes CC departures/returns.
    zero_trunk = 0.0

    # M3: supply every used microhub from one selected logistics center,
    # then perform bicycle routing independently from each microhub.
    used_microhubs = build_facility_summary(assigned_microhubs)

    m3_cc = select_logistics_center(
        centers,
        float(used_microhubs["Latitude"].mean()),
        float(used_microhubs["Longitude"].mean()),
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    

    with timed_stage("m3_supply", "routing", "M3"):
        (
            m3_supply_plan,
            m3_supply_visits,
            m3_microhub_available_at,
        ) = calculate_facility_supply_route(
            city=city,
            selected_cc=m3_cc,
            used_facilities=used_microhubs,
            truck_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
            facility_label="M3",
            routing_config=routing_config,
            traffic_profile=traffic_profile,
            time_traffic_provider=time_traffic_provider,
            traffic_zone=neighborhood_name,
            shift_start=shift_start,
            shift_end=shift_end,
            show_progress=show_progress,
        )

    with timed_stage("m3_cycling_last_mile", "routing", "M3"):
        (
            m3_bike_distance_km,
            m3_bike_duration_min,
            m3_bike_route_count,
            m3_bike_max_route_duration_min,
            m3_bike_detail_rows,
        ) = calculate_microhub_last_mile(
            city=city,
            assigned_microhubs=assigned_microhubs,
            bike_capacity=parameters["BICICLETA_CARGO"]["capacidad"],
            neighborhood_name=neighborhood_name,
            routing_config=routing_config,
            facility_available_at=m3_microhub_available_at,
            shift_end=shift_end,
            add_geometry=add_geometry,
            show_progress=show_progress,
        )

    used_microhub_count = len(used_microhubs)

    # M4/M5: supply every used PUDO from one selected logistics center.
    used_pudos = build_facility_summary(assigned_pudos)
    pudo_cc = select_logistics_center(
        centers,
        float(used_pudos["Latitude"].mean()),
        float(used_pudos["Longitude"].mean()),
        osrm_host=osrm_host,
        osrm_profile=osrm_profile,
    )
    with timed_stage("m45_supply", "routing", "M4/M5"):
        (
            pudo_supply_plan,
            pudo_supply_visits,
            _pudo_available_at,
        ) = calculate_facility_supply_route(
            city=city,
            selected_cc=pudo_cc,
            used_facilities=used_pudos,
            truck_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
            facility_label="M4/M5",
            routing_config=routing_config,
            traffic_profile=traffic_profile,
            time_traffic_provider=time_traffic_provider,
            traffic_zone=neighborhood_name,
            shift_start=shift_start,
            shift_end=shift_end,
            show_progress=show_progress,
        )
    with timed_stage("m4_walking_last_mile", "routing", "M4"):
        (
            m4_distance_km,
            m4_duration_min,
            m4_route_count,
            m4_max_route_duration_min,
            m4_used_pudo_count,
            m4_walking_detail_rows,
        ) = calculate_pudo_last_mile(
            city=city,
            assigned_pudos=assigned_pudos,
            walking_capacity=parameters["PUDO_A_PIE"]["capacidad"],
            neighborhood_name=neighborhood_name,
            routing_config=routing_config,
            add_geometry=add_geometry,
            show_progress=show_progress,
        )
    with timed_stage("m5_customer_collection", "routing", "M5"):
        (
            customer_travel_km,
            customer_travel_min,
            m5_customer_detail_rows,
        ) = calculate_customer_collection_travel(
            city,
            assigned_pudos,
            neighborhood_name,
        )

    print(f"Demand: {customer_count} customers, {package_count} packages")
    print(
            f"M1 {routing_config.algorithm.upper()} driving: "
            f"{m1_plan.route_count} routes | "
            f"{m1_plan.total_distance_km:.2f} km | "
            f"total={m1_plan.total_duration_min:.1f} min | "
            f"max={m1_plan.max_route_duration_min:.1f} min"
        )
    print(
            f"M2 {routing_config.algorithm.upper()} driving: "
            f"{m2_plan.route_count} routes | "
            f"{m2_plan.total_distance_km:.2f} km | "
            f"total={m2_plan.total_duration_min:.1f} min | "
            f"max={m2_plan.max_route_duration_min:.1f} min"
        )
    print(
        f"M3 supply: "
        f"{m3_supply_plan.route_count} truck routes | "
        f"{m3_supply_plan.total_distance_km:.2f} km | "
        f"total={m3_supply_plan.total_duration_min:.1f} min | "
        f"max={m3_supply_plan.max_route_duration_min:.1f} min"
    )

    print(
        f"M3 cycling: "
        f"{used_microhub_count} used microhubs | "
        f"{m3_bike_route_count} routes | "
        f"{m3_bike_distance_km:.2f} km | "
        f"{m3_bike_duration_min:.1f} min"
    )
    print(
        f"M4/M5 supply: "
        f"{pudo_supply_plan.route_count} truck routes | "
        f"{pudo_supply_plan.total_distance_km:.2f} km | "
        f"total={pudo_supply_plan.total_duration_min:.1f} min | "
        f"max={pudo_supply_plan.max_route_duration_min:.1f} min"
    )
    print(
        f"M4 walking: "
        f"{m4_used_pudo_count} used PUDOs | "
        f"{m4_route_count} routes | "
        f"{m4_distance_km:.2f} km | "
        f"total={m4_duration_min:.1f} min | "
        f"max={m4_max_route_duration_min:.1f} min"
    )

    print(f"M5 collection: {len(used_pudos)} used PUDOs")
    print(
        "M5 customer round trips: "
        f"{customer_travel_km:.2f} km, {customer_travel_min:.2f} min"
    )

    with timed_stage("route_detail_build", "postprocessing"):
        m1_detail_rows = build_route_detail_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            model_code="M1",
            leg="direct_delivery",
            vehicle_type="conventional_van",
            depot_name=str(direct_cc["Location"]),
            plan=m1_plan,
            clients=demand_points,
            depot_latitude=direct_cc_lat,
            depot_longitude=direct_cc_lon,
            add_geometry=add_geometry,
        )
        m2_detail_rows = build_route_detail_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            model_code="M2",
            leg="direct_delivery",
            vehicle_type="electric_van",
            depot_name=str(direct_cc["Location"]),
            plan=m2_plan,
            clients=demand_points,
            depot_latitude=direct_cc_lat,
            depot_longitude=direct_cc_lon,
            add_geometry=add_geometry,
        )
        m3_supply_detail_rows = build_route_detail_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            model_code="M3",
            leg="facility_supply",
            vehicle_type="electric_van",
            depot_name=str(m3_cc["Location"]),
            plan=m3_supply_plan,
            clients=m3_supply_visits,
            stop_label_column="Location",
            depot_latitude=float(m3_cc["Latitude"]),
            depot_longitude=float(m3_cc["Longitude"]),
            add_geometry=add_geometry,
        )
        m45_supply_detail_rows = build_route_detail_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            model_code="M4",
            leg="facility_supply",
            vehicle_type="electric_van",
            depot_name=str(pudo_cc["Location"]),
            plan=pudo_supply_plan,
            clients=pudo_supply_visits,
            stop_label_column="Location",
            depot_latitude=float(pudo_cc["Latitude"]),
            depot_longitude=float(pudo_cc["Longitude"]),
            add_geometry=add_geometry,
        )
        m5_supply_detail_rows = [
            {**row, "model": "M5", "route_id": row["route_id"].replace("M4_", "M5_", 1)}
            for row in m45_supply_detail_rows
        ]

    model_details = {
        "M1": m1_detail_rows,
        "M2": m2_detail_rows,
        "M3": m3_supply_detail_rows + m3_bike_detail_rows,
        "M4": m45_supply_detail_rows + m4_walking_detail_rows,
        "M5": m5_supply_detail_rows + m5_customer_detail_rows,
    }

    # ------------------------------------------------------------
    # Customer-routing audit (informational; never blocks simulation)
    # ------------------------------------------------------------

    with timed_stage("routing_audit", "postprocessing"):
        excluded_positions = list(m1_plan.unroutable_client_positions)

        unroutable_customer_rows = build_unroutable_customer_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            original_clients=original_demand_points,
            excluded_positions=excluded_positions,
        )

        audit_specs = (
            ("M1", "direct_delivery", m1_detail_rows),
            ("M2", "direct_delivery", m2_detail_rows),
            ("M3", "cycling_last_mile", m3_bike_detail_rows),
            ("M4", "walking_last_mile", m4_walking_detail_rows),
            ("M5", "customer_collection", m5_customer_detail_rows),
        )

        customer_route_audit_rows = []
        route_customer_summary_rows = []
        routing_integrity_summary_rows = []

        for audit_model, audit_leg, audit_route_rows in audit_specs:
            (
                model_customer_rows,
                model_route_rows,
                model_summary_row,
            ) = audit_customer_routes(
                city=city,
                neighborhood_name=neighborhood_name,
                model=audit_model,
                leg=audit_leg,
                original_clients=original_demand_points,
                routable_clients=demand_points,
                route_detail_rows=audit_route_rows,
                excluded_positions=excluded_positions,
            )

            customer_route_audit_rows.extend(model_customer_rows)
            route_customer_summary_rows.extend(model_route_rows)
            routing_integrity_summary_rows.append(model_summary_row)

    audit_details = {
        "customer_route_audit": customer_route_audit_rows,
        "route_customer_summary": route_customer_summary_rows,
        "routing_integrity_summary": routing_integrity_summary_rows,
        "unroutable_customers": unroutable_customer_rows,
        "performance_profile": performance_rows,
    }

    with timed_stage("summary_models", "postprocessing"):
        summary_results = [
            simulate_m1(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=direct_cc,
                package_count=package_count,
                route_plan=m1_plan,
                parameters=parameters,
                cost_parameters=cost_parameters,
            ),
            simulate_m2(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=direct_cc,
                package_count=package_count,
                route_plan=m2_plan,
                parameters=parameters,
                cost_parameters=cost_parameters,
            ),
            simulate_m3(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=m3_cc,
                last_mile_point=microhub_point,
                package_count=package_count,
                used_microhub_count=used_microhub_count,
                supply_plan=m3_supply_plan,
                bike_distance_km=m3_bike_distance_km,
                bike_duration_min=m3_bike_duration_min,
                bike_route_count=m3_bike_route_count,
                parameters=parameters,
                cost_parameters=cost_parameters,
            ),
            simulate_m4(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=pudo_cc,
                last_mile_point=pudo_point,
                package_count=package_count,
                used_pudo_count=m4_used_pudo_count,
                supply_plan=pudo_supply_plan,
                walking_distance_km=m4_distance_km,
                walking_duration_min=m4_duration_min,
                walking_route_count=m4_route_count,
                parameters=parameters,
                cost_parameters=cost_parameters,
            ),
            simulate_m5(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=pudo_cc,
                last_mile_point=pudo_point,
                package_count=package_count,
                customer_count=customer_count,
                used_pudo_count=len(used_pudos),
                supply_plan=pudo_supply_plan,
                customer_travel_km=customer_travel_km,
                customer_travel_min=customer_travel_min,
                parameters=parameters,
                cost_parameters=cost_parameters,
            ),
        ]

    return summary_results, model_details, audit_details


def simulate_city(
    city: str,
    demand_scenario: str,
    instance_size: int,
    active_zones: list[str] | None = None,
    demand_seed: int | None = None,
    demand_instance_id: str | None = None,
    *,
    osrm_host: str,
    osrm_profile: str,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
    time_traffic_provider: TimeTrafficProvider,
    shift_start: datetime,
    shift_end: datetime,
    cost_parameters: dict[str, float],
    add_geometry: bool = False,
    facility_filter_settings: FacilityFilterSettings | None = None,
    pudo_capacity_mode: str = "configured",
    microhub_capacity_mode: str = "configured",
    show_progress: bool = False,
    zone_result_callback=None,
    zone_failure_callback=None,
    continue_on_zone_error: bool = False,
):
    centers, boundaries, parameters_df = load_city_data(city)
    demand_instance = load_demand_instance(
        city,
        demand_scenario,
        instance_size,
        demand_seed=demand_seed,
        demand_instance_id=demand_instance_id,
    )
    demand_source_file = demand_instance.attrs.get("demand_source_file")
    resolved_demand_instance_id = demand_instance.attrs.get("demand_instance_id")
    resolved_demand_seed = demand_instance.attrs.get("demand_seed")
    classified_locations = load_classified_locations(city)
    microhubs, pudos = load_facility_candidates(classified_locations)
 
    print(
        f"Available facilities: "
        f"{len(microhubs)} microhubs, "
        f"{len(pudos)} PUDOs"
    )
    parameters = get_parameters(parameters_df)
    effective_filter_settings = (
        facility_filter_settings
        if facility_filter_settings is not None
        else FacilityFilterSettings()
    )
 
    all_results = []
    all_model_details = {model: [] for model in ("M1", "M2", "M3", "M4", "M5")}
    all_audit_details = {
        "customer_route_audit": [],
        "route_customer_summary": [],
        "routing_integrity_summary": [],
        "unroutable_customers": [],
        "performance_profile": [],
    }
 
    if active_zones is not None:
        boundaries = select_zones(boundaries, active_zones)
        warn_zone_overlaps(boundaries)
 
    total_zones = len(boundaries)
    
    simulation_start = perf_counter()
    for zone_number, (_, neighborhood) in enumerate(
        boundaries.iterrows(),
        start=1,
    ):
        zone_start = perf_counter()
        reset_osrm_cache_stats()
        try:
            neighborhood_name = neighborhood["zona"]
            zone_type = neighborhood["tipo"]
            print(f"\nSimulating {city.upper()} - {neighborhood_name} ({zone_type})")
            print("\n" + "=" * 60)
            print(
                f"ZONE {zone_number}/{total_zones}: "
                f"{neighborhood_name} ({zone_type})"
            )
            print("=" * 60)
            _stage_start = perf_counter()
            demand_points = filter_points_by_neighborhood(demand_instance, neighborhood)
            zone_performance_rows = [
                _performance_row(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    stage="zone_demand_filter",
                    seconds=perf_counter() - _stage_start,
                    category="preprocessing",
                )
            ]
            if demand_points.empty:
                print(f"No demand stops within {neighborhood_name}.")
                continue
     
            _stage_start = perf_counter()
            demand_centroid = calculate_demand_weighted_centroid(demand_points)
            microhub_point = select_operational_point(
                strategy="nearest_microhub_facility",
                neighborhood_records=demand_points,
                classified_records=classified_locations,
                target_latitude=demand_centroid.latitude,
                target_longitude=demand_centroid.longitude,
            )
            pudo_point = select_operational_point(
                strategy="nearest_pudo_facility",
                neighborhood_records=demand_points,
                classified_records=classified_locations,
                target_latitude=demand_centroid.latitude,
                target_longitude=demand_centroid.longitude,
            )
            zone_performance_rows.append(
                _performance_row(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    stage="operational_point_selection",
                    seconds=perf_counter() - _stage_start,
                    category="preprocessing",
                )
            )

     
            _stage_start = perf_counter()
            candidate_microhubs = filter_facilities_for_zone(
                facilities=microhubs,
                neighborhood=neighborhood,
                settings=effective_filter_settings,
                zone_crs=boundaries.crs,
                facility_label="Microhub",
            )
            candidate_pudos = filter_facilities_for_zone(
                facilities=pudos,
                neighborhood=neighborhood,
                settings=effective_filter_settings,
                zone_crs=boundaries.crs,
                facility_label="PUDO",
            )
            zone_performance_rows.append(
                _performance_row(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    stage="facility_filtering",
                    seconds=perf_counter() - _stage_start,
                    category="preprocessing",
                )
            )

    
            microhub_capacity = (
                None
                if microhub_capacity_mode == "unlimited"
                else float(parameters["MICROHUB"]["capacidad"])
            )
            pudo_capacity = (
                None
                if pudo_capacity_mode == "unlimited"
                else float(parameters["PUDO"]["capacidad"])
            )
    
            _stage_start = perf_counter()
            assigned_microhubs = assign_customers_to_nearest_facility(
                customers=demand_points,
                facilities=candidate_microhubs,
                osrm_host=osrm_host,
                osrm_profile="cycling",
                facility_capacity=microhub_capacity,
                show_progress=show_progress,
                progress_label="Assigning customers to microhubs",
            )
            zone_performance_rows.append(
                _performance_row(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    stage="microhub_assignment",
                    seconds=perf_counter() - _stage_start,
                    category="facility_assignment",
                    model="M3",
                )
            )

    
            print(
                f"Candidate microhubs: {len(candidate_microhubs)} of "
                f"{len(microhubs)} city microhubs | "
                f"used: {assigned_microhubs['assigned_facility'].nunique()} | "
                f"capacity mode: {microhub_capacity_mode}"
            )
    
            _stage_start = perf_counter()
            assigned_pudos = assign_customers_to_nearest_facility(
                customers=demand_points,
                facilities=candidate_pudos,
                osrm_host=osrm_host,
                osrm_profile="walking",
                facility_capacity=pudo_capacity,
                show_progress=show_progress,
                progress_label="Assigning customers to PUDOs",
            )
            zone_performance_rows.append(
                _performance_row(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    stage="pudo_assignment",
                    seconds=perf_counter() - _stage_start,
                    category="facility_assignment",
                    model="M4/M5",
                )
            )

    
            print(
                f"Candidate PUDOs: {len(candidate_pudos)} of "
                f"{len(pudos)} city PUDOs | "
                f"used: {assigned_pudos['assigned_facility'].nunique()} | "
                f"capacity mode: {pudo_capacity_mode}"
            )
    
            _stage_start = perf_counter()
            results, model_details, audit_details = simulate_neighborhood(
                city=city,
                neighborhood_name=neighborhood_name,
                demand_points=demand_points,
                demand_centroid=demand_centroid,
                microhub_point=microhub_point,
                pudo_point=pudo_point,
                assigned_pudos=assigned_pudos,
                assigned_microhubs=assigned_microhubs,
                centers=centers,
                parameters=parameters,
                cost_parameters=cost_parameters,
                osrm_host=osrm_host,
                osrm_profile=osrm_profile,
                routing_config=routing_config,
                traffic_profile=traffic_profile,
                time_traffic_provider=time_traffic_provider,
                shift_start=shift_start,
                shift_end=shift_end,
                add_geometry=add_geometry,
                show_progress=show_progress,
            )
            zone_performance_rows.append(
                _performance_row(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    stage="simulate_neighborhood_total",
                    seconds=perf_counter() - _stage_start,
                    category="routing_and_postprocessing",
                )
            )

            cache_stats = get_osrm_cache_stats()
            zone_performance_rows.extend(
                [
                    _performance_row(
                        city=city,
                        neighborhood_name=neighborhood_name,
                        stage="osrm_http_total",
                        seconds=float(cache_stats["http_seconds"]),
                        category="osrm",
                        detail=f"requests={cache_stats['http_requests']}",
                    ),
                    _performance_row(
                        city=city,
                        neighborhood_name=neighborhood_name,
                        stage="osrm_cache_load",
                        seconds=float(cache_stats["cache_load_seconds"]),
                        category="osrm",
                        detail=(
                            f"hits={cache_stats['cache_hits']} "
                            f"misses={cache_stats['cache_misses']}"
                        ),
                    ),
                    _performance_row(
                        city=city,
                        neighborhood_name=neighborhood_name,
                        stage="osrm_cache_write",
                        seconds=float(cache_stats["cache_write_seconds"]),
                        category="osrm",
                    ),
                ]
            )
            audit_details.setdefault("performance_profile", []).extend(
                zone_performance_rows
            )

            print("\n" + "-" * 60)
            print(f"ZONE PERFORMANCE PROFILE — {neighborhood_name}")
            print("-" * 60)
            for perf_row in sorted(
                audit_details["performance_profile"],
                key=lambda item: item["seconds"],
                reverse=True,
            ):
                print(
                    f"{perf_row['stage']:<34} "
                    f"{perf_row['seconds']:>9.2f} s "
                    f"[{perf_row['category']}]"
                )
            print(
                "OSRM cache: "
                f"hits={cache_stats['cache_hits']} | "
                f"misses={cache_stats['cache_misses']} | "
                f"http_requests={cache_stats['http_requests']} | "
                f"http={cache_stats['http_seconds']:.2f} s | "
                f"cache_load={cache_stats['cache_load_seconds']:.2f} s | "
                f"cache_write={cache_stats['cache_write_seconds']:.2f} s"
            )
            print("-" * 60)

     
            for result in results:
                result["demand_scenario"] = demand_scenario
                result["demand_instance_size"] = instance_size
                result["demand_instance_id"] = resolved_demand_instance_id
                result["demand_seed"] = resolved_demand_seed
                result["demand_source_file"] = demand_source_file
                result["routing_seed"] = routing_config.ils_random_seed

            all_results.extend(results)
            for model_code, detail_rows in model_details.items():
                all_model_details[model_code].extend(detail_rows)
            for audit_name, audit_rows in audit_details.items():
                all_audit_details[audit_name].extend(audit_rows)
            print(
                f"{neighborhood_name} ({zone_type}): {len(demand_points)} customers, "
                f"{int(demand_points['Demand'].sum())} packages simulated"
            )
            
            zone_runtime = perf_counter() - zone_start
            total_runtime = perf_counter() - simulation_start
    
            print(
                f"Completed zone {zone_number}/{total_zones} "
                f"in {zone_runtime:.1f} s "
                f"(total elapsed: {total_runtime:.1f} s)"
            )
     
            if zone_result_callback is not None:
                zone_result_callback(
                    neighborhood_name=neighborhood_name,
                    zone_type=zone_type,
                    results=pd.DataFrame(results),
                    model_details={
                        model_code: pd.DataFrame(detail_rows)
                        for model_code, detail_rows in model_details.items()
                    },
                    audit_details={
                        audit_name: pd.DataFrame(audit_rows)
                        for audit_name, audit_rows in audit_details.items()
                    },
                    runtime_seconds=zone_runtime,
                )
        except Exception as exc:
            zone_runtime = perf_counter() - zone_start
            neighborhood_name = locals().get(
                "neighborhood_name", f"zone_{zone_number}"
            )
            zone_type = locals().get("zone_type", "unknown")
            print(
                f"Zone {neighborhood_name} failed after {zone_runtime:.1f} s: "
                f"{type(exc).__name__}: {exc}"
            )
            if zone_failure_callback is not None:
                zone_failure_callback(
                    neighborhood_name=neighborhood_name,
                    zone_type=zone_type,
                    error=exc,
                    runtime_seconds=zone_runtime,
                )
            if continue_on_zone_error:
                continue
            raise
    return (
        pd.DataFrame(all_results),
        {
            model_code: pd.DataFrame(detail_rows)
            for model_code, detail_rows in all_model_details.items()
        },
        {
            audit_name: pd.DataFrame(audit_rows)
            for audit_name, audit_rows in all_audit_details.items()
        },
    )
