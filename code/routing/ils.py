from __future__ import annotations

import numpy as np
from tqdm.auto import tqdm
from time import perf_counter
from code.routing.cws import clarke_wright_savings
from code.common.routing_utils import (
    calculate_route_durations,
    calculate_routes_matrix_cost,
)

def _is_route_duration_feasible(
    route: list[int],
    duration_matrix: np.ndarray | None,
    max_route_duration_min: float | None,
    route_start_time_per_route_min: float,
) -> bool:
    """Return whether one route satisfies the configured duration limit."""

    if duration_matrix is None or max_route_duration_min is None:
        return True

    route_duration = calculate_route_durations(
        duration_matrix,
        [route],
        route_start_time_per_route_min=route_start_time_per_route_min,
    )[0]

    return route_duration <= max_route_duration_min


def improve_route_two_opt(
    route: list[int],
    matrix: np.ndarray,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[int]]:
    """
    Improve one depot-based route using best-improvement 2-opt.
    The depot is implicit and is not included in ``route``.
    """

    best_route = route.copy()
    best_cost = calculate_routes_matrix_cost(matrix, [best_route])

    if len(best_route) < 3:
        return best_cost, best_route

    improved = True

    while improved:
        improved = False
        iteration_best_route = best_route
        iteration_best_cost = best_cost

        for start_index in range(len(best_route) - 1):
            for end_index in range(start_index + 1, len(best_route)):
                candidate_route = (
                    best_route[:start_index]
                    + list(reversed(best_route[start_index:end_index + 1]))
                    + best_route[end_index + 1:]
                )

                if not _is_route_duration_feasible(
                    candidate_route,
                    duration_matrix,
                    max_route_duration_min,
                    route_start_time_per_route_min,
                ):
                    continue

                candidate_cost = calculate_routes_matrix_cost(
                    matrix,
                    [candidate_route],
                )

                if candidate_cost < iteration_best_cost - 1e-9:
                    iteration_best_route = candidate_route
                    iteration_best_cost = candidate_cost
                    improved = True

        best_route = iteration_best_route
        best_cost = iteration_best_cost

    return best_cost, best_route


def improve_routes_two_opt(
    routes: list[list[int]],
    matrix: np.ndarray,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[list[int]]]:
    """Apply 2-opt independently to every route."""

    improved_routes = []

    for route in routes:
        _, improved_route = improve_route_two_opt(
            route,
            matrix,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
        )
        improved_routes.append(improved_route)

    total_cost = calculate_routes_matrix_cost(matrix, improved_routes)
    return total_cost, improved_routes

def _local_search(
    routes: list[list[int]],
    matrix: np.ndarray,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[list[int]]]:
    
    print(
        f"Starting local search for {len(routes)} routes "
        f"and {sum(len(route) for route in routes)} clients..."
    )
    local_search_start = perf_counter()
    print("Local search: intra-route 2-opt...")
    stage_start = perf_counter()
    final_cost, final_routes = improve_routes_two_opt(
        routes,
        matrix,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )
    print(
        f"Local search stage completed in "
        f"{perf_counter() - stage_start:.2f} seconds."
    )

    print(
        f"Local search completed in "
        f"{perf_counter() - local_search_start:.2f} seconds."
    )
    return final_cost, final_routes


def _select_routes_to_destroy(
    n_routes: int,
    destruction_percentage: float,
    rng: np.random.Generator,
) -> list[int]:
    """Return the indices of the routes selected for destruction."""

    n_to_destroy = round((destruction_percentage / 100.0) * n_routes)
    n_to_destroy = min(max(n_to_destroy, 1), n_routes)

    return rng.choice(
        n_routes,
        size=n_to_destroy,
        replace=False,
    ).tolist()


def destroy_routes(
    routes: list[list[int]],
    destruction_percentage: float,
    rng: np.random.Generator,
) -> tuple[list[list[int]], list[int]]:
    """
    Destroy ``destruction_percentage``% of the routes, chosen at random.
    """

    if not routes:
        return [], []

    destroy_indices = set(
        _select_routes_to_destroy(len(routes), destruction_percentage, rng)
    )

    remaining_routes = [
        route.copy()
        for index, route in enumerate(routes)
        if index not in destroy_indices
    ]

    freed_clients = [
        client
        for index, route in enumerate(routes)
        if index in destroy_indices
        for client in route
    ]

    return remaining_routes, freed_clients


def reconstruct_routes(
    freed_clients: list[int],
    matrix: np.ndarray,
    vehicle_capacity: float,
    client_demands: np.ndarray,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    cws_allow_route_reversal: bool = False,
    show_progress: bool = False,
) -> list[list[int]]:

    if not freed_clients:
        return []

    n_freed = len(freed_clients)
    local_to_original = dict(enumerate(freed_clients, start=1))

    original_indices = [0] + freed_clients
    sub_matrix = matrix[np.ix_(original_indices, original_indices)]
    sub_duration_matrix = (
        duration_matrix[np.ix_(original_indices, original_indices)]
        if duration_matrix is not None
        else None
    )
    sub_demands = np.array(
        [client_demands[client - 1] for client in freed_clients],
        dtype=float,
    )

    _, sub_routes = clarke_wright_savings(
        matrix=sub_matrix,
        n_clients=n_freed,
        vehicle_capacity=vehicle_capacity,
        client_demands=sub_demands,
        duration_matrix=sub_duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        show_progress=show_progress,
        allow_route_reversal=cws_allow_route_reversal,
    )

    return [
        [local_to_original[local_id] for local_id in route]
        for route in sub_routes
    ]


def destruction_reconstruction(
    routes: list[list[int]],
    matrix: np.ndarray,
    vehicle_capacity: float,
    client_demands: np.ndarray,
    destruction_percentage: float,
    rng: np.random.Generator,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    cws_allow_route_reversal: bool = False,
) -> list[list[int]]:

    remaining_routes, freed_clients = destroy_routes(
        routes, destruction_percentage, rng,
    )

    new_routes = reconstruct_routes(
        freed_clients,
        matrix,
        vehicle_capacity,
        client_demands,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        cws_allow_route_reversal=cws_allow_route_reversal,
    )

    return remaining_routes + new_routes


def iterated_local_search(
    matrix: np.ndarray,
    n_clients: int,
    vehicle_capacity: float,
    client_demands=None,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    max_iterations: int = 100,
    max_iterations_without_improvement: int | None = 20,
    destruction_percentage_step: float = 10.0,
    max_destruction_percentage: float = 100.0,
    random_seed: int | None = None,
    show_progress: bool = False,
    cws_allow_route_reversal: bool = False,
    return_stats: bool = False,
) -> tuple[float, list[list[int]]] | tuple[float, list[list[int]], int, int]:
    """
    Build a feasible routing solution using Iterated Local Search.

    Clarke-Wright Savings generates the initial solution. Local search then
    improves it with 2-opt. Perturbation is a destroy-and-rebuild step: a
    growing percentage of routes is removed at random and rebuilt with
    Clarke-Wright Savings on the freed clients.

    A candidate solution is only accepted when it strictly improves the
    current base solution. When it does not, the base solution is kept
    unchanged and the destruction percentage grows for the next attempt,
    resetting after any improvement.
    """

    if max_iterations <= 0:
        raise ValueError("max_iterations must be greater than zero.")

    if (
        max_iterations_without_improvement is not None
        and max_iterations_without_improvement <= 0
    ):
        raise ValueError(
            "max_iterations_without_improvement must be greater than zero "
            "or None."
        )

    if destruction_percentage_step <= 0:
        raise ValueError(
            "destruction_percentage_step must be greater than zero."
        )

    demands = (
        np.ones(n_clients, dtype=float)
        if client_demands is None
        else np.asarray(client_demands, dtype=float)
    )

    if demands.shape != (n_clients,):
        raise ValueError(
            "client_demands must contain exactly one value per client."
        )

    if np.any(demands < 0):
        raise ValueError("Client demands cannot be negative.")

    rng = np.random.default_rng(random_seed)

    _, initial_routes = clarke_wright_savings(
        matrix=matrix,
        n_clients=n_clients,
        vehicle_capacity=vehicle_capacity,
        client_demands=demands,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        show_progress=show_progress,
        allow_route_reversal=cws_allow_route_reversal,
    )

    current_cost, current_routes = _local_search(
        initial_routes,
        matrix,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )

    best_cost = current_cost
    best_routes = [route.copy() for route in current_routes]

    destruction_percentage = 0.0
    iterations_without_improvement = 0

    iteration_progress = tqdm(
        range(max_iterations),
        desc="ILS optimization",
        unit="iteration",
        disable=not show_progress,
        mininterval=0.5,
    )
    iterations_completed = 0
    for _iteration in iteration_progress:
        iterations_completed += 1

        destruction_percentage = min(
            destruction_percentage + destruction_percentage_step,
            max_destruction_percentage,
        )

        perturbed_routes = destruction_reconstruction(
            current_routes,
            matrix,
            vehicle_capacity,
            demands,
            destruction_percentage,
            rng,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
            cws_allow_route_reversal=cws_allow_route_reversal,
        )

        candidate_cost, candidate_routes = _local_search(
            perturbed_routes,
            matrix,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
        )

        if candidate_cost < current_cost - 1e-9:
            current_cost = candidate_cost
            current_routes = [route.copy() for route in candidate_routes]

            destruction_percentage = 0.0
            iterations_without_improvement = 0

            if candidate_cost < best_cost - 1e-9:
                best_cost = candidate_cost
                best_routes = [route.copy() for route in candidate_routes]
        else:
            iterations_without_improvement += 1

        if show_progress:
            iteration_progress.set_postfix(
                best_km=f"{best_cost:.3f}",
                p=f"{destruction_percentage:.0f}%",
                no_improvement=iterations_without_improvement,
            )

        if (
            max_iterations_without_improvement is not None
            and iterations_without_improvement
            >= max_iterations_without_improvement
        ):
            break

    if return_stats:
        return (
            best_cost,
            best_routes,
            iterations_completed,
            iterations_without_improvement,
        )
    return best_cost, best_routes