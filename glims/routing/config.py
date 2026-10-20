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
    ils_biased_cws_sampling_batch_size: int = 8192
    ils_restricted_relocate: bool = True
    ils_relocate_candidate_fraction: float = 0.10
    ils_relocate_neighbor_routes: int = 5
    ils_relocate_max_insertions: int = 3
    ils_random_seed: int | None = 42
    last_service_deadline_enabled: bool = False
    last_service_margin_min: float = 30.0
    last_meter_access_enabled: bool = False
    last_meter_walking_speed_m_s: float = 1.2
    last_meter_round_trip: bool = True
    last_meter_models: tuple[str, ...] | None = ("M1", "M2")

    def last_meter_applies_to(self, model_code: str) -> bool:
        """Return whether last-meter access is enabled for one model.

        ``None`` or an empty tuple means all supported models. This keeps the
        model filter optional while preserving M1/M2 as the explicit default.
        """

        if not self.last_meter_access_enabled:
            return False
        if not self.last_meter_models:
            return True
        return str(model_code).upper() in {
            str(model).upper() for model in self.last_meter_models
        }
