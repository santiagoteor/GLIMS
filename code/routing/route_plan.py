from dataclasses import dataclass, field


@dataclass(frozen=True)
class OsrmRoutePlan:
    transport_mode: str
    vehicle_capacity: float
    routes: list[list[int]]
    total_distance_km: float
    total_duration_min: float
    route_durations_min: list[float]
    route_distances_km: list[float]
    route_loads: list[float]
    route_start_time_per_route_min: float
    service_time_min: float
    routing_algorithm: str = "cws"
    routing_runtime_seconds: float = 0.0
    initial_distance_km: float = 0.0
    improvement_distance_km: float = 0.0
    improvement_percent: float = 0.0
    traffic_profile: str = "baseline"
    traffic_duration_multiplier: float = 1.0
    traffic_source: str = "baseline_osrm"
    time_traffic_profile: str = "not_applied"
    traffic_zone: str = ""
    simulation_day_of_week: str = ""
    simulation_date: str = ""
    shift_start_time: str = ""
    shift_end_time: str = ""
    route_start_datetimes: list[str] = field(default_factory=list)
    route_end_datetimes: list[str] = field(default_factory=list)
    route_base_travel_times_min: list[float] = field(default_factory=list)
    route_adjusted_travel_times_min: list[float] = field(default_factory=list)
    route_traffic_delays_min: list[float] = field(default_factory=list)
    route_shift_feasible: list[bool] = field(default_factory=list)

    @property
    def route_count(self) -> int:
        return len(self.routes)

    @property
    def max_route_duration_min(self) -> float:
        return max(self.route_durations_min, default=0.0)

    @property
    def average_route_duration_min(self) -> float:
        if not self.route_durations_min:
            return 0.0
        return sum(self.route_durations_min) / len(self.route_durations_min)
