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
from code.simulation.data_loader import load_city_data, load_classified_locations, load_demand_instance
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
    show_progress: bool = False,
):
    """Run the five models using demand stops and classified facilities."""

    customer_count = len(demand_points)
    package_count = int(demand_points["Demand"].sum())
    client_demands = demand_points["Demand"].to_numpy(dtype=float)

    if customer_count == 0 or package_count == 0:
        return [], {model: [] for model in ("M1", "M2", "M3", "M4", "M5")}

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

    m1_plan = route_planner.build_capacity_plan(
        depot_latitude=direct_cc_lat,
        depot_longitude=direct_cc_lon,
        clients=demand_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_CONV"]["capacidad"],
        client_demands=client_demands,
        route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
        routing_algorithm=routing_config.algorithm,
        ils_max_iterations=routing_config.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            routing_config.ils_max_iterations_without_improvement
        ),
        ils_perturbation_moves=routing_config.ils_perturbation_moves,
        ils_random_seed=routing_config.ils_random_seed,
        traffic_profile=traffic_profile,
        time_traffic_provider=time_traffic_provider,
        traffic_zone=neighborhood_name,
        shift_start=shift_start,
        shift_end=shift_end,
        show_progress=show_progress,
    )
    m2_plan = route_planner.build_capacity_plan(
        depot_latitude=direct_cc_lat,
        depot_longitude=direct_cc_lon,
        clients=demand_points,
        transport_mode="driving",
        vehicle_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
        client_demands=client_demands,
        route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
        routing_algorithm=routing_config.algorithm,
        ils_max_iterations=routing_config.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            routing_config.ils_max_iterations_without_improvement
        ),
        ils_perturbation_moves=routing_config.ils_perturbation_moves,
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
    

    m3_supply_plan, m3_supply_visits = calculate_facility_supply_route(
        city=city,
        selected_cc=m3_cc,
        used_facilities=used_microhubs,
        truck_capacity=parameters["FURGONETA_CONV"]["capacidad"],
        facility_label="M3",
        routing_config=routing_config,
        traffic_profile=traffic_profile,
        time_traffic_provider=time_traffic_provider,
        traffic_zone=neighborhood_name,
        shift_start=shift_start,
        shift_end=shift_end,
        show_progress=show_progress,
    )

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
    pudo_supply_plan, pudo_supply_visits = calculate_facility_supply_route(
        city=city,
        selected_cc=pudo_cc,
        used_facilities=used_pudos,
        truck_capacity=parameters["FURGONETA_CONV"]["capacidad"],
        facility_label="M4/M5",
        routing_config=routing_config,
        traffic_profile=traffic_profile,
        time_traffic_provider=time_traffic_provider,
        traffic_zone=neighborhood_name,
        shift_start=shift_start,
        shift_end=shift_end,
        show_progress=show_progress,
    )
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
        show_progress=show_progress,
    )
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

    m1_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M1",
        leg="direct_delivery",
        vehicle_type="conventional_van",
        depot_name=str(direct_cc["Location"]),
        plan=m1_plan,
        clients=demand_points,
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
    )
    m3_supply_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M3",
        leg="facility_supply",
        vehicle_type="conventional_van",
        depot_name=str(m3_cc["Location"]),
        plan=m3_supply_plan,
        clients=m3_supply_visits,
        stop_label_column="Location",
    )
    m45_supply_detail_rows = build_route_detail_rows(
        city=city,
        neighborhood_name=neighborhood_name,
        model_code="M4",
        leg="facility_supply",
        vehicle_type="conventional_van",
        depot_name=str(pudo_cc["Location"]),
        plan=pudo_supply_plan,
        clients=pudo_supply_visits,
        stop_label_column="Location",
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

    summary_results = [
        simulate_m1(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=direct_cc,
            last_mile_point=demand_centroid,
            package_count=package_count,
            route_plan=m1_plan,
            parameters=parameters,
            cost_parameters=cost_parameters,
        ),
        simulate_m2(
            city=city,
            neighborhood_name=neighborhood_name,
            selected_cc=direct_cc,
            last_mile_point=demand_centroid,
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

    return summary_results, model_details


def simulate_city(
    city: str,
    demand_scenario: str,
    instance_size: int,
    active_zones: list[str] | None = None,
    *,
    osrm_host: str,
    osrm_profile: str,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
    time_traffic_provider: TimeTrafficProvider,
    shift_start: datetime,
    shift_end: datetime,
    cost_parameters: dict[str, float],
    facility_filter_settings: FacilityFilterSettings | None = None,
    pudo_capacity_mode: str = "configured",
    microhub_capacity_mode: str = "configured",
    show_progress: bool = False,
):
    centers, boundaries, parameters_df = load_city_data(city)
    demand_instance = load_demand_instance(city, demand_scenario, instance_size)
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
        neighborhood_name = neighborhood["zona"]
        zone_type = neighborhood["tipo"]
        print(f"\nSimulating {city.upper()} - {neighborhood_name} ({zone_type})")
        print("\n" + "=" * 60)
        print(
            f"ZONE {zone_number}/{total_zones}: "
            f"{neighborhood_name} ({zone_type})"
        )
        print("=" * 60)
        demand_points = filter_points_by_neighborhood(demand_instance, neighborhood)
        if demand_points.empty:
            print(f"No demand stops within {neighborhood_name}.")
            continue
 
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

        assigned_microhubs = assign_customers_to_nearest_facility(
            customers=demand_points,
            facilities=candidate_microhubs,
            osrm_host=osrm_host,
            osrm_profile="cycling",
            facility_capacity=microhub_capacity,
            show_progress=show_progress,
            progress_label="Assigning customers to microhubs",
        )

        print(
            f"Candidate microhubs: {len(candidate_microhubs)} of "
            f"{len(microhubs)} city microhubs | "
            f"used: {assigned_microhubs['assigned_facility'].nunique()} | "
            f"capacity mode: {microhub_capacity_mode}"
        )

        assigned_pudos = assign_customers_to_nearest_facility(
            customers=demand_points,
            facilities=candidate_pudos,
            osrm_host=osrm_host,
            osrm_profile="walking",
            facility_capacity=pudo_capacity,
            show_progress=show_progress,
            progress_label="Assigning customers to PUDOs",
        )

        print(
            f"Candidate PUDOs: {len(candidate_pudos)} of "
            f"{len(pudos)} city PUDOs | "
            f"used: {assigned_pudos['assigned_facility'].nunique()} | "
            f"capacity mode: {pudo_capacity_mode}"
        )

        results, model_details = simulate_neighborhood(
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
            show_progress=show_progress,
        )
 
        all_results.extend(results)
        for model_code, detail_rows in model_details.items():
            all_model_details[model_code].extend(detail_rows)
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
 
    return (
        pd.DataFrame(all_results),
        {
            model_code: pd.DataFrame(detail_rows)
            for model_code, detail_rows in all_model_details.items()
        },
    )
