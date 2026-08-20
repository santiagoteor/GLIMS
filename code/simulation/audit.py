from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

import pandas as pd

from code.routing.osrm_client import get_osrm_host, get_osrm_snap_records


CUSTOMER_ID_COLUMNS = ("customer_id", "Customer_ID", "ID", "id")


def _customer_id_column(df: pd.DataFrame) -> str:
    for column in CUSTOMER_ID_COLUMNS:
        if column in df.columns:
            return column
    raise ValueError(
        "Customer routing audit requires a stable customer identifier column. "
        f"Expected one of: {CUSTOMER_ID_COLUMNS}."
    )


def _normalize_id(value) -> str:
    """
    Convert customer identifiers to a stable string representation.

    CSV/pandas round-trips can occasionally turn integer identifiers into
    values such as ``123.0``. This helper normalizes those cases so the audit
    does not report false missing/duplicate customers.
    """
    if pd.isna(value):
        return ""

    text = str(value).strip()

    try:
        numeric = float(text)
        if numeric.is_integer():
            return str(int(numeric))
    except (TypeError, ValueError):
        pass

    return text


def _parse_stop_sequence(value) -> list[str]:
    if value is None or pd.isna(value):
        return []

    text = str(value).strip()
    if not text:
        return []

    return [
        _normalize_id(token)
        for token in text.split(" -> ")
        if _normalize_id(token)
    ]


def build_unroutable_customer_rows(
    *,
    city: str,
    neighborhood_name: str,
    original_clients: pd.DataFrame,
    excluded_positions: Iterable[int],
) -> list[dict]:
    """Create one auditable row for every intentionally excluded customer."""
    original = original_clients.reset_index(drop=True)
    id_column = _customer_id_column(original)
    rows: list[dict] = []

    for position in sorted(set(int(pos) for pos in excluded_positions)):
        if not 0 <= position < len(original):
            continue

        customer = original.iloc[position]
        rows.append(
            {
                "city": city,
                "neighborhood": neighborhood_name,
                "customer_id": _normalize_id(customer[id_column]),
                "original_customer_position": position,
                "demand": float(customer.get("Demand", 0.0)),
                "latitude": customer.get("Latitude", ""),
                "longitude": customer.get("Longitude", ""),
                "reason": "osrm_unroutable",
                "action": "excluded_from_all_models",
            }
        )

    return rows


def build_osrm_snapping_audit_rows(
    *,
    city: str,
    neighborhood_name: str,
    original_clients: pd.DataFrame,
    excluded_positions: Iterable[int] = (),
    customer_route_audit_rows: Iterable[dict] = (),
) -> list[dict]:
    """Build an informational OSRM snapping audit without changing routing."""
    original = original_clients.reset_index(drop=True)
    id_column = _customer_id_column(original)
    excluded = {int(position) for position in excluded_positions}

    # Routing is completed before this audit is built, so attach the route
    # assignment without changing OSRM snapping or optimization behavior.
    # Keying by (model, leg, customer_id) prevents cross-model assignments.
    route_lookup: dict[tuple[str, str, str], tuple[str, str]] = {}
    for audit_row in customer_route_audit_rows:
        key = (
            str(audit_row.get("model", "")),
            str(audit_row.get("leg", "")),
            _normalize_id(audit_row.get("customer_id", "")),
        )
        route_lookup[key] = (
            str(audit_row.get("route_ids", "")),
            str(audit_row.get("route_numbers", "")),
        )

    coords = list(
        zip(
            original["Longitude"].astype(float),
            original["Latitude"].astype(float),
        )
    )

    audit_specs = (
        ("M1", "direct_delivery", "driving"),
        ("M2", "direct_delivery", "driving"),
        ("M3", "cycling_last_mile", "cycling"),
        ("M4", "walking_last_mile", "walking"),
        ("M5", "customer_collection", "walking"),
    )

    rows: list[dict] = []
    for model, leg, profile in audit_specs:
        records = get_osrm_snap_records(
            coords,
            host=get_osrm_host(city, profile),
            profile=profile,
            include_missing=True,
        )

        for position, (customer, record) in enumerate(
            zip(original.to_dict("records"), records)
        ):
            customer_id = _normalize_id(customer[id_column])
            route_id, route_number = route_lookup.get(
                (model, leg, customer_id),
                ("", ""),
            )

            row = {
                "city": city,
                "neighborhood": neighborhood_name,
                "model": model,
                "leg": leg,
                "osrm_profile": profile,
                "route_id": route_id,
                "route_number": route_number,
                "customer_id": customer_id,
                "original_customer_position": position,
                "demand": float(customer.get("Demand", 0.0)),
                "original_latitude": float(customer["Latitude"]),
                "original_longitude": float(customer["Longitude"]),
                "excluded_as_unroutable": position in excluded,
                "snap_available": record is not None,
                "snapped_latitude": "",
                "snapped_longitude": "",
                "snap_distance_m": "",
            }
            if record is not None:
                row.update(
                    {
                        "snapped_latitude": record["snapped_latitude"],
                        "snapped_longitude": record["snapped_longitude"],
                        "snap_distance_m": record["snap_distance_m"],
                    }
                )
            rows.append(row)

    return rows


def audit_customer_routes(
    *,
    city: str,
    neighborhood_name: str,
    model: str,
    leg: str,
    original_clients: pd.DataFrame,
    routable_clients: pd.DataFrame,
    route_detail_rows: list[dict],
    excluded_positions: Iterable[int] = (),
) -> tuple[list[dict], list[dict], dict]:
    """
    Audit conservation and uniqueness of customer assignments.

    The audit is informational only: it never raises because a routing result
    contains missing or duplicated customers. It returns detailed rows that can
    be exported and inspected after the experiment.
    """
    original = original_clients.reset_index(drop=True)
    routable = routable_clients.reset_index(drop=True)

    original_id_column = _customer_id_column(original)
    routable_id_column = _customer_id_column(routable)

    original_ids = [
        _normalize_id(value)
        for value in original[original_id_column].tolist()
    ]
    routable_ids = [
        _normalize_id(value)
        for value in routable[routable_id_column].tolist()
    ]

    original_demand_by_id = {
        _normalize_id(row[original_id_column]): float(row.get("Demand", 0.0))
        for _, row in original.iterrows()
    }
    routable_id_set = set(routable_ids)

    excluded_positions = sorted(set(int(pos) for pos in excluded_positions))
    excluded_ids = {
        original_ids[position]
        for position in excluded_positions
        if 0 <= position < len(original_ids)
    }

    occurrences: Counter[str] = Counter()
    customer_routes: dict[str, list[str]] = defaultdict(list)
    customer_route_numbers: dict[str, list[int]] = defaultdict(list)
    route_summary_rows: list[dict] = []

    relevant_rows = [
        row
        for row in route_detail_rows
        if str(row.get("leg", "")) == leg
    ]

    for row in relevant_rows:
        customer_ids = _parse_stop_sequence(row.get("stop_sequence"))
        occurrences.update(customer_ids)

        route_id = str(row.get("route_id", ""))
        route_number = row.get("route_number", "")

        for customer_id in customer_ids:
            customer_routes[customer_id].append(route_id)
            customer_route_numbers[customer_id].append(route_number)

        unique_ids = set(customer_ids)
        route_summary_rows.append(
            {
                "city": city,
                "neighborhood": neighborhood_name,
                "model": model,
                "leg": leg,
                "route_id": route_id,
                "route_number": route_number,
                "customer_count": len(customer_ids),
                "unique_customer_count": len(unique_ids),
                "duplicate_customer_occurrences": (
                    len(customer_ids) - len(unique_ids)
                ),
                "package_count": float(row.get("package_load", 0.0)),
            }
        )

    customer_audit_rows: list[dict] = []

    for original_position, customer_id in enumerate(original_ids):
        demand = original_demand_by_id.get(customer_id, 0.0)

        if customer_id in excluded_ids:
            status = "unroutable"
            assignment_count = 0
        else:
            assignment_count = int(occurrences.get(customer_id, 0))
            if assignment_count == 0:
                status = "unassigned"
            elif assignment_count == 1:
                status = "assigned"
            else:
                status = "duplicated"

        customer_audit_rows.append(
            {
                "city": city,
                "neighborhood": neighborhood_name,
                "model": model,
                "leg": leg,
                "customer_id": customer_id,
                "original_customer_position": original_position,
                "demand": demand,
                "assignment_count": assignment_count,
                "route_ids": ";".join(customer_routes.get(customer_id, [])),
                "route_numbers": ";".join(
                    str(value)
                    for value in customer_route_numbers.get(customer_id, [])
                ),
                "status": status,
            }
        )

    unexpected_ids = sorted(
        customer_id
        for customer_id in occurrences
        if customer_id not in routable_id_set
    )

    assigned_unique_ids = {
        customer_id
        for customer_id in routable_ids
        if occurrences.get(customer_id, 0) > 0
    }
    missing_ids = [
        customer_id
        for customer_id in routable_ids
        if occurrences.get(customer_id, 0) == 0
    ]
    duplicated_ids = [
        customer_id
        for customer_id in routable_ids
        if occurrences.get(customer_id, 0) > 1
    ]

    input_packages = float(original["Demand"].sum())
    excluded_packages = float(
        sum(original_demand_by_id.get(customer_id, 0.0) for customer_id in excluded_ids)
    )
    expected_routable_packages = float(routable["Demand"].sum())

    assigned_package_occurrences = float(
        sum(
            original_demand_by_id.get(customer_id, 0.0) * count
            for customer_id, count in occurrences.items()
            if customer_id in routable_id_set
        )
    )

    customer_balance_ok = (
        len(missing_ids) == 0
        and len(duplicated_ids) == 0
        and len(unexpected_ids) == 0
        and len(assigned_unique_ids) == len(routable_ids)
    )
    package_balance_ok = (
        abs(assigned_package_occurrences - expected_routable_packages) < 1e-9
        and len(duplicated_ids) == 0
        and len(unexpected_ids) == 0
    )

    summary_row = {
        "city": city,
        "neighborhood": neighborhood_name,
        "model": model,
        "leg": leg,
        "input_customers": len(original),
        "excluded_unroutable_customers": len(excluded_ids),
        "expected_routable_customers": len(routable),
        "assigned_unique_customers": len(assigned_unique_ids),
        "assigned_customer_occurrences": int(
            sum(occurrences.get(customer_id, 0) for customer_id in routable_ids)
        ),
        "unassigned_customers": len(missing_ids),
        "duplicated_customers": len(duplicated_ids),
        "unexpected_customer_ids": len(unexpected_ids),
        "input_packages": input_packages,
        "excluded_unroutable_packages": excluded_packages,
        "expected_routable_packages": expected_routable_packages,
        "assigned_package_occurrences": assigned_package_occurrences,
        "customer_balance_ok": customer_balance_ok,
        "package_balance_ok": package_balance_ok,
        "audit_status": "OK" if customer_balance_ok and package_balance_ok else "WARNING",
        "missing_customer_ids": ";".join(missing_ids),
        "duplicated_customer_ids": ";".join(duplicated_ids),
        "unexpected_customer_id_values": ";".join(unexpected_ids),
    }

    print(
        f"Routing audit [{model}/{leg}]: "
        f"{len(assigned_unique_ids)}/{len(routable)} routable customers assigned | "
        f"missing={len(missing_ids)} | duplicated={len(duplicated_ids)} | "
        f"packages={assigned_package_occurrences:g}/{expected_routable_packages:g} | "
        f"status={summary_row['audit_status']}"
    )

    return customer_audit_rows, route_summary_rows, summary_row
