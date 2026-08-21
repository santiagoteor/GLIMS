import json
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from code.common.constants import BIKE_PREPARATION_TIME_PER_ROUTE_MIN, WALKING_PREPARATION_TIME_PER_ROUTE_MIN
from code.common.data_utils import validate_required_columns
from code.routing.config import RoutingAlgorithmConfig
from code.routing.osrm_router import CapacityAwareOsrmRouter
from code.routing.last_meter import build_last_meter_access_metrics
from code.simulation.exporters import build_route_detail_rows
from code.simulation.zones import group_customers_by_facility


def calculate_customer_collection_travel(
    city: str,
    assigned_pudos: pd.DataFrame,
    neighborhood_name: str,
    *,
    routing_config: RoutingAlgorithmConfig,
) -> tuple[float, float, list[dict]]:
    """
    Calculate one customer round trip to the assigned PUDO.

    Customers assigned to the same PUDO are processed together so that
    only one OSRM table request is required per used facility.
    """

    validate_required_columns(
        assigned_pudos,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Latitude",
            "Longitude",
        },
        "customers assigned to PUDOs",
    )

    if assigned_pudos.empty:
        return 0.0, 0.0, []

    router = CapacityAwareOsrmRouter(city)

    total_distance_km = 0.0
    total_duration_min = 0.0
    detail_rows = []

    pudo_groups = group_customers_by_facility(assigned_pudos)

    for pudo_name, customers in pudo_groups.items():
        pudo_latitude = float(customers["facility_latitude"].iloc[0])
        pudo_longitude = float(customers["facility_longitude"].iloc[0])

        customer_locations = customers[
            ["Latitude", "Longitude"]
        ].reset_index(drop=True)

        distance_matrix, duration_matrix = router.get_matrices(
            depot_latitude=pudo_latitude,
            depot_longitude=pudo_longitude,
            clients=customer_locations,
            transport_mode="walking",
        )
        last_meter_metrics = build_last_meter_access_metrics(
            city=city,
            transport_mode="walking",
            clients=customer_locations,
            enabled=routing_config.last_meter_applies_to("M5"),
            walking_speed_m_s=routing_config.last_meter_walking_speed_m_s,
            round_trip=routing_config.last_meter_round_trip,
        )

        for customer_offset, customer in customers.reset_index(drop=True).iterrows():
            matrix_index = customer_offset + 1
            distance_km = float(
                distance_matrix[0, matrix_index]
                + distance_matrix[matrix_index, 0]
            )
            network_distance_km = distance_km
            network_duration_min = float(
                duration_matrix[0, matrix_index]
                + duration_matrix[matrix_index, 0]
            )
            access_distance_km = float(
                last_meter_metrics.access_distance_m[customer_offset] / 1000.0
            )
            access_time_min = float(
                last_meter_metrics.access_time_min[customer_offset]
            )
            snap_distance_m = float(
                last_meter_metrics.snap_distance_m[customer_offset]
            )
            distance_km = network_distance_km + access_distance_km
            duration_min = network_duration_min + access_time_min
            total_distance_km += distance_km
            total_duration_min += duration_min
            detail_rows.append(
                {
                    "city": city,
                    "neighborhood": neighborhood_name,
                    "model": "M5",
                    "leg": "customer_collection",
                    "route_id": (
                        f"M5_{neighborhood_name}_customer_collection_"
                        f"{len(detail_rows) + 1}"
                    ),
                    "vehicle_type": "customer_walking",
                    "depot": pudo_name,
                    "route_number": len(detail_rows) + 1,
                    "stop_count": 1,
                    "package_load": float(customer["Demand"]),
                    "vehicle_capacity": np.nan,
                    "distance_km": network_distance_km,
                    "network_distance_km": network_distance_km,
                    "last_meter_access_distance_km": access_distance_km,
                    "system_distance_km": distance_km,
                    "duration_min": duration_min,
                    "network_duration_min": network_duration_min,
                    "last_meter_access_time_min": access_time_min,
                    "start_handling_min": 0.0,
                    "base_stop_service_min": 0.0,
                    "stop_service_min": access_time_min,
                    "stop_sequence": str(
                        customer.get("customer_id", customer_offset + 1)
                    ),
                    "_stop_ids": [
                        str(
                            customer.get(
                                "customer_id",
                                customer_offset + 1,
                            )
                        )
                    ],
                    "_stop_latitudes": [
                        float(customer["Latitude"])
                    ],
                    "_stop_longitudes": [
                        float(customer["Longitude"])
                    ],
                    "_stop_package_loads": [
                        float(customer["Demand"])
                    ],
                    "_stop_snap_distances_m": [snap_distance_m],
                    "_stop_last_meter_access_distances_m": [
                        access_distance_km * 1000.0
                    ],
                    "_stop_last_meter_access_times_min": [access_time_min],
                    "_stop_base_service_times_min": [0.0],
                    "_stop_effective_service_times_min": [access_time_min],
                    "_stop_arrival_offsets_min": [
                        float(duration_matrix[0, matrix_index])
                    ],
                    "_stop_service_end_offsets_min": [
                        float(duration_matrix[0, matrix_index] + access_time_min)
                    ],
                }
            )

    return total_distance_km, total_duration_min, detail_rows


def calculate_microhub_last_mile(
    city: str,
    assigned_microhubs: pd.DataFrame,
    bike_capacity: float,
    neighborhood_name: str,
    routing_config: RoutingAlgorithmConfig,
    max_route_duration_min: float | None = None,
    facility_available_at: dict[str, datetime] | None = None,
    shift_end: datetime | None = None,
    performance_rows: list[dict] | None = None,
    add_geometry: bool = False,
    show_progress: bool = False,
) -> tuple[float, float, int, float, list[dict]]:
    """
    Simulate bicycle delivery independently from every used microhub.

    Returns
    -------
    total_distance_km
    total_duration_min
    total_routes
    """

    validate_required_columns(
        assigned_microhubs,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Latitude",
            "Longitude",
            "Demand",
        },
        "assigned microhubs",
    )

    if assigned_microhubs.empty:
        return 0.0, 0.0, 0, 0.0, []

    router = CapacityAwareOsrmRouter(city)

    total_distance = 0.0
    total_duration = 0.0
    total_routes = 0
    max_route_duration = 0.0
    detail_rows = []

    groups = group_customers_by_facility(
        assigned_microhubs
    )

    for microhub_name, customers in groups.items():

        def _m3_profile_callback(record: dict) -> None:
            if performance_rows is None:
                return

            detail_payload = dict(record.get("detail") or {})
            detail_payload.update({
                "microhub": str(microhub_name),
                "microhub_customers": int(len(customers)),
                "microhub_packages": float(customers["Demand"].sum()),
            })

            performance_rows.append({
                "city": city,
                "neighborhood": neighborhood_name,
                "category": "routing_detail",
                "model": "M3",
                "stage": str(record.get("stage", "m3_ils_detail")),
                "seconds": float(record.get("seconds", 0.0)),
                "detail": json.dumps(
                    detail_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            })

        plan = router.build_capacity_plan(
            depot_latitude=float(customers["facility_latitude"].iloc[0]),
            depot_longitude=float(customers["facility_longitude"].iloc[0]),
            clients=customers[["Latitude", "Longitude"]],
            transport_mode="cycling",
            vehicle_capacity=bike_capacity,
            client_demands=customers["Demand"].to_numpy(dtype=float),
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
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
            profiling_callback=_m3_profile_callback,
            show_progress=show_progress,
            last_meter_access_enabled=(
                routing_config.last_meter_applies_to("M3")
            ),
            last_meter_walking_speed_m_s=(
                routing_config.last_meter_walking_speed_m_s
            ),
            last_meter_round_trip=routing_config.last_meter_round_trip,
        )

        total_distance += plan.total_distance_km
        total_duration += plan.total_duration_min
        total_routes += plan.route_count
        max_route_duration = max(
            max_route_duration,
            plan.max_route_duration_min,
        )
        microhub_rows = build_route_detail_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            model_code="M3",
            leg="cycling_last_mile",
            vehicle_type="cargo_bike",
            depot_name=microhub_name,
            plan=plan,
            clients=customers,
            depot_latitude=float(customers["facility_latitude"].iloc[0]),
            depot_longitude=float(customers["facility_longitude"].iloc[0]),
            add_geometry=add_geometry,
        )

        # Cycling is not currently traffic-adjusted, but it is now
        # schedule-aware. Every cargo-bike route can start only when all
        # supply visits required by its microhub have been completed.
        available_at = (
            facility_available_at.get(str(microhub_name))
            if facility_available_at is not None
            else None
        )

        if available_at is not None:
            for row in microhub_rows:
                route_duration = float(row["duration_min"])
                route_end = available_at + timedelta(minutes=route_duration)

                preparation = float(row.get("start_handling_min", 0.0))
                service = float(row.get("stop_service_min", 0.0))
                base_travel = max(
                    0.0,
                    route_duration - preparation - service,
                )

                row["route_start_datetime"] = available_at.isoformat(
                    timespec="minutes"
                )
                row["route_end_datetime"] = route_end.isoformat(
                    timespec="minutes"
                )
                row["base_travel_time_min"] = base_travel
                row["time_dependent_travel_time_min"] = base_travel
                row["traffic_delay_min"] = 0.0
                row["time_traffic_profile"] = "not_applied"
                row["simulation_date"] = available_at.date().isoformat()
                row["shift_start_time"] = available_at.time().isoformat(
                    timespec="minutes"
                )
                if shift_end is not None:
                    row["shift_end_time"] = shift_end.time().isoformat(
                        timespec="minutes"
                    )
                    row["shift_feasible"] = route_end <= shift_end

        detail_rows.extend(microhub_rows)

    return (
        total_distance,
        total_duration,
        total_routes,
        max_route_duration,
        detail_rows,
    )

def calculate_pudo_last_mile(
    city: str,
    assigned_pudos: pd.DataFrame,
    walking_capacity: float,
    neighborhood_name: str,
    routing_config: RoutingAlgorithmConfig,
    max_route_duration_min: float | None = None,
    facility_available_at: dict[str, datetime] | None = None,
    shift_end: datetime | None = None,
    add_geometry: bool = False,
    show_progress: bool = False,
) -> tuple[float, float, int, float, int, list[dict]]:
    """
    Simulate walking delivery routes independently from every used PUDO.

    Returns
    -------
    total_distance_km
    total_duration_min
    total_routes
    max_route_duration_min
    """

    validate_required_columns(
        assigned_pudos,
        {
            "assigned_facility",
            "facility_latitude",
            "facility_longitude",
            "Latitude",
            "Longitude",
            "Demand",
        },
        "assigned PUDOs",
    )

    if assigned_pudos.empty:
        return 0.0, 0.0, 0, 0.0, 0, []

    router = CapacityAwareOsrmRouter(city)

    total_distance = 0.0
    total_duration = 0.0
    total_routes = 0
    max_route_duration = 0.0
    detail_rows = []

    groups = group_customers_by_facility(assigned_pudos)
    used_pudo_count = len(groups)

    for pudo_name, customers in groups.items():
        plan = router.build_capacity_plan(
            depot_latitude=float(
                customers["facility_latitude"].iloc[0]
            ),
            depot_longitude=float(
                customers["facility_longitude"].iloc[0]
            ),
            clients=customers[["Latitude", "Longitude"]],
            transport_mode="walking",
            vehicle_capacity=walking_capacity,
            client_demands=customers["Demand"].to_numpy(dtype=float),
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
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
            show_progress=show_progress,
            last_meter_access_enabled=(
                routing_config.last_meter_applies_to("M4")
            ),
            last_meter_walking_speed_m_s=(
                routing_config.last_meter_walking_speed_m_s
            ),
            last_meter_round_trip=routing_config.last_meter_round_trip,
        )

        total_distance += plan.total_distance_km
        total_duration += plan.total_duration_min
        total_routes += plan.route_count
        max_route_duration = max(
            max_route_duration,
            plan.max_route_duration_min,
        )
        pudo_rows = build_route_detail_rows(
            city=city,
            neighborhood_name=neighborhood_name,
            model_code="M4",
            leg="walking_last_mile",
            vehicle_type="walking_courier",
            depot_name=pudo_name,
            plan=plan,
            clients=customers,
            depot_latitude=float(customers["facility_latitude"].iloc[0]),
            depot_longitude=float(customers["facility_longitude"].iloc[0]),
            add_geometry=add_geometry,
        )

        # Same synchronization rule as M3: the walking operation from a PUDO
        # can start only once all supply visits required by that PUDO have
        # completed. Walking is not traffic-adjusted.
        available_at = (
            facility_available_at.get(str(pudo_name))
            if facility_available_at is not None
            else None
        )

        if available_at is not None:
            for row in pudo_rows:
                route_duration = float(row["duration_min"])
                route_end = available_at + timedelta(minutes=route_duration)

                preparation = float(row.get("start_handling_min", 0.0))
                service = float(row.get("stop_service_min", 0.0))
                base_travel = max(
                    0.0,
                    route_duration - preparation - service,
                )

                row["route_start_datetime"] = available_at.isoformat(
                    timespec="minutes"
                )
                row["route_end_datetime"] = route_end.isoformat(
                    timespec="minutes"
                )
                row["base_travel_time_min"] = base_travel
                row["time_dependent_travel_time_min"] = base_travel
                row["traffic_delay_min"] = 0.0
                row["time_traffic_profile"] = "not_applied"
                row["simulation_date"] = available_at.date().isoformat()
                row["shift_start_time"] = available_at.time().isoformat(
                    timespec="minutes"
                )
                if shift_end is not None:
                    row["shift_end_time"] = shift_end.time().isoformat(
                        timespec="minutes"
                    )
                    row["shift_feasible"] = route_end <= shift_end

        detail_rows.extend(pudo_rows)

    return (
        total_distance,
        total_duration,
        total_routes,
        max_route_duration,
        used_pudo_count,
        detail_rows,
    )
