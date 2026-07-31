import numpy as np

from code.common.constants import SERVICE_TIME_PER_STOP_MIN

def calculate_routes_matrix_cost(
    matrix: np.ndarray,
    routes: list[list[int]],
) -> float:
    """Calculate the total matrix cost of depot-based routes."""

    total_cost = 0.0

    for route in routes:
        if not route:
            continue

        total_cost += matrix[0, route[0]]

        for current_client, next_client in zip(route, route[1:]):
            total_cost += matrix[current_client, next_client]

        total_cost += matrix[route[-1], 0]

    return float(total_cost)


def calculate_route_durations(
    duration_matrix,
    routes,
    service_time_per_stop=SERVICE_TIME_PER_STOP_MIN,
    route_start_time_per_route_min=0.0,
):

    durations = []

    for route in routes:

        if not route:
            continue

        driving = duration_matrix[0, route[0]]

        for a, b in zip(route, route[1:]):
            driving += duration_matrix[a, b]

        driving += duration_matrix[route[-1], 0]

        service = len(route) * service_time_per_stop
        route_start = float(route_start_time_per_route_min)

        durations.append(float(route_start + driving + service))

    return durations



def calculate_route_loads(
    routes: list[list[int]],
    client_demands,
) -> list[float]:
    """Return the package load carried by each route."""

    demands = np.asarray(client_demands, dtype=float)
    return [
        float(sum(demands[client_index - 1] for client_index in route))
        for route in routes
    ]


def calculate_radial_distance(matrix, n_clients: int):
    """Sum independent depot-client-depot trips on an asymmetric matrix."""

    return sum(
        matrix[0, i] + matrix[i, 0]
        for i in range(1, n_clients + 1)
    )
