from __future__ import annotations

from datetime import datetime
from typing import Mapping

import pandas as pd

from code.simulation.exporters import build_route_stops_frame


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
    "network_km",
    "last_meter_access_km",
    "last_meter_access_time_min",
    "numero_viajes",
    "emisiones_co2_kg",
    "emisiones_nox_kg",
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
    "costo_distancia_eur",
    "costo_laboral_eur",
    "costo_facility_fijo_eur",
    "costo_almacen_fijo_eur",
    "costo_manipulacion_eur",
    "costo_vehiculo_fijo_eur",
    "costo_capex_eur",
    "costo_tiempo_cliente_eur",
    "costo_carbono_eur",
    "co2_kg_por_paquete",
    "nox_g_por_paquete",
    "co2_kg_por_km",
    "direct_km",
    "direct_trip_count",
    "supply_km",
    "supply_trip_count",
    "last_mile_km",
    "last_mile_trip_count",
    "latest_last_service_time",
    "shift_feasible",
    "last_service_deadline_enabled",
    "last_service_margin_min",
    "last_service_cutoff_time",
    "services_after_deadline",
    "last_service_deadline_feasible",
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



def _last_service_metrics(
    detail_frame: pd.DataFrame | None,
    *,
    model_code: str,
    shift_end: datetime,
    deadline_enabled: bool,
    margin_min: float,
) -> dict:
    """Summarize the last controlled service for the configured deadline."""

    empty = {
        "latest_last_service_time": pd.NA,
        "shift_feasible": pd.NA,
        "last_service_deadline_enabled": bool(deadline_enabled),
        "last_service_margin_min": float(margin_min),
        "last_service_cutoff_time": pd.NA,
        "services_after_deadline": pd.NA,
        "last_service_deadline_feasible": pd.NA,
    }

    if detail_frame is None or detail_frame.empty:
        return empty

    if model_code in {"M1", "M2"}:
        deadline_leg = "direct_delivery"
    elif model_code in {"M3", "M4", "M5"}:
        deadline_leg = "facility_supply"
    else:
        return empty

    relevant = detail_frame.loc[
        detail_frame["leg"].eq(deadline_leg)
    ].copy()
    if relevant.empty:
        return empty

    stop_frame = build_route_stops_frame(
        {model_code: relevant}
    )
    if stop_frame.empty:
        return empty

    service_ends = pd.to_datetime(
        stop_frame["service_end_datetime"].replace("", pd.NA),
        errors="coerce",
    )
    latest = (
        service_ends.max()
        if service_ends.notna().any()
        else pd.NaT
    )

    cutoff = shift_end - pd.Timedelta(minutes=float(margin_min))

    if deadline_enabled:
        after_deadline = int(
            (service_ends.dropna() > cutoff).sum()
        )
        deadline_feasible = after_deadline == 0
        cutoff_text = cutoff.isoformat(timespec="minutes")
    else:
        after_deadline = pd.NA
        deadline_feasible = pd.NA
        cutoff_text = pd.NA

    route_ends = _parse_datetime_series(
        relevant["route_end_datetime"]
        if "route_end_datetime" in relevant.columns
        else pd.Series(dtype="object")
    )
    shift_feasible = (
        bool((route_ends.dropna() <= shift_end).all())
        if len(route_ends.dropna()) == len(relevant)
        and len(relevant) > 0
        else pd.NA
    )

    return {
        "latest_last_service_time": (
            latest.isoformat(timespec="minutes")
            if pd.notna(latest)
            else pd.NA
        ),
        "shift_feasible": shift_feasible,
        "last_service_deadline_enabled": bool(deadline_enabled),
        "last_service_margin_min": float(margin_min),
        "last_service_cutoff_time": cutoff_text,
        "services_after_deadline": after_deadline,
        "last_service_deadline_feasible": deadline_feasible,
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
    last_service_deadline_enabled: bool,
    last_service_margin_min: float,
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
        neighborhood_name = str(row.get("barrio", "")).strip()
        detail = model_details.get(model_code)

        # ``model_details`` contains the concatenated routes of every simulated
        # neighborhood. Summary timing metrics are neighborhood-level, so each
        # result row must be evaluated only against its own route-detail rows.
        # Without this filter, every neighborhood of a given model receives the
        # same city-wide first departure / last completion timestamps.
        if (
            detail is not None
            and not detail.empty
            and "neighborhood" in detail.columns
        ):
            detail = detail.loc[
                detail["neighborhood"].astype(str).str.strip().eq(
                    neighborhood_name
                )
            ].copy()

        # M5 ends operationally when the operator completes PUDO supply.
        # Customer collection remains outside the controlled system timeline.
        timeline_detail = detail
        if (
            model_code == "M5"
            and detail is not None
            and not detail.empty
            and "leg" in detail.columns
        ):
            timeline_detail = detail.loc[
                detail["leg"].eq("facility_supply")
            ].copy()

        metrics = _timeline_metrics(
            timeline_detail,
            shift_end=shift_end,
        )
        for key, value in metrics.items():
            output.at[row_index, key] = value

        last_service_metrics = _last_service_metrics(
            detail,
            model_code=model_code,
            shift_end=shift_end,
            deadline_enabled=last_service_deadline_enabled,
            margin_min=last_service_margin_min,
        )
        for key, value in last_service_metrics.items():
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
