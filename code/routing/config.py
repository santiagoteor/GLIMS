from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingAlgorithmConfig:
    algorithm: str = "cws"
    cws_allow_route_reversal: bool = False
    ils_max_iterations: int = 100
    ils_max_iterations_without_improvement: int | None = 20
    ils_destruction_percentage_step: float = 10.0
    ils_max_destruction_percentage: float = 100.0
    ils_max_full_destruction_attempts: int = 2
    ils_biased_cws_alpha_min: float = 0.05
    ils_biased_cws_alpha_max: float = 0.25
    ils_restricted_relocate: bool = True
    ils_relocate_candidate_fraction: float = 0.10
    ils_relocate_neighbor_routes: int = 5
    ils_relocate_max_insertions: int = 3
    ils_random_seed: int | None = 42
    last_service_deadline_enabled: bool = False
    last_service_margin_min: float = 30.0
