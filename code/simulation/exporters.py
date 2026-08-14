from datetime import timedelta

import pandas as pd
from code.routing.osrm_client import osrm_route_geometry, get_osrm_host
from code.common.constants import SERVICE_TIME_PER_STOP_MIN
from code.routing.route_plan import OsrmRoutePlan


VEHICLE_OSRM_PROFILE = {
    "conventional_van": "driving",
    "electric_van": "driving",
    "cargo_bike": "cycling",
    "walking_courier": "walking",
    "customer_walking": "walking",
}


ROUTING_PLAN_METRIC_COLUMNS = (
    "routing_runtime_seconds",
    "initial_distance_km",
    "improvement_distance_km",
    "improvement_percent",
    "cws_initial_distance_km",
    "cws_initial_route_count",
    "cws_runtime_seconds",
    "ils_final_distance_km",
    "ils_final_route_count",
    "ils_improvement_km",
    "ils_improvement_percent",
    "ils_runtime_seconds",
    "ils_iterations_completed",
    "ils_iterations_without_improvement",
    "unroutable_customer_count",
    "unroutable_package_count",
    "unroutable_customer_ids",
)

INTERNAL_ROUTE_EXPORT_COLUMNS = (
    "_stop_ids",
    "_stop_latitudes",
    "_stop_longitudes",
    "_stop_package_loads",
    "_stop_arrival_offsets_min",
    "_stop_service_end_offsets_min",
)


def build_routing_plan_metrics_frame(
    model_details: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build one record per routing plan instead of repeating plan metrics per route."""

    frames = []
    grouping_columns = [
        "city",
        "neighborhood",
        "model",
        "leg",
        "depot",
    ]

    for detail_df in model_details.values():
        if detail_df is None or detail_df.empty:
            continue

        available_metrics = [
            column
            for column in ROUTING_PLAN_METRIC_COLUMNS
            if column in detail_df.columns
        ]
        if not available_metrics:
            continue

        context_columns = [
            column
            for column in (
                "routing_algorithm",
                "vehicle_type",
                "vehicle_capacity",
                "traffic_profile",
                "traffic_duration_multiplier",
                "traffic_source",
                "time_traffic_profile",
                "simulation_date",
                "shift_start_time",
                "shift_end_time",
            )
            if column in detail_df.columns
        ]

        columns = [
            column
            for column in (
                *grouping_columns,
                *context_columns,
                *available_metrics,
            )
            if column in detail_df.columns
        ]

        plan_rows = detail_df[columns].drop_duplicates(
            subset=[
                column
                for column in grouping_columns
                if column in detail_df.columns
            ],
            keep="first",
        )
        frames.append(plan_rows)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def strip_route_export_columns(detail_df: pd.DataFrame) -> pd.DataFrame:
    """Remove plan-level and internal helper fields from one-row-per-route exports."""

    columns_to_drop = [
        column
        for column in (
            *ROUTING_PLAN_METRIC_COLUMNS,
            *INTERNAL_ROUTE_EXPORT_COLUMNS,
        )
        if column in detail_df.columns
    ]
    return detail_df.drop(columns=columns_to_drop, errors="ignore")


def build_route_stops_frame(
    model_details: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Expand structured route metadata into one record per physical stop."""

    rows = []

    for detail_df in model_details.values():
        if detail_df is None or detail_df.empty:
            continue

        for _, route_row in detail_df.iterrows():
            stop_ids = route_row.get("_stop_ids", [])
            latitudes = route_row.get("_stop_latitudes", [])
            longitudes = route_row.get("_stop_longitudes", [])
            package_loads = route_row.get("_stop_package_loads", [])
            arrival_offsets = route_row.get(
                "_stop_arrival_offsets_min", []
            )
            service_end_offsets = route_row.get(
                "_stop_service_end_offsets_min", []
            )

            if not isinstance(stop_ids, list):
                continue

            route_start = pd.to_datetime(
                route_row.get("route_start_datetime", ""),
                errors="coerce",
            )

            leg = str(route_row.get("leg", ""))
            stop_type = (
                "facility"
                if leg == "facility_supply"
                else "customer"
            )

            for position, stop_id in enumerate(stop_ids, start=1):
                arrival_datetime = ""
                service_start_datetime = ""
                service_end_datetime = ""

                arrival_offset = (
                    arrival_offsets[position - 1]
                    if position <= len(arrival_offsets)
                    else None
                )
                service_end_offset = (
                    service_end_offsets[position - 1]
                    if position <= len(service_end_offsets)
                    else None
                )

                if not pd.isna(route_start) and arrival_offset is not None:
                    arrival_ts = route_start + timedelta(
                        minutes=float(arrival_offset)
                    )
                    arrival_datetime = arrival_ts.isoformat(
                        timespec="minutes"
                    )
                    service_start_datetime = arrival_datetime

                    if service_end_offset is not None:
                        service_end_ts = route_start + timedelta(
                            minutes=float(service_end_offset)
                        )
                        service_end_datetime = service_end_ts.isoformat(
                            timespec="minutes"
                        )

                rows.append(
                    {
                        "city": route_row.get("city", ""),
                        "neighborhood": route_row.get(
                            "neighborhood", ""
                        ),
                        "model": route_row.get("model", ""),
                        "leg": leg,
                        "route_id": route_row.get("route_id", ""),
                        "route_number": route_row.get(
                            "route_number", ""
                        ),
                        "vehicle_type": route_row.get(
                            "vehicle_type", ""
                        ),
                        "depot": route_row.get("depot", ""),
                        "stop_position": position,
                        "stop_type": stop_type,
                        "stop_id": stop_id,
                        "latitude": (
                            latitudes[position - 1]
                            if position <= len(latitudes)
                            else ""
                        ),
                        "longitude": (
                            longitudes[position - 1]
                            if position <= len(longitudes)
                            else ""
                        ),
                        "package_load": (
                            package_loads[position - 1]
                            if position <= len(package_loads)
                            else ""
                        ),
                        "arrival_datetime": arrival_datetime,
                        "service_start_datetime": (
                            service_start_datetime
                        ),
                        "service_end_datetime": (
                            service_end_datetime
                        ),
                    }
                )

    return pd.DataFrame(rows)



def build_facility_summary_frame(
    model_details: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Build one record per used microhub/PUDO and model.

    Supply distance is intentionally not allocated to individual facilities:
    one truck route can serve several facilities, so assigning the whole route
    distance to every stop would double-count distance.
    """

    route_stops = build_route_stops_frame(model_details)
    if route_stops.empty:
        return pd.DataFrame()

    result_rows = []

    model_specs = {
        "M3": ("microhub", "cycling_last_mile"),
        "M4": ("pudo", "walking_last_mile"),
        "M5": ("pudo", "customer_collection"),
    }

    for model_code, (facility_type, last_mile_leg) in model_specs.items():
        detail_df = model_details.get(model_code)
        if detail_df is None or detail_df.empty:
            continue

        supply_stops = route_stops.loc[
            route_stops["model"].eq(model_code)
            & route_stops["leg"].eq("facility_supply")
        ].copy()

        last_mile_routes = detail_df.loc[
            detail_df["leg"].eq(last_mile_leg)
        ].copy()

        if supply_stops.empty and last_mile_routes.empty:
            continue

        facility_names = set(
            supply_stops["stop_id"].dropna().astype(str)
        )
        if "depot" in last_mile_routes.columns:
            facility_names.update(
                last_mile_routes["depot"].dropna().astype(str)
            )

        for facility_name in sorted(facility_names):
            supply_for_facility = supply_stops.loc[
                supply_stops["stop_id"].astype(str).eq(facility_name)
            ].copy()

            last_mile_for_facility = last_mile_routes.loc[
                last_mile_routes["depot"].astype(str).eq(facility_name)
            ].copy()

            latitude = pd.NA
            longitude = pd.NA
            if not supply_for_facility.empty:
                lat_values = pd.to_numeric(
                    supply_for_facility["latitude"], errors="coerce"
                ).dropna()
                lon_values = pd.to_numeric(
                    supply_for_facility["longitude"], errors="coerce"
                ).dropna()
                if not lat_values.empty:
                    latitude = float(lat_values.iloc[0])
                if not lon_values.empty:
                    longitude = float(lon_values.iloc[0])

            supplied_packages = pd.to_numeric(
                supply_for_facility.get(
                    "package_load",
                    pd.Series(dtype=float),
                ),
                errors="coerce",
            ).sum()

            customers_assigned = pd.to_numeric(
                last_mile_for_facility.get(
                    "stop_count",
                    pd.Series(dtype=float),
                ),
                errors="coerce",
            ).sum()
            packages_assigned = pd.to_numeric(
                last_mile_for_facility.get(
                    "package_load",
                    pd.Series(dtype=float),
                ),
                errors="coerce",
            ).sum()
            last_mile_km = pd.to_numeric(
                last_mile_for_facility.get(
                    "distance_km",
                    pd.Series(dtype=float),
                ),
                errors="coerce",
            ).sum()
            last_mile_duration_min = pd.to_numeric(
                last_mile_for_facility.get(
                    "duration_min",
                    pd.Series(dtype=float),
                ),
                errors="coerce",
            ).sum()

            supply_service_ends = pd.to_datetime(
                supply_for_facility.get(
                    "service_end_datetime",
                    pd.Series(dtype="object"),
                ).replace("", pd.NA),
                errors="coerce",
            )
            facility_available_at = (
                supply_service_ends.max()
                if supply_service_ends.notna().any()
                else pd.NaT
            )

            last_mile_route_ends = pd.to_datetime(
                last_mile_for_facility.get(
                    "route_end_datetime",
                    pd.Series(dtype="object"),
                ).replace("", pd.NA),
                errors="coerce",
            )
            last_mile_completion = (
                last_mile_route_ends.max()
                if last_mile_route_ends.notna().any()
                else pd.NaT
            )

            lm_stops = route_stops.loc[
                route_stops["model"].eq(model_code)
                & route_stops["leg"].eq(last_mile_leg)
                & route_stops["depot"].astype(str).eq(facility_name)
            ].copy()
            lm_service_ends = pd.to_datetime(
                lm_stops.get(
                    "service_end_datetime",
                    pd.Series(dtype="object"),
                ).replace("", pd.NA),
                errors="coerce",
            )
            last_service_completion = (
                lm_service_ends.max()
                if lm_service_ends.notna().any()
                else pd.NaT
            )

            sample = (
                supply_for_facility.iloc[0]
                if not supply_for_facility.empty
                else last_mile_for_facility.iloc[0]
            )

            result_rows.append(
                {
                    "city": sample.get("city", ""),
                    "neighborhood": sample.get("neighborhood", ""),
                    "model": model_code,
                    "facility_name": facility_name,
                    "facility_type": facility_type,
                    "latitude": latitude,
                    "longitude": longitude,
                    "customers_assigned": int(customers_assigned),
                    "packages_assigned": float(packages_assigned),
                    "packages_supplied": float(supplied_packages),
                    "supply_route_count": int(
                        supply_for_facility["route_id"].nunique()
                        if not supply_for_facility.empty
                        else 0
                    ),
                    "supply_visit_count": int(len(supply_for_facility)),
                    "last_mile_route_count": int(
                        len(last_mile_for_facility)
                    ),
                    "last_mile_distance_km": float(last_mile_km),
                    "last_mile_duration_min": float(
                        last_mile_duration_min
                    ),
                    "facility_available_time": (
                        facility_available_at.isoformat(
                            timespec="minutes"
                        )
                        if pd.notna(facility_available_at)
                        else ""
                    ),
                    "last_service_completion_time": (
                        last_service_completion.isoformat(
                            timespec="minutes"
                        )
                        if pd.notna(last_service_completion)
                        else ""
                    ),
                    "last_mile_completion_time": (
                        last_mile_completion.isoformat(
                            timespec="minutes"
                        )
                        if pd.notna(last_mile_completion)
                        else ""
                    ),
                }
            )

    return pd.DataFrame(result_rows)



def build_route_detail_rows(
    *,
    city: str,
    neighborhood_name: str,
    model_code: str,
    leg: str,
    vehicle_type: str,
    depot_name: str,
    plan: "OsrmRoutePlan",
    clients: pd.DataFrame,
    stop_label_column: str | None = None,
    depot_latitude: float | None = None,
    depot_longitude: float | None = None,
    add_geometry: bool = False,
) -> list[dict]:
    """Convert a route plan into one auditable CSV record per route."""

    rows = []
    normalized_clients = clients.reset_index(drop=True)

    profile = VEHICLE_OSRM_PROFILE.get(vehicle_type, "driving")
    geometry_enabled = (
        add_geometry
        and depot_latitude is not None
        and depot_longitude is not None
    )

    for route_number, route in enumerate(plan.routes, start=1):
        client_rows = normalized_clients.iloc[
            [client_index - 1 for client_index in route]
        ]

        if stop_label_column and stop_label_column in client_rows.columns:
            stop_labels = client_rows[stop_label_column].astype(str).tolist()
        elif "customer_id" in client_rows.columns:
            stop_labels = client_rows["customer_id"].astype(str).tolist()
        else:
            stop_labels = [str(client_index) for client_index in route]

        geometry_wkt = ""
        if geometry_enabled:
            stop_coords = list(
                zip(
                    client_rows["Longitude"].astype(float),
                    client_rows["Latitude"].astype(float),
                )
            )
            ordered = [
                (depot_longitude, depot_latitude),
                *stop_coords,
                (depot_longitude, depot_latitude),
            ]
            try:
                line = osrm_route_geometry(
                    ordered,
                    host=get_osrm_host(city, profile),
                    profile=profile,
                )
                geometry_wkt = (
                    "LINESTRING ("
                    + ", ".join(f"{lon} {lat}" for lon, lat in line)
                    + ")"
                )
            except Exception as exc:
                print(
                    f"[aviso] geometría OSRM falló en "
                    f"{model_code}/{leg} ruta {route_number}: {exc}"
                )
                geometry_wkt = ""

        stop_latitudes = (
            client_rows["Latitude"].astype(float).tolist()
            if "Latitude" in client_rows.columns
            else []
        )
        stop_longitudes = (
            client_rows["Longitude"].astype(float).tolist()
            if "Longitude" in client_rows.columns
            else []
        )
        stop_package_loads = (
            client_rows["Demand"].astype(float).tolist()
            if "Demand" in client_rows.columns
            else [0.0] * len(route)
        )
        arrival_offsets = (
            plan.route_stop_arrival_offsets_min[route_number - 1]
            if len(plan.route_stop_arrival_offsets_min) >= route_number
            else []
        )
        service_end_offsets = (
            plan.route_stop_service_end_offsets_min[route_number - 1]
            if (
                len(plan.route_stop_service_end_offsets_min)
                >= route_number
            )
            else []
        )

        rows.append(
            {
                "city": city,
                "neighborhood": neighborhood_name,
                "model": model_code,
                "leg": leg,
                "route_id": (
                    f"{model_code}_{neighborhood_name}_{leg}_"
                    f"{depot_name}_{route_number}"
                ),
                "vehicle_type": vehicle_type,
                "depot": depot_name,
                "route_number": route_number,
                "stop_count": len(route),
                "package_load": plan.route_loads[route_number - 1],
                "vehicle_capacity": plan.vehicle_capacity,
                "distance_km": plan.route_distances_km[route_number - 1],
                "duration_min": plan.route_durations_min[route_number - 1],
                "start_handling_min": plan.route_start_time_per_route_min,
                "stop_service_min": len(route) * SERVICE_TIME_PER_STOP_MIN,
                "routing_algorithm": plan.routing_algorithm,
                "routing_runtime_seconds": plan.routing_runtime_seconds,
                "initial_distance_km": plan.initial_distance_km,
                "improvement_distance_km": plan.improvement_distance_km,
                "improvement_percent": plan.improvement_percent,
                "cws_allow_route_reversal": plan.cws_allow_route_reversal,
                "cws_initial_distance_km": plan.cws_initial_distance_km,
                "cws_initial_route_count": plan.cws_initial_route_count,
                "cws_runtime_seconds": plan.cws_runtime_seconds,
                "ils_final_distance_km": plan.ils_final_distance_km,
                "ils_final_route_count": plan.ils_final_route_count,
                "ils_improvement_km": plan.ils_improvement_km,
                "ils_improvement_percent": plan.ils_improvement_percent,
                "ils_runtime_seconds": plan.ils_runtime_seconds,
                "ils_iterations_completed": plan.ils_iterations_completed,
                "ils_iterations_without_improvement": (
                    plan.ils_iterations_without_improvement
                ),
                "unroutable_customer_count": plan.unroutable_customer_count,
                "unroutable_package_count": plan.unroutable_package_count,
                "unroutable_customer_ids": ";".join(plan.unroutable_customer_ids),
                "traffic_profile": plan.traffic_profile,
                "traffic_duration_multiplier": (
                    plan.traffic_duration_multiplier
                ),
                "traffic_source": plan.traffic_source,
                "time_traffic_profile": plan.time_traffic_profile,
                "simulation_date": plan.simulation_date,
                "shift_start_time": plan.shift_start_time,
                "shift_end_time": plan.shift_end_time,
                "route_start_datetime": (
                    plan.route_start_datetimes[route_number - 1]
                    if len(plan.route_start_datetimes) >= route_number
                    else ""
                ),
                "route_end_datetime": (
                    plan.route_end_datetimes[route_number - 1]
                    if len(plan.route_end_datetimes) >= route_number
                    else ""
                ),
                "base_travel_time_min": (
                    plan.route_base_travel_times_min[route_number - 1]
                    if len(plan.route_base_travel_times_min) >= route_number
                    else plan.route_durations_min[route_number - 1]
                ),
                "time_dependent_travel_time_min": (
                    plan.route_adjusted_travel_times_min[route_number - 1]
                    if len(plan.route_adjusted_travel_times_min) >= route_number
                    else plan.route_durations_min[route_number - 1]
                ),
                "traffic_delay_min": (
                    plan.route_traffic_delays_min[route_number - 1]
                    if len(plan.route_traffic_delays_min) >= route_number
                    else 0.0
                ),
                "shift_feasible": (
                    plan.route_shift_feasible[route_number - 1]
                    if len(plan.route_shift_feasible) >= route_number
                    else True
                ),
                "stop_sequence": " -> ".join(stop_labels),
                "geometry_wkt": geometry_wkt,
                "_stop_ids": stop_labels,
                "_stop_latitudes": stop_latitudes,
                "_stop_longitudes": stop_longitudes,
                "_stop_package_loads": stop_package_loads,
                "_stop_arrival_offsets_min": arrival_offsets,
                "_stop_service_end_offsets_min": (
                    service_end_offsets
                ),
            }
        )

    return rows
