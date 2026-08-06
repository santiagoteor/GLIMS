from __future__ import annotations

import numpy as np
from tqdm.auto import tqdm

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

def _calculate_route_load(
    route: list[int],
    client_demands: np.ndarray,
) -> float:
    """Return the total demand assigned to one route."""

    return float(
        sum(client_demands[client_index - 1] for client_index in route)
    )


def improve_routes_relocate(
    routes: list[list[int]],
    matrix: np.ndarray,
    vehicle_capacity: float,
    client_demands,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[list[int]]]:
    """
    Improve a routing solution using best-improvement inter-route relocate.

    One client is removed from a source route and inserted into a different
    destination route. Only capacity- and duration-feasible improvements are
    accepted.
    """

    demands = np.asarray(client_demands, dtype=float)

    if demands.ndim != 1:
        raise ValueError("client_demands must be a one-dimensional array.")

    best_routes = [
        route.copy()
        for route in routes
        if route
    ]
    best_cost = calculate_routes_matrix_cost(matrix, best_routes)

    improved = True

    while improved:
        improved = False
        iteration_best_routes = best_routes
        iteration_best_cost = best_cost

        for source_route_index, source_route in enumerate(best_routes):
            for source_position, client in enumerate(source_route):

                reduced_source_route = (
                    source_route[:source_position]
                    + source_route[source_position + 1:]
                )

                for destination_route_index, destination_route in enumerate(
                    best_routes
                ):
                    if destination_route_index == source_route_index:
                        continue

                    destination_load = _calculate_route_load(
                        destination_route,
                        demands,
                    )
                    client_demand = float(demands[client - 1])

                    if destination_load + client_demand > vehicle_capacity:
                        continue

                    for insertion_position in range(
                        len(destination_route) + 1
                    ):
                        expanded_destination_route = (
                            destination_route[:insertion_position]
                            + [client]
                            + destination_route[insertion_position:]
                        )

                        if reduced_source_route and not _is_route_duration_feasible(
                            reduced_source_route,
                            duration_matrix,
                            max_route_duration_min,
                            route_start_time_per_route_min,
                        ):
                            continue

                        if not _is_route_duration_feasible(
                            expanded_destination_route,
                            duration_matrix,
                            max_route_duration_min,
                            route_start_time_per_route_min,
                        ):
                            continue

                        candidate_routes = [
                            route.copy()
                            for route in best_routes
                        ]

                        candidate_routes[source_route_index] = (
                            reduced_source_route
                        )
                        candidate_routes[destination_route_index] = (
                            expanded_destination_route
                        )

                        candidate_routes = [
                            route
                            for route in candidate_routes
                            if route
                        ]

                        candidate_cost = calculate_routes_matrix_cost(
                            matrix,
                            candidate_routes,
                        )

                        if candidate_cost < iteration_best_cost - 1e-9:
                            iteration_best_routes = candidate_routes
                            iteration_best_cost = candidate_cost
                            improved = True

        best_routes = [
            route.copy()
            for route in iteration_best_routes
        ]
        best_cost = iteration_best_cost

    return best_cost, best_routes

def perturb_routes(
    routes: list[list[int]],
    client_demands,
    vehicle_capacity: float,
    rng: np.random.Generator,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    perturbation_moves: int = 2,
    max_attempts_per_move: int = 100,
) -> list[list[int]]:
    """
    Generate a feasible perturbed solution.

    When multiple routes exist, the operator attempts random inter-route
    relocations. With a single route, it reverses a random route segment.
    Perturbations do not need to improve the objective value.
    """

    if perturbation_moves <= 0:
        raise ValueError("perturbation_moves must be greater than zero.")

    demands = np.asarray(client_demands, dtype=float)
    perturbed_routes = [route.copy() for route in routes if route]

    if not perturbed_routes:
        return []

    for _ in range(perturbation_moves):

        # With one route, perturb its internal order.
        if len(perturbed_routes) == 1:
            route = perturbed_routes[0]

            if len(route) < 2:
                continue

            start_index, end_index = sorted(
                rng.choice(len(route), size=2, replace=False)
            )

            candidate_route = (
                route[:start_index]
                + list(reversed(route[start_index:end_index + 1]))
                + route[end_index + 1:]
            )

            if _is_route_duration_feasible(
                candidate_route,
                duration_matrix,
                max_route_duration_min,
                route_start_time_per_route_min,
            ):
                perturbed_routes[0] = candidate_route

            continue

        # With multiple routes, attempt a random inter-route relocation.
        move_completed = False

        for _attempt in range(max_attempts_per_move):
            source_route_index, destination_route_index = rng.choice(
                len(perturbed_routes),
                size=2,
                replace=False,
            )

            source_route = perturbed_routes[source_route_index]
            destination_route = perturbed_routes[destination_route_index]

            if not source_route:
                continue

            source_position = int(rng.integers(len(source_route)))
            client = source_route[source_position]
            client_demand = float(demands[client - 1])

            destination_load = _calculate_route_load(
                destination_route,
                demands,
            )

            if destination_load + client_demand > vehicle_capacity:
                continue

            insertion_position = int(
                rng.integers(len(destination_route) + 1)
            )

            reduced_source_route = (
                source_route[:source_position]
                + source_route[source_position + 1:]
            )

            expanded_destination_route = (
                destination_route[:insertion_position]
                + [client]
                + destination_route[insertion_position:]
            )

            if reduced_source_route and not _is_route_duration_feasible(
                reduced_source_route,
                duration_matrix,
                max_route_duration_min,
                route_start_time_per_route_min,
            ):
                continue

            if not _is_route_duration_feasible(
                expanded_destination_route,
                duration_matrix,
                max_route_duration_min,
                route_start_time_per_route_min,
            ):
                continue

            candidate_routes = [
                route.copy()
                for route in perturbed_routes
            ]

            candidate_routes[source_route_index] = reduced_source_route
            candidate_routes[destination_route_index] = (
                expanded_destination_route
            )

            perturbed_routes = [
                route
                for route in candidate_routes
                if route
            ]

            move_completed = True
            break

        if not move_completed:
            # No feasible random relocation was found for this move.
            continue

    return perturbed_routes

def _local_search(
    routes: list[list[int]],
    matrix: np.ndarray,
    vehicle_capacity: float,
    client_demands: np.ndarray,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[list[int]]]:
    """
    Improve a feasible solution using the configured local-search operators.

    The search applies:
        1. Intra-route 2-opt.
        2. Inter-route relocate.
        3. Intra-route 2-opt again.
    """

    _, two_opt_routes = improve_routes_two_opt(
        routes,
        matrix,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )

    _, relocate_routes = improve_routes_relocate(
        two_opt_routes,
        matrix,
        vehicle_capacity,
        client_demands,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )

    final_cost, final_routes = improve_routes_two_opt(
        relocate_routes,
        matrix,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )

    return final_cost, final_routes


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
    perturbation_moves: int = 2,
    random_seed: int | None = None,
    show_progress: bool = False,
) -> tuple[float, list[list[int]]]:
    """
    Build a feasible routing solution using Iterated Local Search.

    Clarke-Wright Savings generates the initial solution. Local search then
    improves that solution using 2-opt and inter-route relocate operators.
    Repeated perturbations allow the search to explore different regions of
    the solution space.

    Parameters
    ----------
    matrix
        Distance or cost matrix. Index 0 represents the depot.
    n_clients
        Number of clients, indexed from 1 to n_clients.
    vehicle_capacity
        Maximum capacity allowed on each route.
    client_demands
        Demand associated with each client. Unit demand is assumed when this
        argument is omitted.
    duration_matrix
        Travel-time matrix in minutes.
    max_route_duration_min
        Maximum allowed duration for each route.
    route_start_time_per_route_min
        Preparation or loading time added to every route.
    max_iterations
        Maximum number of perturbation and local-search iterations.
    max_iterations_without_improvement
        Stop early after this number of consecutive iterations without
        improving the best solution. Use None to disable early stopping.
    perturbation_moves
        Number of feasible random moves attempted during each perturbation.
    random_seed
        Seed used to obtain reproducible perturbations.

    Returns
    -------
    tuple[float, list[list[int]]]
        Best total routing distance and best feasible routes found.
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

    if perturbation_moves <= 0:
        raise ValueError("perturbation_moves must be greater than zero.")

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

    # Step 1: construct the initial solution with Clarke-Wright.
    _, initial_routes = clarke_wright_savings(
        matrix=matrix,
        n_clients=n_clients,
        vehicle_capacity=vehicle_capacity,
        client_demands=demands,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        show_progress=show_progress,
    )

    # Step 2: move the initial solution to a local optimum.
    current_cost, current_routes = _local_search(
        initial_routes,
        matrix,
        vehicle_capacity,
        demands,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )

    best_cost = current_cost
    best_routes = [route.copy() for route in current_routes]

    iterations_without_improvement = 0

    # Step 3: repeatedly perturb and improve the current solution.
    iteration_progress = tqdm(
        range(max_iterations),
        desc="ILS optimization",
        unit="iteration",
        disable=not show_progress,
        mininterval=0.5,
    )
    for _iteration in iteration_progress:
        perturbed_routes = perturb_routes(
            current_routes,
            demands,
            vehicle_capacity,
            rng,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
            perturbation_moves=perturbation_moves,
        )

        candidate_cost, candidate_routes = _local_search(
            perturbed_routes,
            matrix,
            vehicle_capacity,
            demands,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
        )

        # Classical ILS acceptance: continue searching from the new local
        # optimum, even when it is worse than the best solution found.
        current_cost = candidate_cost
        current_routes = [route.copy() for route in candidate_routes]

        if candidate_cost < best_cost - 1e-9:
            best_cost = candidate_cost
            best_routes = [
                route.copy()
                for route in candidate_routes
            ]
            iterations_without_improvement = 0
        else:
            iterations_without_improvement += 1

        if show_progress:
            iteration_progress.set_postfix(
                best_km=f"{best_cost:.3f}",
                no_improvement=iterations_without_improvement,
            )

        if (
            max_iterations_without_improvement is not None
            and iterations_without_improvement
            >= max_iterations_without_improvement
        ):
            break

    return best_cost, best_routes