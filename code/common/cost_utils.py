def calculate_direct_route_operating_cost(
    *,
    distance_km: float,
    total_duration_min: float,
    route_count: int,
    route_start_time_per_route_min: float,
    cost_per_km: float,
    labor_cost_per_hour: float,
) -> tuple[float, float, float]:
    """Return distance, labour, and total direct route operating costs.

    The economic boundary starts when each route departs its origin. Route
    preparation/loading time is therefore excluded, while OSRM travel time and
    stop service time remain included.
    """

    distance_km = float(distance_km)
    total_duration_min = float(total_duration_min)
    route_count = int(route_count)
    route_start_time_per_route_min = float(route_start_time_per_route_min)

    in_route_duration_min = max(
        0.0,
        total_duration_min
        - route_count * route_start_time_per_route_min,
    )
    distance_cost = distance_km * float(cost_per_km)
    labor_cost = (in_route_duration_min / 60.0) * float(labor_cost_per_hour)

    return distance_cost, labor_cost, distance_cost + labor_cost
