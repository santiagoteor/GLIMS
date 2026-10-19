from datetime import datetime
from code.simulation.m3_eligibility import classify_m3_customers

import pandas as pd
import numpy as np

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
from code.simulation.audit import (
    audit_customer_routes,
    build_osrm_snapping_audit_rows,
    build_unroutable_customer_rows,
)
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
from code.simulation.microhub_coverage import calculate_microhub_demand_coverage
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
    selected_models: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5"),
    vehicle_variants: tuple[str, ...] = ("electric", "conventional"),
    m3_unserved_rows: list[dict] | None = None,
    m3_fallback_enabled: bool = False,
    m3_fallback_variants: tuple[str, ...] = ("conventional", "electric"),
):
    """Run the five models using demand stops and classified facilities."""

    performance_rows = []

    # The configured shift duration is the common maximum standalone route
    # duration used by routing. For M3/M4, the real supply arrival is applied
    # later to the timeline; a late facility arrival may therefore generate
    # shift overtime instead of making the whole simulation fail.
    shift_duration_min = (shift_end - shift_start).total_seconds() / 60.0
    if shift_duration_min <= 0:
        raise ValueError("The configured shift duration must be greater than zero.")

    max_last_service_completion_min = None
    if routing_config.last_service_deadline_enabled:
        margin_min = float(routing_config.last_service_margin_min)
        if margin_min < 0 or margin_min >= shift_duration_min:
            raise ValueError(
                "last_service_margin_min must be non-negative and smaller "
                "than the configured shift duration."
            )
        max_last_service_completion_min = shift_duration_min - margin_min

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
                "osrm_snapping_audit": [],
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
            max_route_duration_min=shift_duration_min,
            max_last_stop_completion_min=max_last_service_completion_min,
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
            last_meter_access_enabled=(
                routing_config.last_meter_applies_to("M1")
            ),
            last_meter_walking_speed_m_s=(
                routing_config.last_meter_walking_speed_m_s
            ),
            last_meter_round_trip=routing_config.last_meter_round_trip,
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

    # The stable row identifier travels through M3 eligibility and assignment.
    # Only demand remaining routable for M1/M2 can be assigned to fallback.
    if m3_fallback_enabled and "M3" in selected_models:
        served_ids = set(assigned_microhubs["_m3_row_position"].tolist())
        fallback_clients = demand_points.loc[
            ~demand_points["_m3_row_position"].isin(served_ids)
        ].reset_index(drop=True).copy()
    else:
        fallback_clients = demand_points.iloc[:0].copy()

    m1_capacity = float(parameters["FURGONETA_CONV"]["capacidad"])
    m2_capacity = float(parameters["FURGONETA_ELEC"]["capacidad"])

    direct_routing_reusable = (
        abs(m1_capacity - m2_capacity) <= 1e-9
        and routing_config.last_meter_applies_to("M1")
        == routing_config.last_meter_applies_to("M2")
    )

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
                max_route_duration_min=shift_duration_min,
                max_last_stop_completion_min=max_last_service_completion_min,
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
                last_meter_access_enabled=(
                    routing_config.last_meter_applies_to("M2")
                ),
                last_meter_walking_speed_m_s=(
                    routing_config.last_meter_walking_speed_m_s
                ),
                last_meter_round_trip=routing_config.last_meter_round_trip,
            )

        # M3 hybrid: one direct-delivery plan per distinct vehicle capacity/constraint.
    # M1/M2 plans cannot be sliced: routes must be reoptimized for the excluded set.
    m3_fallback_plans = {}
    if len(fallback_clients):
        for variant in m3_fallback_variants:
            vehicle_code = "M1" if variant == "conventional" else "M2"
            capacity = m1_capacity if variant == "conventional" else m2_capacity
            reuse_variant = next((v for v, plan in m3_fallback_plans.items()
                if abs(capacity - (m1_capacity if v == "conventional" else m2_capacity)) <= 1e-9
                and routing_config.last_meter_applies_to(vehicle_code) ==
                    routing_config.last_meter_applies_to("M1" if v == "conventional" else "M2")), None)
            if reuse_variant is not None:
                m3_fallback_plans[variant] = m3_fallback_plans[reuse_variant]
                continue
            with timed_stage("m3_fallback_routing_" + variant, "routing", "M3"):
                m3_fallback_plans[variant] = route_planner.build_capacity_plan(
                    depot_latitude=direct_cc_lat, depot_longitude=direct_cc_lon,
                    clients=fallback_clients, transport_mode="driving",
                    vehicle_capacity=capacity,
                    client_demands=fallback_clients["Demand"].to_numpy(dtype=float),
                    max_route_duration_min=shift_duration_min,
                    max_last_stop_completion_min=max_last_service_completion_min,
                    route_start_time_per_route_min=DIRECT_VAN_LOADING_TIME_PER_ROUTE_MIN,
                    routing_algorithm=routing_config.algorithm,
                    cws_allow_route_reversal=routing_config.cws_allow_route_reversal,
                    ils_max_iterations=routing_config.ils_max_iterations,
                    ils_max_iterations_without_improvement=routing_config.ils_max_iterations_without_improvement,
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
                    shift_start=shift_start, shift_end=shift_end,
                    show_progress=show_progress,
                    exclude_unroutable_clients=True,
                    last_meter_access_enabled=routing_config.last_meter_applies_to(vehicle_code),
                    last_meter_walking_speed_m_s=routing_config.last_meter_walking_speed_m_s,
                    last_meter_round_trip=routing_config.last_meter_round_trip,
                )

    for variant, plan in m3_fallback_plans.items():
        if plan.unroutable_client_positions:
            raise RuntimeError(
                "M3 fallback has OSRM-unroutable customers for " + variant
                + "; cannot report them as delivered."
            )

    # Direct models have no separate trunk leg: CWS includes CC departures/returns.
    zero_trunk = 0.0

    # M3 only: restrict assignments to Haversine-eligible customers.
    # The input assignment is already filtered before OSRM assignment.
    m3_customer_count = len(assigned_microhubs)
    m3_package_count = int(assigned_microhubs["Demand"].sum())
    m3_unserved_rows = m3_unserved_rows or []

    # M3: supply every used microhub from one selected logistics center,
    # then perform bicycle routing independently from each microhub.
    used_microhubs = build_facility_summary(assigned_microhubs)

    if m3_customer_count:
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
                max_route_duration_min=shift_duration_min,
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
                max_route_duration_min=shift_duration_min,
                facility_available_at=m3_microhub_available_at,
                shift_end=shift_end,
                performance_rows=performance_rows,
                add_geometry=add_geometry,
                show_progress=show_progress,
            )

    else:
        m3_cc = direct_cc
        m3_supply_plan = None
        m3_supply_visits = []
        m3_microhub_available_at = {}
        m3_bike_distance_km = 0.0
        m3_bike_duration_min = 0.0
        m3_bike_route_count = 0
        m3_bike_max_route_duration_min = 0.0
        m3_bike_detail_rows = []

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
            pudo_available_at,
        ) = calculate_facility_supply_route(
            city=city,
            selected_cc=pudo_cc,
            used_facilities=used_pudos,
            truck_capacity=parameters["FURGONETA_ELEC"]["capacidad"],
            facility_label="M4/M5",
            max_route_duration_min=shift_duration_min,
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
            max_route_duration_min=shift_duration_min,
            facility_available_at=pudo_available_at,
            shift_end=shift_end,
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
            routing_config=routing_config,
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
    if m3_supply_plan is None:
        print("M3 supply: 0 truck routes | 0.00 km | total=0.0 min | max=0.0 min")
    else:
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
        m3_supply_detail_rows = []
        if m3_supply_plan is not None:
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
                routable_clients=(assigned_microhubs if audit_model == "M3" else demand_points),
                route_detail_rows=audit_route_rows,
                excluded_positions=(excluded_positions if audit_model != "M3" else [
                    pos for pos, row in original_demand_points.reset_index(drop=True).iterrows()
                    if str(row.get('customer_id', row.get('Customer_ID', row.get('ID', row.get('id', pos)))))
                    not in set(assigned_microhubs.get('customer_id', assigned_microhubs.get('Customer_ID', assigned_microhubs.get('ID', assigned_microhubs.get('id', pd.Series(dtype=object))))).astype(str))
                ]),
            )

            customer_route_audit_rows.extend(model_customer_rows)
            route_customer_summary_rows.extend(model_route_rows)
            routing_integrity_summary_rows.append(model_summary_row)

        osrm_snapping_audit_rows = build_osrm_snapping_audit_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            original_clients=original_demand_points,
            excluded_positions=excluded_positions,
            customer_route_audit_rows=customer_route_audit_rows,
        )

    m3_last_meter_access_km = float(
        sum(float(row.get("last_meter_access_distance_km", 0.0) or 0.0) for row in m3_bike_detail_rows)
    )
    m3_last_meter_access_time_min = float(
        sum(float(row.get("last_meter_access_time_min", 0.0) or 0.0) for row in m3_bike_detail_rows)
    )
    m4_last_meter_access_km = float(
        sum(float(row.get("last_meter_access_distance_km", 0.0) or 0.0) for row in m4_walking_detail_rows)
    )
    m4_last_meter_access_time_min = float(
        sum(float(row.get("last_meter_access_time_min", 0.0) or 0.0) for row in m4_walking_detail_rows)
    )
    m5_last_meter_access_km = float(
        sum(float(row.get("last_meter_access_distance_km", 0.0) or 0.0) for row in m5_customer_detail_rows)
    )
    m5_last_meter_access_time_min = float(
        sum(float(row.get("last_meter_access_time_min", 0.0) or 0.0) for row in m5_customer_detail_rows)
    )

    audit_details = {
        "customer_route_audit": customer_route_audit_rows,
        "route_customer_summary": route_customer_summary_rows,
        "routing_integrity_summary": routing_integrity_summary_rows,
        "unroutable_customers": unroutable_customer_rows,
        "osrm_snapping_audit": osrm_snapping_audit_rows,
        "performance_profile": performance_rows,
        "m3_unserved_clients": m3_unserved_rows,
    }

    with timed_stage("summary_models", "postprocessing"):
        summary_results = []
        if "M1" in selected_models:
            result = simulate_m1(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=direct_cc,
                package_count=package_count,
                route_plan=m1_plan,
                parameters=parameters,
                cost_parameters=cost_parameters,
            )
            result["supply_vehicle_type"] = "conventional"
            summary_results.append(result)
        if "M2" in selected_models:
            result = simulate_m2(
                city=city,
                neighborhood_name=neighborhood_name,
                selected_cc=direct_cc,
                package_count=package_count,
                route_plan=m2_plan,
                parameters=parameters,
                cost_parameters=cost_parameters,
            )
            result["supply_vehicle_type"] = "electric"
            summary_results.append(result)
        if "M3" in selected_models:
            for variant in vehicle_variants:
                if m3_customer_count:
                    result = simulate_m3(
                    city=city,
                    neighborhood_name=neighborhood_name,
                    selected_cc=m3_cc,
                    last_mile_point=microhub_point,
                    package_count=m3_package_count,
                    used_microhub_count=used_microhub_count,
                    supply_plan=m3_supply_plan,
                    bike_distance_km=m3_bike_distance_km,
                    bike_duration_min=m3_bike_duration_min,
                    bike_route_count=m3_bike_route_count,
                    last_meter_access_km=m3_last_meter_access_km,
                    last_meter_access_time_min=m3_last_meter_access_time_min,
                    parameters=parameters,
                    cost_parameters=cost_parameters,
                        supply_vehicle_type=variant,
                    )
                if not m3_customer_count:
                    # Do not claim a route, emissions, or expenditure when nothing was delivered.
                    # Build the zero-service row from M1's schema without invoking M3 cost code.
                    result = dict(summary_results[0]) if summary_results else {}
                    result.update({"model_code": "M3", "modelo": "M3: CC -> Microhubs -> Bicicleta",
                                   "supply_vehicle_type": variant, "paquetes": 0,
                                   "numero_puntos_ultima_milla_usados": 0})
                    # Zero-service M3 must not inherit any M1 economic or route metrics.
                    zero_metrics = (
                        "km_recorridos", "network_km", "last_meter_access_km",
                        "last_meter_access_time_min", "numero_viajes", "direct_km",
                        "direct_trip_count", "supply_km", "supply_trip_count",
                        "last_mile_km", "last_mile_trip_count", "emisiones_co2_kg",
                        "emisiones_nox_kg", "costo_operacion_ruta_eur",
                        "costo_servicio_facility_eur", "costo_distancia_eur",
                        "costo_laboral_eur", "costo_facility_fijo_eur",
                        "costo_almacen_fijo_eur", "costo_manipulacion_eur",
                        "costo_vehiculo_fijo_eur", "costo_capex_eur",
                        "costo_tiempo_cliente_eur", "costo_carbono_eur",
                        "otros_costos_eur", "costo_total_eur",
                        "costo_por_paquete_eur", "co2_kg_por_paquete",
                        "nox_g_por_paquete", "co2_kg_por_km",
                    )
                    for key in zero_metrics:
                        result[key] = 0
                    result["centro_logistico"] = m3_cc["Location"]
                    result["latitud_punto_ultima_milla"] = None
                    result["longitud_punto_ultima_milla"] = None
                    result["tipo_punto_ultima_milla"] = None
                    result["estrategia_punto_ultima_milla"] = None
                    result["punto_ultima_milla"] = None
                    result["supply_vehicle_co2_kg"] = 0
                    result["supply_vehicle_nox_kg"] = 0
                    result["clientes_atendidos"] = 0
                    result["paquetes_atendidos"] = 0
                # The variant applies to both M3 supply and fallback direct vans.
                # M1/M2 fixed warehouse cost is shared, not charged twice.
                if m3_fallback_enabled:
                    result["m3_fallback_enabled"] = True
                    result["m3_microhub_packages"] = m3_package_count
                    result["m3_fallback_packages"] = 0
                    if variant in m3_fallback_plans:
                        fallback_plan = m3_fallback_plans[variant]
                        fallback_packages = int(fallback_clients["Demand"].sum())
                        fallback_result = (simulate_m1 if variant == "conventional" else simulate_m2)(
                            city=city, neighborhood_name=neighborhood_name,
                            selected_cc=direct_cc, package_count=fallback_packages,
                            route_plan=fallback_plan, parameters=parameters,
                            cost_parameters=cost_parameters,
                        )
                        from code.simulation.m3_hybrid import combine_m3_fallback
                        result = combine_m3_fallback(
                            result, fallback_result,
                            microhub_packages=m3_package_count,
                            fallback_packages=fallback_packages,
                        )
                summary_results.append(result)
        if "M4" in selected_models:
            for variant in vehicle_variants:
                result = simulate_m4(
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
                last_meter_access_km=m4_last_meter_access_km,
                last_meter_access_time_min=m4_last_meter_access_time_min,
                parameters=parameters,
                cost_parameters=cost_parameters,
                    supply_vehicle_type=variant,
                )
                summary_results.append(result)
        if "M5" in selected_models:
            for variant in vehicle_variants:
                result = simulate_m5(
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
                last_meter_access_km=m5_last_meter_access_km,
                last_meter_access_time_min=m5_last_meter_access_time_min,
                parameters=parameters,
                cost_parameters=cost_parameters,
                    supply_vehicle_type=variant,
                )
                summary_results.append(result)

    # Include direct fallback legs in the M3 route exports, tagged per variant.
    # Existing microhub exclusions are retained as diagnostic records.
    m3_fallback_detail_rows = {}
    for variant, plan in m3_fallback_plans.items():
        m3_fallback_detail_rows[variant] = build_route_detail_rows(
            city=city, neighborhood_name=neighborhood_name,
            model_code="M3", leg="fallback_direct_delivery",
            vehicle_type=("electric_van" if variant == "electric" else "conventional_van"),
            depot_name=str(direct_cc["Location"]), plan=plan,
            clients=fallback_clients, depot_latitude=direct_cc_lat,
            depot_longitude=direct_cc_lon, add_geometry=add_geometry,
        )

    # Separate per-technology audit: microhub exclusion does not imply hybrid
    # non-delivery. This audit verifies fallback assignments independently.
    for variant, rows in m3_fallback_detail_rows.items():
        fallback_ids = set(fallback_clients["_m3_row_position"].tolist())
        fallback_excluded = [
            i for i, row in original_demand_points.iterrows()
            if row["_m3_row_position"] not in fallback_ids
        ]
        customer_rows, route_rows, summary_row = audit_customer_routes(
            city=city, neighborhood_name=neighborhood_name,
            model="M3_fallback_" + variant,
            leg="fallback_direct_delivery",
            original_clients=original_demand_points,
            routable_clients=fallback_clients,
            route_detail_rows=rows,
            excluded_positions=fallback_excluded,
        )
        audit_details["customer_route_audit"].extend(customer_rows)
        audit_details["route_customer_summary"].extend(route_rows)
        audit_details["routing_integrity_summary"].append(summary_row)

    # Expand the shared physical routes into explicitly labelled technology
    # scenarios only after customer audits (which must count each delivery once).
    # The OSRM route geometry and duration are common assumptions; no extra
    # route optimisation or technology-specific timing is implied here.
    for model_code in ("M3", "M4", "M5"):
        if model_code not in selected_models:
            continue
        shared_rows = model_details[model_code]
        scenario_rows = []
        for variant in vehicle_variants:
            van_label = (
                "electric_van" if variant == "electric" else "conventional_van"
            )
            for row in shared_rows:
                scenario_row = dict(row)
                scenario_row["supply_vehicle_type"] = variant
                scenario_row["route_id"] = (
                    f"{row['route_id']}_{variant}"
                    if row.get("route_id") else row.get("route_id")
                )
                if row.get("leg") == "facility_supply":
                    scenario_row["vehicle_type"] = van_label
                scenario_rows.append(scenario_row)
        if model_code == "M3":
            for variant in vehicle_variants:
                for row in m3_fallback_detail_rows.get(variant, []):
                    scenario_row = dict(row)
                    scenario_row["supply_vehicle_type"] = variant
                    scenario_row["route_id"] = f"{row['route_id']}_{variant}"
                    scenario_rows.append(scenario_row)
        model_details[model_code] = scenario_rows

    model_details = {code: rows for code, rows in model_details.items() if code in selected_models}
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
    selected_models: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5"),
    vehicle_variants: tuple[str, ...] = ("electric", "conventional"),
    microhub_coverage_radius_m: float = 3000.0,
    m3_fallback_enabled: bool = False,
    m3_fallback_variants: tuple[str, ...] = ("conventional", "electric"),
    save_m3_priority_audit: bool = False,
    zone_result_callback=None,
    zone_failure_callback=None,
    zone_skipped_callback=None,
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
        "osrm_snapping_audit": [],
        "performance_profile": [],
        "m3_unserved_clients": [],
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
                if zone_skipped_callback is not None:
                    zone_skipped_callback(
                        neighborhood_name=neighborhood_name,
                        zone_type=zone_type,
                        runtime_seconds=perf_counter() - zone_start,
                    )
                continue
     
            demand_points = demand_points.reset_index(drop=True).copy()
            demand_points["_m3_row_position"] = range(len(demand_points))
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
            candidate_microhubs = microhubs.copy().reset_index(drop=True)  # All city hubs for diagnostic M3
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
            eligible_m3, m3_unserved_rows = classify_m3_customers(
                demand_points, candidate_microhubs, city=city,
                neighborhood=neighborhood_name, radius_m=microhub_coverage_radius_m,
            )
            m3_priority_rows = []
            if not eligible_m3.empty:
                assigned_microhubs = assign_customers_to_nearest_facility(
                customers=eligible_m3,
                facilities=candidate_microhubs,
                osrm_host=osrm_host,
                osrm_profile="cycling",
                facility_capacity=microhub_capacity,
                show_progress=show_progress,
                progress_label="Assigning customers to microhubs",
                max_haversine_distance_m=microhub_coverage_radius_m,
                allow_unassigned=True,
                priority_nearest_first=(microhub_capacity is not None),
                priority_audit_rows=(m3_priority_rows if save_m3_priority_audit else None),
            )
                capacity_rejected = assigned_microhubs.loc[assigned_microhubs["assigned_facility"].isna()]
                if not capacity_rejected.empty:
                    from code.simulation.m3_eligibility import rejected_capacity_rows
                    m3_unserved_rows.extend(rejected_capacity_rows(capacity_rejected, candidate_microhubs, city, neighborhood_name, microhub_coverage_radius_m))
                assigned_microhubs = assigned_microhubs.dropna(subset=["assigned_facility"]).reset_index(drop=True)
            else:
                assigned_microhubs = eligible_m3.copy()
                for column in ("assigned_facility", "facility_latitude", "facility_longitude", "assigned_demand"):
                    assigned_microhubs[column] = pd.Series(dtype="object")
            if save_m3_priority_audit and "M3" in selected_models:
                # Include customers excluded before assignment by the 3 km filter.
                from code.simulation.m3_eligibility import _distances
                eligible_positions = set(eligible_m3["_m3_row_position"].tolist())
                outside = demand_points.loc[~demand_points["_m3_row_position"].isin(eligible_positions)]
                if not outside.empty and not candidate_microhubs.empty:
                    distances = _distances(outside, candidate_microhubs)
                    for pos, (_, customer) in enumerate(outside.iterrows()):
                        nearest = int(np.argmin(distances[pos]))
                        facility = candidate_microhubs.iloc[nearest]
                        m3_priority_rows.append({
                            "customer_row_position": int(customer["_m3_row_position"]),
                            "customer_id": str(customer.get("customer_id", customer.get("Customer_ID", customer.get("ID", customer["_m3_row_position"])))),
                            "priority_rank": None,
                            "packages": float(customer["Demand"]),
                            "microhub_id": str(facility.get("Location", "")),
                            "nearest_eligible_microhub": "",
                            "distance_m": float(distances[pos, nearest]),
                            "assigned_microhub": "",
                            "capacity_before": None,
                            "capacity_after": None,
                            "decision": "rejected",
                            "reason": "outside_microhub_coverage",
                        })
                audit_details_m3_priority = m3_priority_rows
            else:
                audit_details_m3_priority = None
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
            coverage = calculate_microhub_demand_coverage(demand_points, microhubs, microhub_coverage_radius_m)
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
                selected_models=selected_models,
                vehicle_variants=vehicle_variants,
                m3_unserved_rows=m3_unserved_rows,
                m3_fallback_enabled=m3_fallback_enabled,
                m3_fallback_variants=m3_fallback_variants,
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
            if audit_details_m3_priority is not None:
                audit_details["m3_priority_audit"] = audit_details_m3_priority
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
                if result["model_code"] == "M3":
                    result.update(coverage)
                    # M3 applies the Haversine coverage radius as a hard operational constraint.
                    result["microhub_coverage_is_enforced"] = True
                    total_clients = len(demand_points)
                    total_packages = int(demand_points["Demand"].sum())
                    unserved_clients = len(m3_unserved_rows)
                    unserved_packages = sum(float(row["packages"]) for row in m3_unserved_rows)
                    if m3_fallback_enabled and result.get("supply_vehicle_type") in m3_fallback_variants:
                        # Unserved by microhub != undelivered by the hybrid system.
                        fallback_packages = float(result.get("m3_fallback_packages", 0))
                        result["m3_microhub_unserved_packages"] = unserved_packages
                        result["m3_hybrid_served_packages"] = (
                            float(result.get("m3_microhub_packages", 0)) + fallback_packages
                        )
                        result["m3_hybrid_unserved_packages"] = max(
                            0.0, total_packages - result["m3_hybrid_served_packages"]
                        )
                    result.update({
                        "m3_total_clients": total_clients,
                        "m3_served_clients": total_clients - unserved_clients,
                        "m3_unserved_clients": unserved_clients,
                        "m3_total_packages": total_packages,
                        "m3_served_packages": total_packages - unserved_packages,
                        "m3_unserved_packages": unserved_packages,
                        "m3_service_rate_pct": 100 * (total_clients - unserved_clients) / total_clients if total_clients else 0,
                        "m3_package_service_rate_pct": 100 * (total_packages - unserved_packages) / total_packages if total_packages else 0,
                    })
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
                all_audit_details.setdefault(audit_name, []).extend(audit_rows)
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
