
import numpy as np
from tqdm.auto import tqdm
from code.common.constants import SERVICE_TIME_PER_STOP_MIN
from code.common.routing_utils import calculate_route_durations, calculate_routes_matrix_cost
from time import perf_counter


def clarke_wright_savings(
    matrix: np.ndarray,
    n_clients: int,
    vehicle_capacity: float,
    client_demands=None,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    show_progress: bool = False,
):
    """
    Build capacity- and duration-feasible routes using parallel Clarke-Wright.

    Matrix index 0 is the depot and client indices are 1..n_clients.
    Each returned route represents one vehicle that leaves the depot once
    and returns once. Multiple routes therefore mean multiple vehicles.

    When ``duration_matrix`` and ``max_route_duration_min`` are supplied,
    every complete depot-route-depot tour must fit within the duration limit.
    """

    if n_clients <= 0:
        return 0.0, []

    vehicle_capacity = float(vehicle_capacity)
    route_start_time_per_route_min = float(route_start_time_per_route_min)

    if route_start_time_per_route_min < 0:
        raise ValueError("route_start_time_per_route_min cannot be negative.")

    if vehicle_capacity <= 0:
        raise ValueError("Vehicle capacity must be greater than zero.")

    if client_demands is None:
        demands = np.ones(n_clients, dtype=float)
    else:
        demands = np.asarray(client_demands, dtype=float)

        if demands.shape != (n_clients,):
            raise ValueError(
                "client_demands must contain exactly one value per client."
            )

        if np.any(demands < 0):
            raise ValueError("Client demands cannot be negative.")

    if np.any(demands > vehicle_capacity):
        raise ValueError(
            "At least one client demand exceeds the vehicle capacity."
        )

    duration_limit_enabled = (
        duration_matrix is not None
        and max_route_duration_min is not None
    )

    if duration_limit_enabled:
        duration_matrix = np.asarray(duration_matrix, dtype=float)
        expected_shape = (n_clients + 1, n_clients + 1)

        if duration_matrix.shape != expected_shape:
            raise ValueError(
                "duration_matrix must have shape "
                f"{expected_shape}, got {duration_matrix.shape}."
            )

        max_route_duration_min = float(max_route_duration_min)

        if max_route_duration_min <= 0:
            raise ValueError(
                "max_route_duration_min must be greater than zero."
            )

        infeasible_clients = [
            client
            for client in range(1, n_clients + 1)
            if (
                route_start_time_per_route_min
                + duration_matrix[0, client]
                + duration_matrix[client, 0]
                + SERVICE_TIME_PER_STOP_MIN
                > max_route_duration_min
            )
        ]

        if infeasible_clients:
            raise ValueError(
                "At least one client cannot be served within the route "
                f"duration limit of {max_route_duration_min:.0f} minutes, "
                "even in an independent depot-client-depot route. "
                f"Client indices: {infeasible_clients}"
            )

    routes = {
        client: [client]
        for client in range(1, n_clients + 1)
    }
    route_of = {
        client: client
        for client in range(1, n_clients + 1)
    }
    route_loads = {
        client: float(demands[client - 1])
        for client in range(1, n_clients + 1)
    }

    savings = []

    print(
        f"Generating directed CWS savings for "
        f"{n_clients} clients "
        f"({n_clients * (n_clients - 1):,} candidate pairs)..."
    )

    savings_start = perf_counter()
    
    # Directed savings support asymmetric OSRM matrices.
    client_indices = tqdm(
        range(1, n_clients + 1),
        desc="CWS savings construction",
        unit="client",
        disable=not show_progress,
        mininterval=0.5,
        leave=False,
    )
    for i in client_indices:
        for j in range(1, n_clients + 1):
            if i == j:
                continue

            saving = (
                matrix[i, 0]
                + matrix[0, j]
                - matrix[i, j]
            )

            if np.isfinite(saving):
                savings.append((saving, i, j))
    savings_generation_seconds = perf_counter() - savings_start

    print(
        f"Generated {len(savings):,} CWS savings in "
        f"{savings_generation_seconds:.2f} seconds."
    )

    print("Sorting CWS savings...")
    sorting_start = perf_counter()
    
    savings.sort(reverse=True, key=lambda item: item[0])
    sorting_seconds = perf_counter() - sorting_start

    print(
        f"CWS savings sorted in {sorting_seconds:.2f} seconds."
    )

    print("Starting CWS route merging...")
    merging_start = perf_counter()
    
    merge_progress = tqdm(
        savings,
        desc="CWS route merging",
        unit="saving",
        disable=not show_progress,
        mininterval=0.5,
        miniters=max(1, len(savings) // 1000),
        leave=False,
    )
    for _saving, i, j in merge_progress:
        route_i_id = route_of[i]
        route_j_id = route_of[j]

        if route_i_id == route_j_id:
            continue

        route_i = routes[route_i_id]
        route_j = routes[route_j_id]

        if route_i[-1] != i or route_j[0] != j:
            continue

        merged_load = route_loads[route_i_id] + route_loads[route_j_id]

        if merged_load > vehicle_capacity:
            continue

        merged_route = route_i + route_j

        if duration_limit_enabled:
            merged_duration = calculate_route_durations(
                duration_matrix,
                [merged_route],
                route_start_time_per_route_min=route_start_time_per_route_min,
            )[0]

            if merged_duration > max_route_duration_min:
                continue

        routes[route_i_id] = merged_route
        route_loads[route_i_id] = merged_load

        del routes[route_j_id]
        del route_loads[route_j_id]

        for client in merged_route:
            route_of[client] = route_i_id

    final_routes = list(routes.values())
    total_cost = calculate_routes_matrix_cost(matrix, final_routes)

    merging_seconds = perf_counter() - merging_start

    print(
        f"CWS route merging completed in "
        f"{merging_seconds:.2f} seconds. "
        f"Final routes: {len(final_routes)}."
    )
    return total_cost, final_routes
