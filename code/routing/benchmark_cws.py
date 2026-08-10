from __future__ import annotations
import numpy as np
from time import perf_counter
from code.routing.cws import clarke_wright_savings


def benchmark_cws(
    n_clients: int,
    vehicle_capacity: float = 1000.0,
    seed: int = 0,
) -> float:
    """Time clarke_wright_savings on a random instance of n_clients."""

    rng = np.random.default_rng(seed)
    size = n_clients + 1

    matrix = rng.uniform(1, 100, size=(size, size))
    np.fill_diagonal(matrix, 0)
    demands = rng.uniform(1, 10, size=n_clients)

    start = perf_counter()
    cost, routes = clarke_wright_savings(
        matrix=matrix,
        n_clients=n_clients,
        vehicle_capacity=vehicle_capacity,
        client_demands=demands,
        show_progress=False,
    )
    elapsed = perf_counter() - start

    print(
        f"n_clients={n_clients:>6}  ->  {elapsed:7.2f} s  "
        f"({len(routes)} rutas, coste={cost:.1f})"
    )
    return elapsed


if __name__ == "__main__":
    sizes = [500, 1000, 2000, 4000, 6000]

    print("Benchmark de clarke_wright_savings\n" + "-" * 40)
    for n in sizes:
        benchmark_cws(n)