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
    candidate_window: int = 2048,
    sampling_batch_size: int = 8192,
):
    """Biased RCL sampling; batch_size=1 reproduces the legacy RNG sequence."""
    if candidate_count <= 0:
        return
    window = max(1, min(int(candidate_window), int(candidate_count)))
    batch_size = max(1, int(sampling_batch_size))
    active = list(range(window))
    next_position = window

    if batch_size == 1:
        while active:
            alpha = float(rng.uniform(alpha_min, alpha_max))
            rank = int(rng.geometric(alpha)) - 1
            if rank >= len(active):
                rank = int(rng.integers(0, len(active)))
            selected_position = active.pop(rank)
            if next_position < candidate_count:
                active.append(next_position)
                next_position += 1
            yield selected_position
        return

    remaining_draws = int(candidate_count)
    while active and remaining_draws > 0:
        draw_count = min(batch_size, remaining_draws)
        alphas = rng.uniform(alpha_min, alpha_max, size=draw_count)
        ranks = rng.geometric(alphas) - 1
        for sampled_rank in ranks:
            if not active:
                return
            rank = int(sampled_rank)
            if rank >= len(active):
                rank = int(rng.integers(0, len(active)))
            selected_position = active.pop(rank)
            if next_position < candidate_count:
                active.append(next_position)
                next_position += 1
            remaining_draws -= 1
            yield selected_position


def clarke_wright_savings(
    matrix: np.ndarray,
    n_clients: int,
    vehicle_capacity: float,
    client_demands=None,
    *,
    duration_matrix: np.ndarray | None = None,
    max_route_duration_min: float | None = None,
    max_last_stop_completion_min: float | None = None,
    route_start_time_per_route_min: float = 0.0,
    show_progress: bool = False,
    allow_route_reversal: bool = False,
    biased_randomization: bool = False,
    biased_alpha_min: float = 0.05,
    biased_alpha_max: float = 0.25,
    biased_candidate_window: int = 2048,
    biased_sampling_batch_size: int = 8192,
    max_failed_candidates_without_merge: int | None = 200_000,
    rng: np.random.Generator | None = None,
    profiling_callback=None,
):
    """
    Build capacity- and duration-feasible routes using parallel Clarke-Wright.

    Matrix index 0 is the depot and client indices are 1..n_clients.
    Savings generation and sorting are vectorized with numpy; route merging
    remains a sequential pass (it depends on state built incrementally).
    """

    cws_total_start = perf_counter()
    cws_setup_start = cws_total_start

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

        biased_candidate_window = int(biased_candidate_window)
        if biased_candidate_window <= 0:
            raise ValueError(
                "biased_candidate_window must be greater than zero."
            )

        biased_sampling_batch_size = int(biased_sampling_batch_size)
        if biased_sampling_batch_size <= 0:
            raise ValueError(
                "biased_sampling_batch_size must be greater than zero."
            )

        if max_failed_candidates_without_merge is not None:
            max_failed_candidates_without_merge = int(
                max_failed_candidates_without_merge
            )
            if max_failed_candidates_without_merge <= 0:
                raise ValueError(
                    "max_failed_candidates_without_merge must be greater "
                    "than zero when provided."
                )

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

    timing_constraints_enabled = (
        duration_matrix is not None
        and (
            max_route_duration_min is not None
            or max_last_stop_completion_min is not None
        )
    )
    duration_limit_enabled = (
        duration_matrix is not None
        and max_route_duration_min is not None
    )
    last_stop_deadline_enabled = (
        duration_matrix is not None
        and max_last_stop_completion_min is not None
    )

    if timing_constraints_enabled:
        duration_matrix = np.asarray(duration_matrix, dtype=float)
        expected_shape = (n_clients + 1, n_clients + 1)
        if duration_matrix.shape != expected_shape:
            raise ValueError(
                "duration_matrix must have shape "
                f"{expected_shape}, got {duration_matrix.shape}."
            )

    if duration_limit_enabled:
        max_route_duration_min = float(max_route_duration_min)
        if max_route_duration_min <= 0:
            raise ValueError("max_route_duration_min must be greater than zero.")

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

    if last_stop_deadline_enabled:
        max_last_stop_completion_min = float(max_last_stop_completion_min)
        if max_last_stop_completion_min <= 0:
            raise ValueError(
                "max_last_stop_completion_min must be greater than zero."
            )

        deadline_infeasible_clients = [
            client
            for client in range(1, n_clients + 1)
            if (
                route_start_time_per_route_min
                + duration_matrix[0, client]
                + SERVICE_TIME_PER_STOP_MIN
                > max_last_stop_completion_min
            )
        ]
        if deadline_infeasible_clients:
            raise ValueError(
                "At least one supply stop cannot be completed before the "
                "configured facility-arrival cutoff, even when served alone. "
                f"Cutoff elapsed minutes: {max_last_stop_completion_min:.1f}. "
                f"Client indices: {deadline_infeasible_clients}"
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

    # Cache route durations so candidate merges can be checked in O(1)
    # without rescanning the entire merged route.
    route_durations = None
    if timing_constraints_enabled:
        route_durations = {
            client: float(
                route_start_time_per_route_min
                + duration_matrix[0, client]
                + duration_matrix[client, 0]
                + SERVICE_TIME_PER_STOP_MIN
            )
            for client in range(1, n_clients + 1)
        }

    setup_seconds = perf_counter() - cws_setup_start

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
            candidate_window=biased_candidate_window,
            sampling_batch_size=biased_sampling_batch_size,
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
    processed_candidates = 0
    successful_merges = 0
    failed_since_last_merge = 0
    termination_reason = "candidate_exhaustion"
    same_route_rejections = 0
    endpoint_rejections = 0
    capacity_rejections = 0
    route_duration_rejections = 0
    last_service_deadline_rejections = 0
    route_id_update_clients = 0

    def register_failed_candidate() -> bool:
        nonlocal failed_since_last_merge, termination_reason
        failed_since_last_merge += 1
        if (
            biased_randomization
            and max_failed_candidates_without_merge is not None
            and failed_since_last_merge
            >= max_failed_candidates_without_merge
        ):
            termination_reason = "stagnation"
            return True
        return False

    for candidate_position in merge_progress:
        processed_candidates += 1

        i = int(sorted_i[candidate_position])
        j = int(sorted_j[candidate_position])
        route_i_id = route_of[i]
        route_j_id = route_of[j]

        if route_i_id == route_j_id:
            same_route_rejections += 1
            if register_failed_candidate():
                break
            continue

        route_i = routes[route_i_id]
        route_j = routes[route_j_id]

        if allow_route_reversal:
            if route_i[-1] == i:
                oriented_i = route_i
            elif route_i[0] == i:
                oriented_i = list(reversed(route_i))
            else:
                endpoint_rejections += 1
                if register_failed_candidate():
                    break
                continue

            if route_j[0] == j:
                oriented_j = route_j
            elif route_j[-1] == j:
                oriented_j = list(reversed(route_j))
            else:
                endpoint_rejections += 1
                if register_failed_candidate():
                    break
                continue
        else:
            if route_i[-1] != i or route_j[0] != j:
                endpoint_rejections += 1
                if register_failed_candidate():
                    break
                continue
            oriented_i = route_i
            oriented_j = route_j

        merged_load = route_loads[route_i_id] + route_loads[route_j_id]

        if merged_load > vehicle_capacity:
            capacity_rejections += 1
            if register_failed_candidate():
                break
            continue

        merged_route = oriented_i + oriented_j

        if timing_constraints_enabled:
            merged_duration = float(
                route_durations[route_i_id]
                + route_durations[route_j_id]
                - route_start_time_per_route_min
                - duration_matrix[oriented_i[-1], 0]
                - duration_matrix[0, oriented_j[0]]
                + duration_matrix[oriented_i[-1], oriented_j[0]]
            )

            if (
                duration_limit_enabled
                and merged_duration > max_route_duration_min
            ):
                route_duration_rejections += 1
                if register_failed_candidate():
                    break
                continue

            if last_stop_deadline_enabled:
                merged_last_stop_completion = (
                    merged_duration
                    - float(duration_matrix[oriented_j[-1], 0])
                )
                if merged_last_stop_completion > max_last_stop_completion_min:
                    last_service_deadline_rejections += 1
                    if register_failed_candidate():
                        break
                    continue

        routes[route_i_id] = merged_route
        route_loads[route_i_id] = merged_load
        if timing_constraints_enabled:
            route_durations[route_i_id] = merged_duration

        del routes[route_j_id]
        del route_loads[route_j_id]
        if timing_constraints_enabled:
            del route_durations[route_j_id]

        # Only clients absorbed from route_j need a route-id update.
        for client in oriented_j:
            route_of[client] = route_i_id
        route_id_update_clients += len(oriented_j)

        successful_merges += 1
        failed_since_last_merge = 0

        # No further capacity-/service-feasible solution can contain fewer
        # routes than this lower bound.
        if len(routes) <= lower_bound_routes:
            termination_reason = "lower_bound_reached"
            break

    final_routes = list(routes.values())
    total_cost = calculate_routes_matrix_cost(matrix, final_routes)

    merging_seconds = perf_counter() - merging_start

    print(
        f"{'BR-CWS' if biased_randomization else 'CWS'} route merging completed in "
        f"{merging_seconds:.2f} seconds. "
        f"Final routes: {len(final_routes)} | "
        f"processed savings: {processed_candidates:,} | "
        f"successful merges: {successful_merges:,} | "
        f"termination: {termination_reason}."
    )

    if profiling_callback is not None:
        total_seconds = perf_counter() - cws_total_start
        profiling_callback({
            "stage": "m3_brcws_internal" if biased_randomization else "m3_cws_internal",
            "seconds": float(total_seconds),
            "detail": {
                "biased_randomization": bool(biased_randomization),
                "clients": int(n_clients),
                "candidate_pairs": int(n_clients * (n_clients - 1)),
                "finite_savings": int(len(flat_savings)),
                "setup_seconds": float(setup_seconds),
                "savings_generation_seconds": float(savings_generation_seconds),
                "sorting_seconds": float(sorting_seconds),
                "merging_seconds": float(merging_seconds),
                "processed_candidates": int(processed_candidates),
                "successful_merges": int(successful_merges),
                "same_route_rejections": int(same_route_rejections),
                "endpoint_rejections": int(endpoint_rejections),
                "capacity_rejections": int(capacity_rejections),
                "route_duration_rejections": int(route_duration_rejections),
                "last_service_deadline_rejections": int(last_service_deadline_rejections),
                "route_id_update_clients": int(route_id_update_clients),
                "failed_since_last_merge_at_end": int(failed_since_last_merge),
                "termination_reason": str(termination_reason),
                "initial_routes": int(n_clients),
                "final_routes": int(len(final_routes)),
                "lower_bound_routes": int(lower_bound_routes),
                "candidate_window": int(biased_candidate_window) if biased_randomization else None,
                "sampling_batch_size": int(biased_sampling_batch_size) if biased_randomization else None,
                "max_failed_candidates_without_merge": (
                    int(max_failed_candidates_without_merge)
                    if biased_randomization and max_failed_candidates_without_merge is not None
                    else None
                ),
            },
        })

    return total_cost, final_routes