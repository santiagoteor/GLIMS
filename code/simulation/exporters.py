import pandas as pd
from code.routing.osrm_client import osrm_route_geometry, get_osrm_host
from code.common.constants import SERVICE_TIME_PER_STOP_MIN
from code.routing.route_plan import OsrmRoutePlan


VEHICLE_OSRM_PROFILE = {
    "conventional_van": "driving",
    "electric_van": "driving",
    "cargo_bike": "cycling",
    "walking_courier": "walking",
    "customer_walking": "walking",
}

def build_route_detail_rows(
    *,
    city: str,
    neighborhood_name: str,
    model_code: str,
    leg: str,
    vehicle_type: str,
    depot_name: str,
    plan: "OsrmRoutePlan",
    clients: pd.DataFrame,
    stop_label_column: str | None = None,
    depot_latitude: float | None = None,
    depot_longitude: float | None = None,
    add_geometry: bool = False,
) -> list[dict]:
    """Convert a route plan into one auditable CSV record per route."""

    rows = []
    normalized_clients = clients.reset_index(drop=True)

    profile = VEHICLE_OSRM_PROFILE.get(vehicle_type, "driving")
    geometry_enabled = (
        add_geometry
        and depot_latitude is not None
        and depot_longitude is not None
    )

    for route_number, route in enumerate(plan.routes, start=1):
        client_rows = normalized_clients.iloc[
            [client_index - 1 for client_index in route]
        ]

        if stop_label_column and stop_label_column in client_rows.columns:
            stop_labels = client_rows[stop_label_column].astype(str).tolist()
        elif "customer_id" in client_rows.columns:
            stop_labels = client_rows["customer_id"].astype(str).tolist()
        else:
            stop_labels = [str(client_index) for client_index in route]

        geometry_wkt = ""
        if geometry_enabled:
            stop_coords = list(
                zip(
                    client_rows["Longitude"].astype(float),
                    client_rows["Latitude"].astype(float),
                )
            )
            ordered = [
                (depot_longitude, depot_latitude),
                *stop_coords,
                (depot_longitude, depot_latitude),
            ]
            try:
                line = osrm_route_geometry(
                    ordered,
                    host=get_osrm_host(city, profile),
                    profile=profile,
                )
                geometry_wkt = (
                    "LINESTRING ("
                    + ", ".join(f"{lon} {lat}" for lon, lat in line)
                    + ")"
                )
            except Exception as exc:
                print(
                    f"[aviso] geometría OSRM falló en "
                    f"{model_code}/{leg} ruta {route_number}: {exc}"
                )
                geometry_wkt = ""

        rows.append(
            {
                "city": city,
                "neighborhood": neighborhood_name,
                "model": model_code,
                "leg": leg,
                "route_id": (
                    f"{model_code}_{neighborhood_name}_{leg}_"
                    f"{depot_name}_{route_number}"
                ),
                "vehicle_type": vehicle_type,
                "depot": depot_name,
                "route_number": route_number,
                "stop_count": len(route),
                "package_load": plan.route_loads[route_number - 1],
                "vehicle_capacity": plan.vehicle_capacity,
                "distance_km": plan.route_distances_km[route_number - 1],
                "duration_min": plan.route_durations_min[route_number - 1],
                "start_handling_min": plan.route_start_time_per_route_min,
                "stop_service_min": len(route) * SERVICE_TIME_PER_STOP_MIN,
                "routing_algorithm": plan.routing_algorithm,
                "routing_runtime_seconds": plan.routing_runtime_seconds,
                "initial_distance_km": plan.initial_distance_km,
                "improvement_distance_km": plan.improvement_distance_km,
                "improvement_percent": plan.improvement_percent,
                "cws_allow_route_reversal": plan.cws_allow_route_reversal,
                "cws_initial_distance_km": plan.cws_initial_distance_km,
                "cws_initial_route_count": plan.cws_initial_route_count,
                "cws_runtime_seconds": plan.cws_runtime_seconds,
                "ils_final_distance_km": plan.ils_final_distance_km,
                "ils_final_route_count": plan.ils_final_route_count,
                "ils_improvement_km": plan.ils_improvement_km,
                "ils_improvement_percent": plan.ils_improvement_percent,
                "ils_runtime_seconds": plan.ils_runtime_seconds,
                "ils_iterations_completed": plan.ils_iterations_completed,
                "ils_iterations_without_improvement": (
                    plan.ils_iterations_without_improvement
                ),
                "traffic_profile": plan.traffic_profile,
                "traffic_duration_multiplier": (
                    plan.traffic_duration_multiplier
                ),
                "traffic_source": plan.traffic_source,
                "time_traffic_profile": plan.time_traffic_profile,
                "simulation_date": plan.simulation_date,
                "shift_start_time": plan.shift_start_time,
                "shift_end_time": plan.shift_end_time,
                "route_start_datetime": (
                    plan.route_start_datetimes[route_number - 1]
                    if len(plan.route_start_datetimes) >= route_number
                    else ""
                ),
                "route_end_datetime": (
                    plan.route_end_datetimes[route_number - 1]
                    if len(plan.route_end_datetimes) >= route_number
                    else ""
                ),
                "base_travel_time_min": (
                    plan.route_base_travel_times_min[route_number - 1]
                    if len(plan.route_base_travel_times_min) >= route_number
                    else plan.route_durations_min[route_number - 1]
                ),
                "time_dependent_travel_time_min": (
                    plan.route_adjusted_travel_times_min[route_number - 1]
                    if len(plan.route_adjusted_travel_times_min) >= route_number
                    else plan.route_durations_min[route_number - 1]
                ),
                "traffic_delay_min": (
                    plan.route_traffic_delays_min[route_number - 1]
                    if len(plan.route_traffic_delays_min) >= route_number
                    else 0.0
                ),
                "shift_feasible": (
                    plan.route_shift_feasible[route_number - 1]
                    if len(plan.route_shift_feasible) >= route_number
                    else True
                ),
                "stop_sequence": " -> ".join(stop_labels),
                "geometry_wkt": geometry_wkt,
            }
        )

    return rows
