from datetime import datetime, timedelta
from time import perf_counter

import numpy as np
import pandas as pd

from code.common.constants import MAX_ROUTE_DURATION_MIN, SERVICE_TIME_PER_STOP_MIN, TRUCK_LOADING_TIME_PER_ROUTE_MIN
from code.common.routing_utils import calculate_route_durations, calculate_route_loads, calculate_routes_matrix_cost
from code.routing.config import RoutingAlgorithmConfig
from code.routing.cws import clarke_wright_savings
from code.routing.ils import iterated_local_search
from code.routing.osrm_client import get_osrm_host, osrm_distance_duration_table, osrm_table
from code.routing.osrm_validation import (
    sanitize_osrm_routing_inputs,
    validate_osrm_matrices,
)
from code.routing.route_plan import OsrmRoutePlan
from code.common.data_utils import validate_required_columns
from code.simulation.traffic import TrafficProfile, apply_traffic_profile
from code.traffic.provider import TimeTrafficProvider
from code.traffic.timeline import evaluate_route_timeline

class CapacityAwareOsrmRouter:
    """
    Generic OSRM-backed planner for driving, cycling, and walking routes.

    OSRM supplies mode-specific distance and duration matrices. Clarke-Wright
    then creates routes subject to the supplied vehicle or courier capacity.
    """

    def __init__(self, city: str):
        self.city = city
        self._last_matrix_key = None
        self._last_matrices = None

    def get_matrices(
        self,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        coords = [(depot_longitude, depot_latitude)] + list(
            zip(clients["Longitude"], clients["Latitude"])
        )

        print(
            f"\nQuerying OSRM /table for {len(coords)} points "
            f"using {transport_mode}..."
        )

        matrix_key = (
            transport_mode,
            tuple((round(float(lon), 6), round(float(lat), 6)) for lon, lat in coords),
        )

        if matrix_key == self._last_matrix_key and self._last_matrices is not None:
            print(
                "Reusing the previous OSRM distance and duration matrices "
                f"for {transport_mode}."
            )
            return self._last_matrices

        osrm_host = get_osrm_host(self.city, transport_mode)
        distance_matrix, duration_matrix = osrm_distance_duration_table(
            coords,
            host=osrm_host,
            profile=transport_mode,
        )

        self._last_matrix_key = matrix_key
        self._last_matrices = (distance_matrix, duration_matrix)

        print(
            "Distance and duration matrices received from OSRM "
            f"for {transport_mode}."
        )

        return distance_matrix, duration_matrix

    def build_capacity_plan(
        self,
        *,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
        vehicle_capacity: float,
        client_demands=None,
        max_route_duration_min: float | None = MAX_ROUTE_DURATION_MIN,
        max_last_stop_completion_min: float | None = None,
        route_start_time_per_route_min: float = 0.0,
        routing_algorithm: str = "cws",
        cws_allow_route_reversal: bool = False,
        ils_max_iterations: int = 100,
        ils_max_iterations_without_improvement: int | None = 20,
        ils_destruction_percentage_step: float = 10.0,
        ils_max_destruction_percentage: float = 100.0,
        ils_max_full_destruction_attempts: int = 2,
        ils_biased_cws_alpha_min: float = 0.05,
        ils_biased_cws_alpha_max: float = 0.25,
        ils_restricted_relocate: bool = True,
        ils_relocate_candidate_fraction: float = 0.10,
        ils_relocate_neighbor_routes: int = 5,
        ils_relocate_max_insertions: int = 3,
        ils_random_seed: int | None = 42,
        profiling_callback=None,
        traffic_profile: TrafficProfile | None = None,
        time_traffic_provider: TimeTrafficProvider | None = None,
        traffic_zone: str | None = None,
        shift_start: datetime | None = None,
        shift_end: datetime | None = None,
        show_progress: bool = False,
        exclude_unroutable_clients: bool = False,
    ) -> OsrmRoutePlan:
        distance_matrix, duration_matrix = self.get_matrices(
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            clients=clients,
            transport_mode=transport_mode,
        )

        original_clients = clients.reset_index(drop=True).copy()
        original_demands = (
            np.ones(len(original_clients), dtype=float)
            if client_demands is None
            else np.asarray(client_demands, dtype=float)
        )

        if exclude_unroutable_clients:
            (
                distance_matrix,
                duration_matrix,
                clients,
                client_demands,
                unroutable_client_positions,
                unroutable_clients,
            ) = sanitize_osrm_routing_inputs(
                distance_matrix,
                duration_matrix,
                clients=original_clients,
                client_demands=original_demands,
                transport_mode=transport_mode,
            )
        else:
            validate_osrm_matrices(
                distance_matrix,
                duration_matrix,
                clients=original_clients,
                transport_mode=transport_mode,
            )
            clients = original_clients
            client_demands = original_demands
            unroutable_client_positions = []
            unroutable_clients = original_clients.iloc[0:0].copy()

        unroutable_package_count = float(
            original_demands[unroutable_client_positions].sum()
            if unroutable_client_positions
            else 0.0
        )
        id_column = next(
            (
                column
                for column in ("customer_id", "Customer_ID", "ID", "id")
                if column in unroutable_clients.columns
            ),
            None,
        )
        unroutable_customer_ids = (
            unroutable_clients[id_column].astype(str).tolist()
            if id_column is not None
            else []
        )

        # If clients were excluded, cache the already-sanitized reduced matrix
        # under its own coordinate key. M2 uses the same depot and filtered
        # demand, so it can reuse this matrix instead of making another large
        # OSRM /table request.
        if unroutable_client_positions:
            filtered_coords = [(depot_longitude, depot_latitude)] + list(
                zip(clients["Longitude"], clients["Latitude"])
            )
            self._last_matrix_key = (
                transport_mode,
                tuple(
                    (round(float(lon), 6), round(float(lat), 6))
                    for lon, lat in filtered_coords
                ),
            )
            self._last_matrices = (distance_matrix, duration_matrix)

        print(
            f"Starting routing preparation for {len(clients)} clients "
            f"using {routing_algorithm.upper()}..."
        )

        preparation_start = perf_counter()
        
        base_duration_matrix = np.asarray(duration_matrix, dtype=float).copy()

        if transport_mode == "driving":
            effective_traffic_profile = (
                traffic_profile
                if traffic_profile is not None
                else TrafficProfile(
                    name="baseline",
                    duration_multiplier=1.0,
                    source="baseline_osrm",
                )
            )
        else:
            effective_traffic_profile = TrafficProfile(
                name="not_applied",
                duration_multiplier=1.0,
                source="traffic_not_applied_to_non_driving_mode",
            )

        duration_matrix = apply_traffic_profile(
            duration_matrix,
            effective_traffic_profile,
        )
        preparation_seconds = perf_counter() - preparation_start
        print(
            f"Routing preparation completed in "
            f"{preparation_seconds:.2f} seconds."
        )
        

        if routing_algorithm == "cws":
            algorithm_start = perf_counter()
            total_distance_km, routes = clarke_wright_savings(
                matrix=distance_matrix,
                n_clients=len(clients),
                vehicle_capacity=vehicle_capacity,
                client_demands=client_demands,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                max_last_stop_completion_min=max_last_stop_completion_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
                show_progress=show_progress,
                allow_route_reversal=cws_allow_route_reversal,
            )
            routing_runtime_seconds = perf_counter() - algorithm_start
            initial_distance_km = float(total_distance_km)
            cws_initial_distance_km = float(total_distance_km)
            cws_initial_route_count = len(routes)
            cws_runtime_seconds = float(routing_runtime_seconds)
            ils_iterations_completed = 0
            ils_iterations_without_improvement_final = 0

        elif routing_algorithm == "ils":
            print("Building reference CWS solution for ILS comparison...")
            reference_cws_start = perf_counter()

            initial_distance_km, reference_cws_routes = clarke_wright_savings(
                matrix=distance_matrix,
                n_clients=len(clients),
                vehicle_capacity=vehicle_capacity,
                client_demands=client_demands,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                max_last_stop_completion_min=max_last_stop_completion_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
                show_progress=show_progress,
                allow_route_reversal=cws_allow_route_reversal,
            )

            reference_cws_seconds = perf_counter() - reference_cws_start

            print(
                f"Reference CWS solution completed in "
                f"{reference_cws_seconds:.2f} seconds."
            )

            print("Starting ILS optimization...")
            algorithm_start = perf_counter()

            (
                total_distance_km,
                routes,
                ils_iterations_completed,
                ils_iterations_without_improvement_final,
            ) = iterated_local_search(
                matrix=distance_matrix,
                n_clients=len(clients),
                vehicle_capacity=vehicle_capacity,
                client_demands=client_demands,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                max_last_stop_completion_min=max_last_stop_completion_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
                max_iterations=ils_max_iterations,
                max_iterations_without_improvement=(
                    ils_max_iterations_without_improvement
                ),
                destruction_percentage_step=ils_destruction_percentage_step,
                max_destruction_percentage=ils_max_destruction_percentage,
                max_full_destruction_attempts=(
                    ils_max_full_destruction_attempts
                ),
                biased_cws_alpha_min=ils_biased_cws_alpha_min,
                biased_cws_alpha_max=ils_biased_cws_alpha_max,
                restricted_relocate=ils_restricted_relocate,
                relocate_candidate_fraction=ils_relocate_candidate_fraction,
                relocate_neighbor_routes=ils_relocate_neighbor_routes,
                relocate_max_insertions=ils_relocate_max_insertions,
                random_seed=ils_random_seed,
                show_progress=show_progress,
                cws_allow_route_reversal=cws_allow_route_reversal,
                return_stats=True,
                profiling_callback=profiling_callback,
            )
            routing_runtime_seconds = perf_counter() - algorithm_start
            cws_initial_distance_km = float(initial_distance_km)
            cws_initial_route_count = len(reference_cws_routes)
            cws_runtime_seconds = float(reference_cws_seconds)

        else:
            raise ValueError(
                "Unsupported routing algorithm: "
                f"{routing_algorithm!r}. Expected 'cws' or 'ils'."
            )

        initial_distance_km = float(initial_distance_km)
        total_distance_km = float(total_distance_km)
        improvement_distance_km = max(
            0.0,
            initial_distance_km - total_distance_km,
        )
        improvement_percent = (
            100.0 * improvement_distance_km / initial_distance_km
            if initial_distance_km > 0.0
            else 0.0
        )

        print(
            f"Routing metrics [{routing_algorithm.upper()} | {transport_mode}]: "
            f"initial={initial_distance_km:.3f} km | "
            f"final={total_distance_km:.3f} km | "
            f"improvement={improvement_distance_km:.3f} km "
            f"({improvement_percent:.2f}%) | "
            f"runtime={routing_runtime_seconds:.4f} s"
        )
            
        route_durations = calculate_route_durations(
            duration_matrix,
            routes,
            route_start_time_per_route_min=route_start_time_per_route_min,
        )

        route_timelines = []
        if transport_mode == "driving" and time_traffic_provider is not None:
            if shift_start is None or shift_end is None:
                raise ValueError(
                    "shift_start and shift_end are required when a "
                    "time-dependent traffic provider is used."
                )
            route_timelines = [
                evaluate_route_timeline(
                    route=route,
                    duration_matrix=base_duration_matrix,
                    route_start=shift_start,
                    shift_end=shift_end,
                    traffic_provider=time_traffic_provider,
                    traffic_zone=traffic_zone,
                    service_time_per_stop_min=SERVICE_TIME_PER_STOP_MIN,
                    route_preparation_time_min=route_start_time_per_route_min,
                )
                for route in routes
            ]
            if (
                max_last_stop_completion_min is not None
                and shift_start is not None
            ):
                actual_cutoff = shift_start + timedelta(
                    minutes=float(max_last_stop_completion_min)
                )
                late_route_count = 0
                latest_service_completion = None

                for timeline in route_timelines:
                    non_depot_segments = [
                        segment
                        for segment in timeline.segments
                        if segment.destination_index != 0
                    ]
                    if not non_depot_segments:
                        continue

                    last_delivery_segment = non_depot_segments[-1]
                    service_completion = (
                        last_delivery_segment.arrival_datetime
                        + timedelta(minutes=float(SERVICE_TIME_PER_STOP_MIN))
                    )
                    if (
                        latest_service_completion is None
                        or service_completion > latest_service_completion
                    ):
                        latest_service_completion = service_completion

                    if service_completion > actual_cutoff:
                        late_route_count += 1

                if late_route_count:
                    print(
                        "WARNING [last-service deadline]: "
                        f"{late_route_count} route(s) exceed the configured "
                        "last-service cutoff after time-dependent traffic "
                        "evaluation. Latest service completion="
                        f"{latest_service_completion.isoformat(timespec='minutes')}."
                    )
                else:
                    print(
                        "Last-service deadline audit: all route services "
                        f"finish by {actual_cutoff.isoformat(timespec='minutes')}."
                    )

            route_durations = [
                timeline.total_duration_min for timeline in route_timelines
            ]

        total_duration_min = sum(route_durations)
        route_distances = [
            calculate_routes_matrix_cost(distance_matrix, [route])
            for route in routes
        ]
        demands = (
            np.ones(len(clients), dtype=float)
            if client_demands is None
            else np.asarray(client_demands, dtype=float)
        )
        route_loads = calculate_route_loads(routes, demands)

        service_time = (
            len(clients) * SERVICE_TIME_PER_STOP_MIN
            + len(routes) * float(route_start_time_per_route_min)
        )

        return OsrmRoutePlan(
            transport_mode=transport_mode,
            vehicle_capacity=float(vehicle_capacity),
            routes=routes,
            total_distance_km=total_distance_km,
            total_duration_min=total_duration_min,
            route_durations_min=route_durations,
            route_distances_km=route_distances,
            route_loads=route_loads,
            route_start_time_per_route_min=float(route_start_time_per_route_min),
            service_time_min=service_time,
            routing_algorithm=routing_algorithm,
            cws_allow_route_reversal=bool(cws_allow_route_reversal),
            cws_initial_distance_km=float(cws_initial_distance_km),
            cws_initial_route_count=int(cws_initial_route_count),
            cws_runtime_seconds=float(cws_runtime_seconds),
            ils_final_distance_km=(total_distance_km if routing_algorithm == "ils" else 0.0),
            ils_final_route_count=(len(routes) if routing_algorithm == "ils" else 0),
            ils_improvement_km=(improvement_distance_km if routing_algorithm == "ils" else 0.0),
            ils_improvement_percent=(improvement_percent if routing_algorithm == "ils" else 0.0),
            ils_runtime_seconds=(float(routing_runtime_seconds) if routing_algorithm == "ils" else 0.0),
            ils_iterations_completed=int(ils_iterations_completed),
            ils_iterations_without_improvement=int(ils_iterations_without_improvement_final),
            unroutable_customer_count=len(unroutable_client_positions),
            unroutable_package_count=unroutable_package_count,
            unroutable_client_positions=list(unroutable_client_positions),
            unroutable_customer_ids=unroutable_customer_ids,
            routing_runtime_seconds=float(routing_runtime_seconds),
            initial_distance_km=initial_distance_km,
            improvement_distance_km=improvement_distance_km,
            improvement_percent=improvement_percent,
            traffic_profile=effective_traffic_profile.name,
            traffic_duration_multiplier=(
                effective_traffic_profile.duration_multiplier
            ),
            traffic_source=effective_traffic_profile.source,
            time_traffic_profile=(
                time_traffic_provider.profile_name
                if route_timelines
                else "not_applied"
            ),
            traffic_zone=(traffic_zone or "all") if route_timelines else "",
            simulation_day_of_week=(
                shift_start.strftime("%A").lower()
                if route_timelines
                else ""
            ),
            simulation_date=(shift_start.date().isoformat() if route_timelines else ""),
            shift_start_time=(shift_start.time().isoformat(timespec="minutes") if route_timelines else ""),
            shift_end_time=(shift_end.time().isoformat(timespec="minutes") if route_timelines else ""),
            route_start_datetimes=[
                timeline.route_start.isoformat(timespec="minutes")
                for timeline in route_timelines
            ],
            route_end_datetimes=[
                timeline.route_end.isoformat(timespec="minutes")
                for timeline in route_timelines
            ],
            route_base_travel_times_min=[
                timeline.base_travel_time_min for timeline in route_timelines
            ],
            route_adjusted_travel_times_min=[
                timeline.adjusted_travel_time_min for timeline in route_timelines
            ],
            route_traffic_delays_min=[
                timeline.traffic_delay_min for timeline in route_timelines
            ],
            route_shift_feasible=[
                timeline.shift_feasible for timeline in route_timelines
            ],
        )

    def build_independent_round_trips(
        self,
        *,
        depot_latitude: float,
        depot_longitude: float,
        clients: pd.DataFrame,
        transport_mode: str,
        trip_weights=None,
    ) -> tuple[float, float]:
        distance_matrix, duration_matrix = self.get_matrices(
            depot_latitude=depot_latitude,
            depot_longitude=depot_longitude,
            clients=clients,
            transport_mode=transport_mode,
        )
        validate_osrm_matrices(
            distance_matrix,
            duration_matrix,
            clients=clients,
            transport_mode=transport_mode,
        )

        client_count = len(clients)
        if trip_weights is None:
            weights = np.ones(client_count, dtype=float)
        else:
            weights = np.asarray(trip_weights, dtype=float)
            if weights.shape != (client_count,):
                raise ValueError("trip_weights must contain one value per client.")

        distance = sum(
            (distance_matrix[0, i] + distance_matrix[i, 0]) * weights[i - 1]
            for i in range(1, client_count + 1)
        )
        duration = sum(
            (duration_matrix[0, i] + duration_matrix[i, 0]) * weights[i - 1]
            for i in range(1, client_count + 1)
        )
        return float(distance), float(duration)
    
def calculate_facility_supply_route(
    city: str,
    selected_cc: pd.Series,
    used_facilities: pd.DataFrame,
    truck_capacity: float,
    facility_label: str,
    max_route_duration_min: float = MAX_ROUTE_DURATION_MIN,
    *,
    routing_config: RoutingAlgorithmConfig,
    traffic_profile: TrafficProfile,
    time_traffic_provider: TimeTrafficProvider | None = None,
    traffic_zone: str | None = None,
    shift_start: datetime | None = None,
    shift_end: datetime | None = None,
    show_progress: bool = False,
) -> tuple[OsrmRoutePlan, pd.DataFrame, dict[str, datetime]]:
    """Route logistics-center supply to every used facility.

    Facility demand is split into visits no larger than truck capacity.
    Each CWS route represents a truck tour that leaves the logistics center
    once and returns once.
    """

    if used_facilities.empty:
        return OsrmRoutePlan(
            transport_mode="driving",
            vehicle_capacity=float(truck_capacity),
            routes=[],
            total_distance_km=0.0,
            total_duration_min=0.0,
            route_durations_min=[],
            route_distances_km=[],
            route_loads=[],
            route_start_time_per_route_min=TRUCK_LOADING_TIME_PER_ROUTE_MIN,
            service_time_min=0.0,
            routing_algorithm=routing_config.algorithm,
            routing_runtime_seconds=0.0,
            initial_distance_km=0.0,
            improvement_distance_km=0.0,
            improvement_percent=0.0,
            traffic_profile=traffic_profile.name,
            traffic_duration_multiplier=traffic_profile.duration_multiplier,
            traffic_source=traffic_profile.source,
        ), pd.DataFrame(), {}

    supply_visits = expand_facility_supply_visits(
        used_facilities,
        truck_capacity,
    )

    print(
        f"{facility_label} supply: {len(used_facilities)} used facilities, "
        f"{len(supply_visits)} capacity-feasible visits"
    )

    max_last_stop_completion_min = None
    supply_cutoff: datetime | None = None

    if routing_config.last_service_deadline_enabled:
        if shift_start is None or shift_end is None:
            raise ValueError(
                "shift_start and shift_end are required when the supply "
                "arrival deadline is enabled."
            )

        margin_min = float(routing_config.last_service_margin_min)
        shift_duration_min = (shift_end - shift_start).total_seconds() / 60.0
        if margin_min < 0 or margin_min >= shift_duration_min:
            raise ValueError(
                "last_service_margin_min must be non-negative and smaller "
                "than the configured shift duration."
            )

        max_last_stop_completion_min = shift_duration_min - margin_min
        supply_cutoff = shift_end - timedelta(minutes=margin_min)
        print(
            f"{facility_label} last-service deadline: "
            f"facility service cutoff="
            f"{supply_cutoff.isoformat(timespec='minutes')} "
            f"(margin={margin_min:.1f} min)."
        )

    router = CapacityAwareOsrmRouter(city)
    plan = router.build_capacity_plan(
        depot_latitude=float(selected_cc["Latitude"]),
        depot_longitude=float(selected_cc["Longitude"]),
        clients=supply_visits[["Latitude", "Longitude"]],
        transport_mode="driving",
        vehicle_capacity=truck_capacity,
        client_demands=supply_visits["Demand"].to_numpy(dtype=float),
        max_route_duration_min=max_route_duration_min,
        max_last_stop_completion_min=max_last_stop_completion_min,
        route_start_time_per_route_min=TRUCK_LOADING_TIME_PER_ROUTE_MIN,
        routing_algorithm=routing_config.algorithm,
        cws_allow_route_reversal=routing_config.cws_allow_route_reversal,
        ils_max_iterations=routing_config.ils_max_iterations,
        ils_max_iterations_without_improvement=(
            routing_config.ils_max_iterations_without_improvement
        ),
        ils_destruction_percentage_step=routing_config.ils_destruction_percentage_step,
        ils_max_destruction_percentage=routing_config.ils_max_destruction_percentage,
        ils_max_full_destruction_attempts=(
            routing_config.ils_max_full_destruction_attempts
        ),
        ils_biased_cws_alpha_min=routing_config.ils_biased_cws_alpha_min,
        ils_biased_cws_alpha_max=routing_config.ils_biased_cws_alpha_max,
        ils_restricted_relocate=routing_config.ils_restricted_relocate,
        ils_relocate_candidate_fraction=routing_config.ils_relocate_candidate_fraction,
        ils_relocate_neighbor_routes=routing_config.ils_relocate_neighbor_routes,
        ils_relocate_max_insertions=routing_config.ils_relocate_max_insertions,
        ils_random_seed=routing_config.ils_random_seed,
        traffic_profile=traffic_profile,
        time_traffic_provider=time_traffic_provider,
        traffic_zone=traffic_zone,
        shift_start=shift_start,
        shift_end=shift_end,
        show_progress=show_progress,
    )

    print(
        f"{facility_label} supply plan: {plan.route_count} truck routes, "
        f"{plan.total_distance_km:.2f} km, "
        f"{plan.total_duration_min:.2f} total min"
    )

    # Determine when every facility has received all of its supply visits.
    # This is used to start downstream last-mile operations only after the
    # required parcels are physically available at the facility.
    facility_available_at: dict[str, datetime] = {}

    if (
        time_traffic_provider is not None
        and shift_start is not None
        and shift_end is not None
        and plan.routes
    ):
        # Reuse the matrix already cached by this router; this does not issue
        # another OSRM request for the same coordinates.
        _, base_duration_matrix = router.get_matrices(
            depot_latitude=float(selected_cc["Latitude"]),
            depot_longitude=float(selected_cc["Longitude"]),
            clients=supply_visits[["Latitude", "Longitude"]],
            transport_mode="driving",
        )

        for route in plan.routes:
            timeline = evaluate_route_timeline(
                route=route,
                duration_matrix=base_duration_matrix,
                route_start=shift_start,
                shift_end=shift_end,
                traffic_provider=time_traffic_provider,
                traffic_zone=traffic_zone,
                service_time_per_stop_min=SERVICE_TIME_PER_STOP_MIN,
                route_preparation_time_min=TRUCK_LOADING_TIME_PER_ROUTE_MIN,
            )

            for segment in timeline.segments:
                if segment.destination_index == 0:
                    continue

                visit = supply_visits.iloc[segment.destination_index - 1]
                facility_name = str(visit["Location"])

                # Parcels are considered available once the stop service /
                # unloading operation has completed.
                available_at = segment.arrival_datetime + timedelta(
                    minutes=float(SERVICE_TIME_PER_STOP_MIN)
                )

                previous = facility_available_at.get(facility_name)
                if previous is None or available_at > previous:
                    facility_available_at[facility_name] = available_at

        if supply_cutoff is not None:
            late_facilities = {
                name: available_at
                for name, available_at in facility_available_at.items()
                if available_at > supply_cutoff
            }
            if late_facilities:
                latest_name, latest_time = max(
                    late_facilities.items(),
                    key=lambda item: item[1],
                )
                print(
                    f"WARNING [{facility_label} last-service deadline]: "
                    f"{len(late_facilities)} facilities exceed the cutoff "
                    "after time-dependent traffic evaluation. "
                    f"Latest={latest_name!r} at "
                    f"{latest_time.isoformat(timespec='minutes')}."
                )
            else:
                print(
                    f"{facility_label} last-service deadline audit: "
                    f"all {len(facility_available_at)} facilities are "
                    "available within the configured cutoff."
                )

    return plan, supply_visits, facility_available_at

def expand_facility_supply_visits(
    facilities: pd.DataFrame,
    vehicle_capacity: float,
) -> pd.DataFrame:
    """
    Split each facility demand into vehicle-sized delivery visits.

    Every resulting row is one visit that can be assigned to one route.
    A route still leaves the logistics center once and returns once; repeated
    facility coordinates represent separate vehicles when demand exceeds
    capacity.
    """

    validate_required_columns(
        facilities,
        {"Location", "Latitude", "Longitude", "Demand"},
        "facility supply summary",
    )

    vehicle_capacity = float(vehicle_capacity)

    if vehicle_capacity <= 0:
        raise ValueError("Vehicle capacity must be greater than zero.")

    expanded_rows = []

    for facility in facilities.itertuples(index=False):
        remaining_demand = float(facility.Demand)
        visit_number = 1

        if remaining_demand <= 0:
            continue

        while remaining_demand > 0:
            visit_demand = min(remaining_demand, vehicle_capacity)

            expanded_rows.append(
                {
                    "Location": facility.Location,
                    "Latitude": float(facility.Latitude),
                    "Longitude": float(facility.Longitude),
                    "Demand": visit_demand,
                    "SupplyVisit": visit_number,
                }
            )

            remaining_demand -= visit_demand
            visit_number += 1

    return pd.DataFrame(
        expanded_rows,
        columns=[
            "Location",
            "Latitude",
            "Longitude",
            "Demand",
            "SupplyVisit",
        ],
    )


def select_logistics_center(
    centers: pd.DataFrame,
    last_mile_lat: float,
    last_mile_lon: float,
    *,
    osrm_host: str,
    osrm_profile: str,
):
    """
    Select the logistics center with the shortest bidirectional trunk route.

    Coordinate index 0 is the last-mile point. Indices 1..n are the
    logistics centers, so one OSRM table request provides both directions.
    """

    centers = centers.copy()

    coords = [(last_mile_lon, last_mile_lat)] + list(
        zip(centers["Longitude"], centers["Latitude"])
    )

    distance_matrix = osrm_table(
        coords,
        host=osrm_host,
        profile=osrm_profile,
    )
    center_indices = np.arange(1, len(centers) + 1)

    centers["distancia_troncal_ida_km"] = distance_matrix[center_indices, 0]
    centers["distancia_troncal_regreso_km"] = distance_matrix[0, center_indices]
    centers["distancia_troncal_total_km"] = (
        centers["distancia_troncal_ida_km"]
        + centers["distancia_troncal_regreso_km"]
    )

    reachable_centers = centers[
        np.isfinite(centers["distancia_troncal_total_km"])
    ]

    if reachable_centers.empty:
        raise RuntimeError(
            "OSRM could not find a bidirectional route between any logistics "
            "center and the last-mile point."
        )

    return reachable_centers.loc[
        reachable_centers["distancia_troncal_total_km"].idxmin()
    ]
