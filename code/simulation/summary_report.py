from __future__ import annotations

from datetime import datetime
from typing import Mapping

import pandas as pd


COMPACT_COLUMNS = [
    "ciudad",
    "barrio",
    "model_code",
    "modelo",
    "paquetes",
    "centro_logistico",
    "tipo_punto_ultima_milla",
    "numero_puntos_ultima_milla_usados",
    "km_recorridos",
    "numero_viajes",
    "emisiones_co2_kg",
    "costo_operacion_ruta_eur",
    "costo_servicio_facility_eur",
    "otros_costos_eur",
    "costo_total_eur",
    "costo_por_paquete_eur",
    "system_timeline_complete",
    "system_first_departure_time",
    "system_last_completion_time",
    "system_operation_duration_min",
    "routes_finishing_after_shift",
    "overtime_min",
]

FULL_EXTRA_COLUMNS = [
    "punto_ultima_milla",
    "latitud_punto_ultima_milla",
    "longitud_punto_ultima_milla",
    "estrategia_punto_ultima_milla",
    "demand_scenario",
    "demand_instance_size",
    "demand_instance_id",
    "demand_seed",
    "demand_source_file",
    "experiment_id",
    "routing_algorithm",
    "routing_seed",
    "cws_allow_route_reversal",
    "traffic_profile",
    "traffic_duration_multiplier",
    "traffic_source",
    "time_traffic_profile",
    "simulation_date",
    "simulation_day_of_week",
    "traffic_city_file",
    "shift_start_time_configured",
    "shift_end_time_configured",
    "timed_route_count",
    "untimed_route_count",
]


def _parse_datetime_series(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(series.replace("", pd.NA), errors="coerce")


def _timeline_metrics(
    detail_frame: pd.DataFrame | None,
    *,
    shift_end: datetime,
) -> dict:
    empty = {
        "system_timeline_complete": False,
        "system_first_departure_time": pd.NA,
        "system_last_completion_time": pd.NA,
        "system_operation_duration_min": pd.NA,
        "routes_finishing_after_shift": pd.NA,
        "overtime_min": pd.NA,
        "timed_route_count": 0,
        "untimed_route_count": 0,
    }

    if detail_frame is None or detail_frame.empty:
        return empty

    route_count = len(detail_frame)
    if (
        "route_start_datetime" not in detail_frame.columns
        or "route_end_datetime" not in detail_frame.columns
    ):
        return {**empty, "untimed_route_count": route_count}

    starts = _parse_datetime_series(detail_frame["route_start_datetime"])
    ends = _parse_datetime_series(detail_frame["route_end_datetime"])
    timed_mask = starts.notna() & ends.notna()
    timed_count = int(timed_mask.sum())
    untimed_count = int(route_count - timed_count)
    complete = route_count > 0 and untimed_count == 0

    first_departure = starts[timed_mask].min() if timed_count else pd.NaT
    last_completion = ends[timed_mask].max() if complete else pd.NaT

    if complete:
        operation_duration = (
            (last_completion - first_departure).total_seconds() / 60.0
        )
        after_shift = int((ends[timed_mask] > shift_end).sum())
        overtime = max(
            0.0,
            (last_completion - shift_end).total_seconds() / 60.0,
        )
    else:
        operation_duration = pd.NA
        after_shift = pd.NA
        overtime = pd.NA

    return {
        "system_timeline_complete": complete,
        "system_first_departure_time": (
            first_departure.isoformat(timespec="minutes")
            if pd.notna(first_departure)
            else pd.NA
        ),
        "system_last_completion_time": (
            last_completion.isoformat(timespec="minutes")
            if pd.notna(last_completion)
            else pd.NA
        ),
        "system_operation_duration_min": operation_duration,
        "routes_finishing_after_shift": after_shift,
        "overtime_min": overtime,
        "timed_route_count": timed_count,
        "untimed_route_count": untimed_count,
    }


def build_summary_export(
    frame: pd.DataFrame,
    *,
    model_details: Mapping[str, pd.DataFrame],
    summary_detail: str,
    experiment_id: str,
    routing_algorithm: str,
    routing_seed: int | None,
    cws_allow_route_reversal: bool,
    traffic_profile_name: str,
    traffic_duration_multiplier: float,
    traffic_source: str,
    time_traffic_profile: str,
    simulation_date: str,
    simulation_day_of_week: str,
    traffic_city_file: str,
    shift_start: datetime,
    shift_end: datetime,
) -> pd.DataFrame:
    output = frame.copy()

    output["experiment_id"] = experiment_id
    output["routing_algorithm"] = routing_algorithm
    output["routing_seed"] = routing_seed
    output["cws_allow_route_reversal"] = cws_allow_route_reversal
    output["traffic_profile"] = traffic_profile_name
    output["traffic_duration_multiplier"] = traffic_duration_multiplier
    output["traffic_source"] = traffic_source
    output["time_traffic_profile"] = time_traffic_profile
    output["simulation_date"] = simulation_date
    output["simulation_day_of_week"] = simulation_day_of_week
    output["traffic_city_file"] = traffic_city_file
    output["shift_start_time_configured"] = shift_start.time().isoformat(
        timespec="minutes"
    )
    output["shift_end_time_configured"] = shift_end.time().isoformat(
        timespec="minutes"
    )

    for row_index, row in output.iterrows():
        model_code = str(row.get("model_code", "")).strip()
        detail = model_details.get(model_code)
        metrics = _timeline_metrics(detail, shift_end=shift_end)
        for key, value in metrics.items():
            output.at[row_index, key] = value

    detail = summary_detail.strip().lower()
    if detail not in {"compact", "full"}:
        raise ValueError("summary_detail must be either 'compact' or 'full'.")

    requested_columns = (
        COMPACT_COLUMNS
        if detail == "compact"
        else COMPACT_COLUMNS + FULL_EXTRA_COLUMNS
    )

    for column in requested_columns:
        if column not in output.columns:
            output[column] = pd.NA

    return output[requested_columns].copy()
