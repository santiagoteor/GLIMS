from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingAlgorithmConfig:
    algorithm: str = "cws"
    ils_max_iterations: int = 100
    ils_max_iterations_without_improvement: int | None = 20
    ils_perturbation_moves: int = 2
    ils_random_seed: int | None = 42