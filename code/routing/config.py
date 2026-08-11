from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingAlgorithmConfig:
    algorithm: str = "cws"
    cws_allow_route_reversal: bool = False
    ils_max_iterations: int = 100
    ils_max_iterations_without_improvement: int | None = 20
    ils_destruction_percentage_step: float = 10.0
    ils_max_destruction_percentage: float = 100.0
    ils_biased_cws_alpha_min: float = 0.05
    ils_biased_cws_alpha_max: float = 0.25
    ils_random_seed: int | None = 42