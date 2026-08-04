import pandas as pd

from code.common.constants import SERVICE_TIME_PER_STOP_MIN
from code.routing.route_plan import OsrmRoutePlan


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
) -> list[dict]:
    """Convert a route plan into one auditable CSV record per route."""

    rows = []
    normalized_clients = clients.reset_index(drop=True)

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
                "traffic_profile": plan.traffic_profile,
                "traffic_duration_multiplier": (
                    plan.traffic_duration_multiplier
                ),
                "traffic_source": plan.traffic_source,
                "stop_sequence": " -> ".join(stop_labels),
            }
        )

    return rows
