from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from code.traffic.provider import TimeTrafficProvider


@dataclass(frozen=True)
class SegmentTimeline:
    origin_index: int
    destination_index: int
    departure_datetime: datetime
    arrival_datetime: datetime
    base_duration_min: float
    adjusted_duration_min: float
    traffic_multiplier: float
    traffic_source: str


@dataclass(frozen=True)
class RouteTimeline:
    route_start: datetime
    route_end: datetime
    base_travel_time_min: float
    adjusted_travel_time_min: float
    traffic_delay_min: float
    service_time_min: float
    preparation_time_min: float
    total_duration_min: float
    shift_feasible: bool
    segments: list[SegmentTimeline]


def evaluate_route_timeline(
    *,
    route: list[int],
    duration_matrix: np.ndarray,
    route_start: datetime,
    shift_end: datetime,
    traffic_provider: TimeTrafficProvider,
    traffic_zone: str | None,
    service_time_per_stop_min: float,
    route_preparation_time_min: float,
) -> RouteTimeline:
    """Evaluate one completed route sequentially using departure-time traffic."""

    current_time = route_start + timedelta(
        minutes=float(route_preparation_time_min)
    )
    nodes = [0, *route, 0]
    segments: list[SegmentTimeline] = []
    base_travel_time = 0.0
    adjusted_travel_time = 0.0

    for origin, destination in zip(nodes, nodes[1:]):
        base_duration = float(duration_matrix[origin, destination])
        observation = traffic_provider.get_multiplier(
            zone=traffic_zone,
            departure_datetime=current_time,
        )
        adjusted_duration = base_duration * observation.multiplier
        arrival_time = current_time + timedelta(minutes=adjusted_duration)

        segments.append(
            SegmentTimeline(
                origin_index=origin,
                destination_index=destination,
                departure_datetime=current_time,
                arrival_datetime=arrival_time,
                base_duration_min=base_duration,
                adjusted_duration_min=adjusted_duration,
                traffic_multiplier=observation.multiplier,
                traffic_source=observation.source,
            )
        )
        base_travel_time += base_duration
        adjusted_travel_time += adjusted_duration
        current_time = arrival_time

        if destination != 0:
            current_time += timedelta(minutes=float(service_time_per_stop_min))

    stop_service_time = len(route) * float(service_time_per_stop_min)
    preparation_time = float(route_preparation_time_min)
    total_duration = adjusted_travel_time + stop_service_time + preparation_time

    return RouteTimeline(
        route_start=route_start,
        route_end=current_time,
        base_travel_time_min=base_travel_time,
        adjusted_travel_time_min=adjusted_travel_time,
        traffic_delay_min=adjusted_travel_time - base_travel_time,
        service_time_min=stop_service_time,
        preparation_time_min=preparation_time,
        total_duration_min=total_duration,
        shift_feasible=current_time <= shift_end,
        segments=segments,
    )
