import math

import numpy as np
from tqdm.auto import tqdm
from code.common.constants import SERVICE_TIME_PER_STOP_MIN
from code.common.routing_utils import calculate_route_durations, calculate_routes_matrix_cost
from time import perf_counter


class _FenwickOrderStatisticSet:
    """Compact order-statistic set used by biased-randomized CWS.

    The structure initially contains positions ``0..size-1``. ``pop_rank(k)``
    removes and returns the k-th remaining position in O(log n), avoiding the
    O(n) list shifts that ``list.pop(k)`` would introduce for large savings
    lists.
    """

    def __init__(self, size: int) -> None:
        self.size = int(size)
        self.remaining = int(size)
        self.tree = np.empty(self.size + 1, dtype=np.int32)
        self.tree[0] = 0

        # For an all-ones Fenwick tree, tree[i] == lowbit(i). Fill in chunks to
        # avoid allocating another array as large as the complete savings list.
        chunk_size = 1_000_000
        for start in range(1, self.size + 1, chunk_size):
            end = min(start + chunk_size, self.size + 1)
            indices = np.arange(start, end, dtype=np.int32)
            self.tree[start:end] = indices & -indices

    def pop_rank(self, rank: int) -> int:
        if not 0 <= rank < self.remaining:
            raise IndexError("rank is outside the remaining candidate set")

        # Fenwick binary lifting: find the (rank + 1)-th active element.
        target = rank + 1
        index = 0
        bit = 1 << (self.size.bit_length() - 1) if self.size else 0

        while bit:
            next_index = index + bit
            if next_index <= self.size and self.tree[next_index] < target:
                index = next_index
                target -= int(self.tree[next_index])
            bit >>= 1

        fenwick_index = index + 1
        selected_position = fenwick_index - 1

        update_index = fenwick_index
        while update_index <= self.size:
            self.tree[update_index] -= 1
            update_index += update_index & -update_index

        self.remaining -= 1
        return selected_position


def _minimum_route_count_lower_bound(
    *,
    n_clients: int,
    total_demand: float,
    vehicle_capacity: float,
    max_route_duration_min: float | None,
    route_start_time_per_route_min: float,
) -> int:
    """Return a safe lower bound on the number of feasible routes."""

    capacity_bound = max(1, math.ceil(total_demand / vehicle_capacity))

    if max_route_duration_min is None:
        return capacity_bound

    available_service_time = (
        float(max_route_duration_min) - float(route_start_time_per_route_min)
    )
    if available_service_time <= 0:
        return n_clients

    max_stops_from_service = max(
        1,
        math.floor(available_service_time / SERVICE_TIME_PER_STOP_MIN),
    )
    service_bound = math.ceil(n_clients / max_stops_from_service)
    return max(capacity_bound, service_bound)


def _biased_candidate_positions(
    candidate_count: int,
    *,
    alpha_min: float,
    alpha_max: float,
    rng: np.random.Generator,
):
    """Yield savings-list positions using Juan et al.'s quasi-geometric bias.

    Rank 0 is the best remaining saving. A fresh alpha is drawn uniformly from
    ``[alpha_min, alpha_max]`` at every edge-selection step. A geometric draw
    selects the rank; if it falls outside the finite current list, a uniform
    fallback is used. This is equivalent to distributing the geometric tail
    probability uniformly across the finite list (the paper's epsilon term).
    """

    active = _FenwickOrderStatisticSet(candidate_count)

    while active.remaining:
        alpha = float(rng.uniform(alpha_min, alpha_max))
        rank = int(rng.geometric(alpha)) - 1

        if rank >= active.remaining:
            rank = int(rng.integers(0, active.remaining))

        yield active.pop_rank(rank)


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
    allow_route_reversal: bool = False,
    biased_randomization: bool = False,
    biased_alpha_min: float = 0.05,
    biased_alpha_max: float = 0.25,
    rng: np.random.Generator | None = None,
):
    """
    Build capacity- and duration-feasible routes using parallel Clarke-Wright.

    Matrix index 0 is the depot and client indices are 1..n_clients.
    Savings generation and sorting are vectorized with numpy; route merging
    remains a sequential pass (it depends on state built incrementally).
    """

    if biased_randomization:
        biased_alpha_min = float(biased_alpha_min)
        biased_alpha_max = float(biased_alpha_max)
        if not 0.0 < biased_alpha_min < 1.0:
            raise ValueError(
                "biased_alpha_min must be strictly between 0 and 1."
            )
        if not 0.0 < biased_alpha_max < 1.0:
            raise ValueError(
                "biased_alpha_max must be strictly between 0 and 1."
            )
        if biased_alpha_min > biased_alpha_max:
            raise ValueError(
                "biased_alpha_min cannot exceed biased_alpha_max."
            )
        if rng is None:
            rng = np.random.default_rng()

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

    print(
        f"Generating directed CWS savings for "
        f"{n_clients} clients "
        f"({n_clients * (n_clients - 1):,} candidate pairs, vectorized)..."
    )
    savings_start = perf_counter()

    client_range = np.arange(1, n_clients + 1)
    depot_to_client = matrix[0, client_range]          # matrix[0, j]
    client_to_depot = matrix[client_range, 0]          # matrix[i, 0]
    inner_matrix = matrix[np.ix_(client_range, client_range)]  # matrix[i, j]

    savings_matrix = (
        client_to_depot[:, None] + depot_to_client[None, :] - inner_matrix
    )
    np.fill_diagonal(savings_matrix, -np.inf)          # exclude i == j

    finite_mask = np.isfinite(savings_matrix)
    flat_savings = savings_matrix[finite_mask]
    flat_a, flat_b = np.nonzero(finite_mask)            # 0-indexed positions

    flat_i = flat_a + 1                                 # back to client ids
    flat_j = flat_b + 1

    savings_generation_seconds = perf_counter() - savings_start
    print(
        f"Generated {len(flat_savings):,} CWS savings in "
        f"{savings_generation_seconds:.2f} seconds."
    )

    print("Sorting CWS savings...")
    sorting_start = perf_counter()

    order = np.argsort(flat_savings)[::-1]

    sorted_i = flat_i[order]
    sorted_j = flat_j[order]

    sorting_seconds = perf_counter() - sorting_start
    print(f"CWS savings sorted in {sorting_seconds:.2f} seconds.")

    if biased_randomization:
        print(
            "Starting biased-randomized CWS route merging "
            f"(alpha~U({biased_alpha_min:.3f}, {biased_alpha_max:.3f}))..."
        )
        candidate_positions = _biased_candidate_positions(
            len(sorted_i),
            alpha_min=biased_alpha_min,
            alpha_max=biased_alpha_max,
            rng=rng,
        )
    else:
        print("Starting CWS route merging...")
        candidate_positions = range(len(sorted_i))

    merging_start = perf_counter()
    n_pairs = len(sorted_i)

    lower_bound_routes = _minimum_route_count_lower_bound(
        n_clients=n_clients,
        total_demand=float(demands.sum()),
        vehicle_capacity=vehicle_capacity,
        max_route_duration_min=(
            max_route_duration_min if duration_limit_enabled else None
        ),
        route_start_time_per_route_min=route_start_time_per_route_min,
    )

    merge_progress = tqdm(
        candidate_positions,
        total=n_pairs,
        desc=(
            "BR-CWS route merging"
            if biased_randomization
            else "CWS route merging"
        ),
        unit="saving",
        disable=not show_progress,
        mininterval=0.5,
        miniters=max(1, n_pairs // 1000),
        leave=False,
    )
    for candidate_position in merge_progress:
        i = int(sorted_i[candidate_position])
        j = int(sorted_j[candidate_position])
        route_i_id = route_of[i]
        route_j_id = route_of[j]

        if route_i_id == route_j_id:
            continue

        route_i = routes[route_i_id]
        route_j = routes[route_j_id]

        if allow_route_reversal:
            if route_i[-1] == i:
                oriented_i = route_i
            elif route_i[0] == i:
                oriented_i = list(reversed(route_i))
            else:
                continue

            if route_j[0] == j:
                oriented_j = route_j
            elif route_j[-1] == j:
                oriented_j = list(reversed(route_j))
            else:
                continue
        else:
            if route_i[-1] != i or route_j[0] != j:
                continue
            oriented_i = route_i
            oriented_j = route_j

        merged_load = route_loads[route_i_id] + route_loads[route_j_id]

        if merged_load > vehicle_capacity:
            continue

        merged_route = oriented_i + oriented_j

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

        # No further capacity-/service-feasible solution can contain fewer
        # routes than this lower bound. Once reached, additional candidate
        # processing cannot reduce the route count further.
        if len(routes) <= lower_bound_routes:
            break

    final_routes = list(routes.values())
    total_cost = calculate_routes_matrix_cost(matrix, final_routes)

    merging_seconds = perf_counter() - merging_start

    print(
        f"{'BR-CWS' if biased_randomization else 'CWS'} route merging completed in "
        f"{merging_seconds:.2f} seconds. "
        f"Final routes: {len(final_routes)}."
    )
    return total_cost, final_routes