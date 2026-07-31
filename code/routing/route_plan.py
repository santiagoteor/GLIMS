from dataclasses import dataclass

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

    @property
    def route_count(self):
        return len(self.routes)

    @property
    def max_route_duration_min(self):
        if not self.route_durations_min:
            return 0.0
        return max(self.route_durations_min)

    @property
    def average_route_duration_min(self):
        if not self.route_durations_min:
            return 0.0
        return sum(self.route_durations_min)/len(self.route_durations_min)
