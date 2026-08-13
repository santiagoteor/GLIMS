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
    max_last_stop_completion_min: float | None = None,
) -> bool:
    """Return whether a route satisfies all configured temporal limits."""

    if duration_matrix is None or not route:
        return True
    if max_route_duration_min is None and max_last_stop_completion_min is None:
        return True

    route_duration = calculate_route_durations(
        duration_matrix,
        [route],
        route_start_time_per_route_min=route_start_time_per_route_min,
    )[0]

    if max_route_duration_min is not None and route_duration > max_route_duration_min:
        return False

    if max_last_stop_completion_min is not None:
        last_stop_completion = route_duration - float(duration_matrix[route[-1], 0])
        if last_stop_completion > max_last_stop_completion_min:
            return False

    return True



def _directed_two_opt_delta(
    route: list[int],
    start_index: int,
    end_index: int,
    matrix: np.ndarray,
    reverse_minus_forward_prefix: np.ndarray,
) -> float:
    """Return the exact directed cost delta of one 2-opt reversal in O(1)."""
    first = route[start_index]
    last = route[end_index]
    previous = 0 if start_index == 0 else route[start_index - 1]
    following = 0 if end_index == len(route) - 1 else route[end_index + 1]

    boundary_delta = (
        matrix[previous, last]
        + matrix[first, following]
        - matrix[previous, first]
        - matrix[last, following]
    )

    internal_delta = (
        reverse_minus_forward_prefix[end_index]
        - reverse_minus_forward_prefix[start_index]
    )

    return float(boundary_delta + internal_delta)


def _two_opt_reverse_minus_forward_prefix(
    route: list[int],
    matrix: np.ndarray,
) -> np.ndarray:
    """Prefix of reversed-edge cost minus forward-edge cost."""
    prefix = np.zeros(len(route), dtype=float)
    for edge_index in range(len(route) - 1):
        a = route[edge_index]
        b = route[edge_index + 1]
        prefix[edge_index + 1] = (
            prefix[edge_index]
            + matrix[b, a]
            - matrix[a, b]
        )
    return prefix



def improve_route_two_opt(
    route: list[int],
    matrix: np.ndarray,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[int]]:
    """
    Improve one depot-based route using best-improvement directed 2-opt.

    O(1) directed deltas are used only as a screening step. Any candidate that
    can plausibly improve the route is still reconstructed and evaluated with
    the original exact feasibility and cost functions before acceptance.
    """

    best_route = route.copy()
    best_cost = calculate_routes_matrix_cost(matrix, [best_route])

    if len(best_route) < 3:
        return best_cost, best_route

    improved = True
    screening_margin = 1e-8

    while improved:
        improved = False
        iteration_best_route = best_route
        iteration_best_cost = best_cost

        reverse_minus_forward_prefix = (
            _two_opt_reverse_minus_forward_prefix(best_route, matrix)
        )

        for start_index in range(len(best_route) - 1):
            for end_index in range(start_index + 1, len(best_route)):
                estimated_delta = _directed_two_opt_delta(
                    best_route,
                    start_index,
                    end_index,
                    matrix,
                    reverse_minus_forward_prefix,
                )
                estimated_cost = best_cost + estimated_delta

                # Clearly worse candidates cannot become the best improvement.
                # Borderline cases still go through exact legacy evaluation.
                if estimated_cost >= iteration_best_cost + screening_margin:
                    continue

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
                    max_last_stop_completion_min,
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
    max_last_stop_completion_min: float | None = None,
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
            max_last_stop_completion_min=max_last_stop_completion_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
        )
        improved_routes.append(improved_route)

    total_cost = calculate_routes_matrix_cost(matrix, improved_routes)
    return total_cost, improved_routes


def _route_removal_gain(
    route: list[int],
    position: int,
    matrix: np.ndarray,
) -> float:
    """Distance saved by removing ``route[position]`` from a directed route."""
    client = route[position]
    previous = 0 if position == 0 else route[position - 1]
    following = 0 if position == len(route) - 1 else route[position + 1]
    return float(
        matrix[previous, client]
        + matrix[client, following]
        - matrix[previous, following]
    )


def _route_insertion_delta(
    route: list[int],
    position: int,
    client: int,
    matrix: np.ndarray,
) -> float:
    """Extra directed distance caused by inserting a client at ``position``."""
    previous = 0 if position == 0 else route[position - 1]
    following = 0 if position == len(route) else route[position]
    return float(
        matrix[previous, client]
        + matrix[client, following]
        - matrix[previous, following]
    )


def _route_proximity_score(
    client: int,
    route: list[int],
    matrix: np.ndarray,
) -> float:
    """
    Directed proximity score used only to shortlist destination routes.

    The score definition is unchanged; only the calculation is vectorized.
    """
    if not route:
        return float("inf")

    route_indices = np.asarray(route, dtype=np.intp)
    directed_scores = (
        matrix[client, route_indices]
        + matrix[route_indices, client]
    )
    return float(np.min(directed_scores))


def improve_routes_restricted_relocate(
    routes: list[list[int]],
    matrix: np.ndarray,
    vehicle_capacity: float,
    client_demands: np.ndarray,
    *,
    candidate_fraction: float = 0.10,
    neighbor_routes: int = 5,
    max_insertions_per_route: int = 3,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
) -> tuple[float, list[list[int]], dict[str, float | int]]:
    """
    Bounded inter-route relocate.

    Only high-marginal-cost clients are considered, only a small set of nearby
    routes is inspected, and only the best few insertion positions are tested.
    Improving moves are accepted immediately.
    """
    started_at = perf_counter()

    if len(routes) < 2:
        return (
            float(calculate_routes_matrix_cost(matrix, routes)),
            [route.copy() for route in routes],
            {
                "runtime_seconds": perf_counter() - started_at,
                "candidate_clients": 0,
                "moves_evaluated": 0,
                "moves_accepted": 0,
                "improvement_km": 0.0,
            },
        )

    candidate_fraction = float(candidate_fraction)
    if not 0.0 < candidate_fraction <= 1.0:
        raise ValueError(
            "candidate_fraction must be greater than 0 and at most 1."
        )
    if neighbor_routes <= 0:
        raise ValueError("neighbor_routes must be greater than zero.")
    if max_insertions_per_route <= 0:
        raise ValueError(
            "max_insertions_per_route must be greater than zero."
        )

    working_routes = [route.copy() for route in routes]
    demands = np.asarray(client_demands, dtype=float)
    route_loads = [
        float(sum(demands[client - 1] for client in route))
        for route in working_routes
    ]
    initial_cost = float(
        calculate_routes_matrix_cost(matrix, working_routes)
    )

    ranked_candidates: list[tuple[float, int]] = []
    for route in working_routes:
        for position, client in enumerate(route):
            ranked_candidates.append(
                (_route_removal_gain(route, position, matrix), client)
            )

    ranked_candidates.sort(key=lambda item: item[0], reverse=True)
    candidate_count = max(
        1,
        int(np.ceil(candidate_fraction * len(ranked_candidates))),
    )
    candidate_clients = [
        client for _gain, client in ranked_candidates[:candidate_count]
    ]

    client_to_route: dict[int, int] = {}
    for route_index, route in enumerate(working_routes):
        for client in route:
            client_to_route[client] = route_index

    moves_evaluated = 0
    moves_accepted = 0

    for client in candidate_clients:
        source_index = client_to_route.get(client)
        if source_index is None:
            continue

        source_route = working_routes[source_index]
        if not source_route:
            continue

        try:
            source_position = source_route.index(client)
        except ValueError:
            continue

        removal_gain = _route_removal_gain(
            source_route,
            source_position,
            matrix,
        )
        demand = float(demands[client - 1])

        destinations: list[tuple[float, int]] = []
        for destination_index, destination_route in enumerate(working_routes):
            if destination_index == source_index or not destination_route:
                continue
            if route_loads[destination_index] + demand > vehicle_capacity + 1e-9:
                continue
            destinations.append(
                (
                    _route_proximity_score(
                        client,
                        destination_route,
                        matrix,
                    ),
                    destination_index,
                )
            )

        destinations.sort(key=lambda item: item[0])
        destinations = destinations[:neighbor_routes]

        accepted = False

        for _proximity, destination_index in destinations:
            destination_route = working_routes[destination_index]

            insertion_options = [
                (
                    _route_insertion_delta(
                        destination_route,
                        position,
                        client,
                        matrix,
                    ),
                    position,
                )
                for position in range(len(destination_route) + 1)
            ]
            insertion_options.sort(key=lambda item: item[0])

            for insertion_delta, insertion_position in insertion_options[
                :max_insertions_per_route
            ]:
                moves_evaluated += 1

                total_delta = insertion_delta - removal_gain
                if total_delta >= -1e-9:
                    continue

                candidate_source = (
                    source_route[:source_position]
                    + source_route[source_position + 1:]
                )
                candidate_destination = (
                    destination_route[:insertion_position]
                    + [client]
                    + destination_route[insertion_position:]
                )

                # An accepted relocate may remove the last client from its
                # source route. An empty source route is valid: that vehicle
                # route simply disappears. Do not send an empty route to the
                # duration evaluator, whose result list has no element [0].
                if candidate_source and not _is_route_duration_feasible(
                    candidate_source,
                    duration_matrix,
                    max_route_duration_min,
                    route_start_time_per_route_min,
                    max_last_stop_completion_min,
                ):
                    continue

                if not _is_route_duration_feasible(
                    candidate_destination,
                    duration_matrix,
                    max_route_duration_min,
                    route_start_time_per_route_min,
                    max_last_stop_completion_min,
                ):
                    continue

                working_routes[source_index] = candidate_source
                working_routes[destination_index] = candidate_destination
                route_loads[source_index] -= demand
                route_loads[destination_index] += demand
                client_to_route[client] = destination_index

                moves_accepted += 1
                accepted = True
                break

            if accepted:
                break

    working_routes = [route for route in working_routes if route]
    final_cost = float(
        calculate_routes_matrix_cost(matrix, working_routes)
    )

    return (
        final_cost,
        working_routes,
        {
            "runtime_seconds": perf_counter() - started_at,
            "candidate_clients": candidate_count,
            "moves_evaluated": moves_evaluated,
            "moves_accepted": moves_accepted,
            "improvement_km": max(0.0, initial_cost - final_cost),
        },
    )



def _local_search(
    routes: list[list[int]],
    matrix: np.ndarray,
    *,
    vehicle_capacity: float | None = None,
    client_demands: np.ndarray | None = None,
    restricted_relocate: bool = False,
    relocate_candidate_fraction: float = 0.10,
    relocate_neighbor_routes: int = 5,
    relocate_max_insertions: int = 3,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    return_stats: bool = False,
):
    
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
        max_last_stop_completion_min=max_last_stop_completion_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
    )
    two_opt_seconds = perf_counter() - stage_start
    print(
        f"Local search stage completed in "
        f"{two_opt_seconds:.2f} seconds."
    )

    relocate_seconds = 0.0
    relocate_stats = {
        "candidate_clients": 0,
        "moves_evaluated": 0,
        "moves_accepted": 0,
        "improvement_km": 0.0,
    }

    if restricted_relocate:
        if vehicle_capacity is None or client_demands is None:
            raise ValueError(
                "vehicle_capacity and client_demands are required when "
                "restricted relocate is enabled."
            )

        print(
            "Local search: restricted inter-route relocate "
            f"(top {100.0 * relocate_candidate_fraction:.1f}% clients, "
            f"{relocate_neighbor_routes} neighbor routes, "
            f"{relocate_max_insertions} insertion positions)..."
        )
        relocate_start = perf_counter()

        final_cost, final_routes, relocate_stats = (
            improve_routes_restricted_relocate(
                final_routes,
                matrix,
                vehicle_capacity,
                client_demands,
                candidate_fraction=relocate_candidate_fraction,
                neighbor_routes=relocate_neighbor_routes,
                max_insertions_per_route=relocate_max_insertions,
                duration_matrix=duration_matrix,
                max_route_duration_min=max_route_duration_min,
                max_last_stop_completion_min=max_last_stop_completion_min,
                route_start_time_per_route_min=route_start_time_per_route_min,
            )
        )

        relocate_seconds = perf_counter() - relocate_start
        print(
            "Restricted relocate completed in "
            f"{relocate_seconds:.2f} seconds | "
            f"candidates={relocate_stats['candidate_clients']} | "
            f"evaluated={relocate_stats['moves_evaluated']} | "
            f"accepted={relocate_stats['moves_accepted']} | "
            f"improvement={relocate_stats['improvement_km']:.3f} km"
        )

    local_search_seconds = perf_counter() - local_search_start
    print(
        f"Local search completed in "
        f"{local_search_seconds:.2f} seconds."
    )

    if return_stats:
        return (
            final_cost,
            final_routes,
            {
                "total_seconds": float(local_search_seconds),
                "two_opt_seconds": float(two_opt_seconds),
                "relocate_seconds": float(relocate_seconds),
                "relocate_candidate_clients": int(
                    relocate_stats["candidate_clients"]
                ),
                "relocate_moves_evaluated": int(
                    relocate_stats["moves_evaluated"]
                ),
                "relocate_moves_accepted": int(
                    relocate_stats["moves_accepted"]
                ),
                "relocate_improvement_km": float(
                    relocate_stats["improvement_km"]
                ),
            },
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
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    cws_allow_route_reversal: bool = False,
    biased_cws_alpha_min: float = 0.05,
    biased_cws_alpha_max: float = 0.25,
    rng: np.random.Generator | None = None,
    show_progress: bool = False,
    profiling_callback=None,
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

    if rng is None:
        rng = np.random.default_rng()

    print(
        f"BR-CWS reconstruction: {n_freed} clients | "
        f"alpha~U({biased_cws_alpha_min:.3f}, {biased_cws_alpha_max:.3f})"
    )

    _, sub_routes = clarke_wright_savings(
        matrix=sub_matrix,
        n_clients=n_freed,
        vehicle_capacity=vehicle_capacity,
        client_demands=sub_demands,
        duration_matrix=sub_duration_matrix,
        max_route_duration_min=max_route_duration_min,
        max_last_stop_completion_min=max_last_stop_completion_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        show_progress=show_progress,
        allow_route_reversal=cws_allow_route_reversal,
        biased_randomization=True,
        biased_alpha_min=biased_cws_alpha_min,
        biased_alpha_max=biased_cws_alpha_max,
        rng=rng,
        profiling_callback=profiling_callback,
    )

    print(
        f"BR-CWS reconstruction completed: "
        f"{len(sub_routes)} routes created."
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
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    cws_allow_route_reversal: bool = False,
    biased_cws_alpha_min: float = 0.05,
    biased_cws_alpha_max: float = 0.25,
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
        max_last_stop_completion_min=max_last_stop_completion_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        cws_allow_route_reversal=cws_allow_route_reversal,
        biased_cws_alpha_min=biased_cws_alpha_min,
        biased_cws_alpha_max=biased_cws_alpha_max,
        rng=rng,
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
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    max_iterations: int = 100,
    max_iterations_without_improvement: int | None = 20,
    destruction_percentage_step: float = 10.0,
    max_destruction_percentage: float = 100.0,
    max_full_destruction_attempts: int = 2,
    biased_cws_alpha_min: float = 0.05,
    biased_cws_alpha_max: float = 0.25,
    restricted_relocate: bool = True,
    relocate_candidate_fraction: float = 0.10,
    relocate_neighbor_routes: int = 5,
    relocate_max_insertions: int = 3,
    random_seed: int | None = None,
    show_progress: bool = False,
    cws_allow_route_reversal: bool = False,
    return_stats: bool = False,
    profiling_callback=None,
):
    """
    Build a feasible routing solution using Iterated Local Search.

    Clarke-Wright Savings generates the initial solution. Local search then
    improves it with 2-opt. Perturbation is a destroy-and-rebuild step: a
    growing percentage of routes is removed at random and rebuilt with a
    biased-randomized Clarke-Wright Savings procedure on the freed clients.
    During reconstruction, every BR-CWS edge-selection step samples its
    geometric-distribution alpha uniformly from
    ``[biased_cws_alpha_min, biased_cws_alpha_max]``.

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
    if max_full_destruction_attempts <= 0:
        raise ValueError(
            "max_full_destruction_attempts must be greater than zero."
        )

    biased_cws_alpha_min = float(biased_cws_alpha_min)
    biased_cws_alpha_max = float(biased_cws_alpha_max)
    if not 0.0 < biased_cws_alpha_min < 1.0:
        raise ValueError(
            "biased_cws_alpha_min must be strictly between 0 and 1."
        )
    if not 0.0 < biased_cws_alpha_max < 1.0:
        raise ValueError(
            "biased_cws_alpha_max must be strictly between 0 and 1."
        )
    if biased_cws_alpha_min > biased_cws_alpha_max:
        raise ValueError(
            "biased_cws_alpha_min cannot exceed biased_cws_alpha_max."
        )

    relocate_candidate_fraction = float(relocate_candidate_fraction)
    if not 0.0 < relocate_candidate_fraction <= 1.0:
        raise ValueError(
            "relocate_candidate_fraction must be greater than 0 and at most 1."
        )
    if relocate_neighbor_routes <= 0:
        raise ValueError("relocate_neighbor_routes must be greater than zero.")
    if relocate_max_insertions <= 0:
        raise ValueError(
            "relocate_max_insertions must be greater than zero."
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

    initial_cws_started = perf_counter()
    initial_cws_cost, initial_routes = clarke_wright_savings(
        matrix=matrix,
        n_clients=n_clients,
        vehicle_capacity=vehicle_capacity,
        client_demands=demands,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        max_last_stop_completion_min=max_last_stop_completion_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        show_progress=show_progress,
        allow_route_reversal=cws_allow_route_reversal,
        profiling_callback=profiling_callback,
    )
    initial_cws_seconds = perf_counter() - initial_cws_started
    if profiling_callback is not None:
        profiling_callback({
            "stage": "m3_ils_initial_cws",
            "seconds": initial_cws_seconds,
            "detail": {
                "clients": n_clients,
                "routes": len(initial_routes),
                "cost_km": float(initial_cws_cost),
            },
        })

    current_cost, current_routes, initial_ls_stats = _local_search(
        initial_routes,
        matrix,
        vehicle_capacity=vehicle_capacity,
        client_demands=demands,
        restricted_relocate=restricted_relocate,
        relocate_candidate_fraction=relocate_candidate_fraction,
        relocate_neighbor_routes=relocate_neighbor_routes,
        relocate_max_insertions=relocate_max_insertions,
        duration_matrix=duration_matrix,
        max_route_duration_min=max_route_duration_min,
        max_last_stop_completion_min=max_last_stop_completion_min,
        route_start_time_per_route_min=route_start_time_per_route_min,
        return_stats=True,
    )
    if profiling_callback is not None:
        profiling_callback({
            "stage": "m3_ils_initial_local_search",
            "seconds": initial_ls_stats["total_seconds"],
            "detail": initial_ls_stats,
        })

    best_cost = current_cost
    best_routes = [route.copy() for route in current_routes]

    destruction_percentage = 0.0
    full_destruction_attempts = 0
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
        attempted_destruction_percentage = destruction_percentage

        reconstruction_started = perf_counter()
        remaining_routes, freed_clients = destroy_routes(
            current_routes,
            destruction_percentage,
            rng,
        )
        rebuilt_routes = reconstruct_routes(
            freed_clients,
            matrix,
            vehicle_capacity,
            demands,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            max_last_stop_completion_min=max_last_stop_completion_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
            cws_allow_route_reversal=cws_allow_route_reversal,
            biased_cws_alpha_min=biased_cws_alpha_min,
            biased_cws_alpha_max=biased_cws_alpha_max,
            rng=rng,
            profiling_callback=profiling_callback,
        )
        perturbed_routes = remaining_routes + rebuilt_routes
        reconstruction_seconds = perf_counter() - reconstruction_started

        candidate_cost, candidate_routes, candidate_ls_stats = _local_search(
            perturbed_routes,
            matrix,
            vehicle_capacity=vehicle_capacity,
            client_demands=demands,
            restricted_relocate=restricted_relocate,
            relocate_candidate_fraction=relocate_candidate_fraction,
            relocate_neighbor_routes=relocate_neighbor_routes,
            relocate_max_insertions=relocate_max_insertions,
            duration_matrix=duration_matrix,
            max_route_duration_min=max_route_duration_min,
            max_last_stop_completion_min=max_last_stop_completion_min,
            route_start_time_per_route_min=route_start_time_per_route_min,
            return_stats=True,
        )

        base_cost_before_acceptance = current_cost
        accepted = candidate_cost < current_cost - 1e-9

        destruction_cycle_reset = False

        if accepted:
            current_cost = candidate_cost
            current_routes = [route.copy() for route in candidate_routes]

            destruction_percentage = 0.0
            full_destruction_attempts = 0
            iterations_without_improvement = 0

            if candidate_cost < best_cost - 1e-9:
                best_cost = candidate_cost
                best_routes = [route.copy() for route in candidate_routes]
        else:
            iterations_without_improvement += 1

            if (
                attempted_destruction_percentage
                >= max_destruction_percentage - 1e-12
            ):
                full_destruction_attempts += 1

                if (
                    full_destruction_attempts
                    >= max_full_destruction_attempts
                ):
                    # Full BR-CWS reconstruction is the most expensive
                    # neighborhood. After a configurable number of consecutive
                    # unsuccessful full destructions, restart the destruction
                    # ladder instead of repeatedly rebuilding 100% of clients.
                    destruction_percentage = 0.0
                    full_destruction_attempts = 0
                    destruction_cycle_reset = True
            else:
                full_destruction_attempts = 0

        if profiling_callback is not None:
            profiling_callback({
                "stage": "m3_ils_iteration",
                "seconds": (
                    reconstruction_seconds
                    + candidate_ls_stats["total_seconds"]
                ),
                "detail": {
                    "iteration": iterations_completed,
                    "destruction_percentage": float(
                        destruction_percentage
                    ),
                    "destruction_percentage_attempted": float(
                        attempted_destruction_percentage
                    ),
                    "full_destruction_attempts": int(
                        full_destruction_attempts
                    ),
                    "max_full_destruction_attempts": int(
                        max_full_destruction_attempts
                    ),
                    "destruction_cycle_reset": bool(
                        destruction_cycle_reset
                    ),
                    "freed_clients": len(freed_clients),
                    "remaining_routes": len(remaining_routes),
                    "rebuilt_routes": len(rebuilt_routes),
                    "reconstruction_seconds": float(reconstruction_seconds),
                    "two_opt_seconds": float(
                        candidate_ls_stats["two_opt_seconds"]
                    ),
                    "relocate_seconds": float(
                        candidate_ls_stats["relocate_seconds"]
                    ),
                    "relocate_candidates": int(
                        candidate_ls_stats["relocate_candidate_clients"]
                    ),
                    "relocate_moves_evaluated": int(
                        candidate_ls_stats["relocate_moves_evaluated"]
                    ),
                    "relocate_moves_accepted": int(
                        candidate_ls_stats["relocate_moves_accepted"]
                    ),
                    "base_cost_before_km": float(
                        base_cost_before_acceptance
                    ),
                    "candidate_cost_km": float(candidate_cost),
                    "best_cost_km": float(best_cost),
                    "accepted": bool(accepted),
                    "iterations_without_improvement": int(
                        iterations_without_improvement
                    ),
                },
            })

        if show_progress:
            iteration_progress.set_postfix(
                best_km=f"{best_cost:.3f}",
                p=f"{destruction_percentage:.0f}%",
                full_attempts=full_destruction_attempts,
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