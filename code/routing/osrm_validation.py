from __future__ import annotations

import numpy as np
import pandas as pd


class OsrmMatrixValidationError(RuntimeError):
    """Raised when OSRM matrices contain non-finite values."""


def validate_osrm_matrices(
    distance_matrix: np.ndarray,
    duration_matrix: np.ndarray,
    *,
    clients: pd.DataFrame | None = None,
    transport_mode: str = "unknown",
    max_points_to_print: int = 20,
) -> None:
    """
    Validate OSRM distance/duration matrices before routing.

    The function intentionally stops the simulation if OSRM returned NaN/Inf
    values, preventing them from propagating through CWS/ILS.

    Diagnostics rank matrix points by the number of invalid incoming/outgoing
    relations. This is more useful than simply reporting every row/column that
    contains a NaN, because a single disconnected point can make almost every
    other point appear "affected".

    Matrix convention:
        index 0 -> depot
        index k -> clients.iloc[k - 1], for k >= 1
    """
    distance_matrix = np.asarray(distance_matrix, dtype=float)
    duration_matrix = np.asarray(duration_matrix, dtype=float)

    if distance_matrix.shape != duration_matrix.shape:
        raise OsrmMatrixValidationError(
            "OSRM distance and duration matrices have different shapes: "
            f"{distance_matrix.shape} vs {duration_matrix.shape}."
        )

    if distance_matrix.ndim != 2:
        raise OsrmMatrixValidationError(
            f"OSRM matrices must be 2-dimensional; got {distance_matrix.ndim}D."
        )

    if distance_matrix.shape[0] != distance_matrix.shape[1]:
        raise OsrmMatrixValidationError(
            "Expected square OSRM matrices before routing; "
            f"got shape {distance_matrix.shape}."
        )

    invalid_distance = ~np.isfinite(distance_matrix)
    invalid_duration = ~np.isfinite(duration_matrix)
    invalid_mask = invalid_distance | invalid_duration

    invalid_distance_count = int(invalid_distance.sum())
    invalid_duration_count = int(invalid_duration.sum())
    invalid_cell_count = int(invalid_mask.sum())

    if invalid_cell_count == 0:
        print(
            "OSRM matrix validation passed: "
            f"{distance_matrix.shape[0]} points, no NaN/Inf values."
        )
        return

    invalid_outgoing = invalid_mask.sum(axis=1).astype(int)
    invalid_incoming = invalid_mask.sum(axis=0).astype(int)
    invalid_total = invalid_outgoing + invalid_incoming

    ranked_indices = np.argsort(invalid_total)[::-1]
    ranked_indices = [
        int(idx)
        for idx in ranked_indices
        if invalid_total[idx] > 0
    ]

    print("\n" + "=" * 72)
    print("OSRM MATRIX VALIDATION FAILED")
    print("=" * 72)
    print(f"Transport mode: {transport_mode}")
    print(f"Matrix shape: {distance_matrix.shape}")
    print(f"Invalid distance cells: {invalid_distance_count}")
    print(f"Invalid duration cells: {invalid_duration_count}")
    print(f"Invalid cells (distance OR duration): {invalid_cell_count}")
    print(
        "Points touching at least one invalid relation: "
        f"{len(ranked_indices)}"
    )

    print("\nPoints with the most invalid OSRM relations:")
    print(
        "(A disconnected point can cause many otherwise valid points to "
        "appear affected; focus on the highest counts.)"
    )

    for rank, matrix_index in enumerate(
        ranked_indices[:max_points_to_print],
        start=1,
    ):
        outgoing = int(invalid_outgoing[matrix_index])
        incoming = int(invalid_incoming[matrix_index])
        total = int(invalid_total[matrix_index])

        print(
            f"\n  #{rank} matrix_index={matrix_index} | "
            f"outgoing={outgoing} | incoming={incoming} | total={total}"
        )

        if matrix_index == 0:
            print("     point_type=depot")
            continue

        client_row = matrix_index - 1

        if clients is None:
            print(f"     client_row={client_row}")
            continue

        if not (0 <= client_row < len(clients)):
            print(
                f"     client_row={client_row} "
                "(outside provided clients DataFrame)"
            )
            continue

        client = clients.iloc[client_row]

        details = [f"client_row={client_row}"]

        for column in (
            "Customer_ID",
            "customer_id",
            "ID",
            "id",
            "Latitude",
            "Longitude",
            "Demand",
            "Address",
            "Street",
        ):
            if column in clients.columns:
                details.append(f"{column}={client[column]}")

        print("     " + " | ".join(details))

    # Also highlight points that are invalid against a large fraction of the
    # matrix. These are much stronger candidates for being truly disconnected
    # than points that merely touch one bad relation.
    n_points = distance_matrix.shape[0]
    strong_threshold = max(10, int(0.25 * n_points))

    strong_candidates = [
        idx
        for idx in ranked_indices
        if (
            invalid_outgoing[idx] >= strong_threshold
            or invalid_incoming[idx] >= strong_threshold
        )
    ]

    print("\nLikely disconnected/problematic points:")
    if strong_candidates:
        print(
            f"  Threshold: >= {strong_threshold} invalid incoming or "
            "outgoing relations."
        )
        print(
            "  Matrix indices: "
            + ", ".join(map(str, strong_candidates[:max_points_to_print]))
        )
        if len(strong_candidates) > max_points_to_print:
            print(
                f"  ... plus "
                f"{len(strong_candidates) - max_points_to_print} more."
            )
    else:
        print(
            "  No point crosses the diagnostic threshold. "
            "The invalid relations may be sparse/directional rather than "
            "caused by a fully disconnected point."
        )

    print("=" * 72 + "\n")

    candidate_text = (
        ", ".join(map(str, strong_candidates[:max_points_to_print]))
        if strong_candidates
        else "none identified automatically"
    )

    raise OsrmMatrixValidationError(
        "OSRM returned non-finite distance/duration values before routing. "
        "The simulation was stopped intentionally to prevent NaN values from "
        "propagating through CWS/ILS and failing hours later. "
        f"Transport mode: {transport_mode}. "
        f"Invalid matrix cells: {invalid_cell_count}. "
        f"Likely problematic matrix indices: {candidate_text}. "
        "Matrix index 0 is the depot; client matrix index k corresponds to "
        "clients.iloc[k - 1]. Review the diagnostics printed immediately "
        "above this exception."
    )



def sanitize_osrm_routing_inputs(
    distance_matrix: np.ndarray,
    duration_matrix: np.ndarray,
    *,
    clients: pd.DataFrame,
    client_demands=None,
    transport_mode: str = "unknown",
    strong_fraction: float = 0.25,
) -> tuple[
    np.ndarray,
    np.ndarray,
    pd.DataFrame,
    np.ndarray,
    list[int],
    pd.DataFrame,
]:
    """
    Remove strongly disconnected client points from square OSRM matrices.

    This helper is intentionally conservative. A point is removed only when
    at least ``strong_fraction`` of all matrix relations are invalid in either
    the incoming or outgoing direction. After removal, the reduced matrices
    must contain only finite values; otherwise an exception is raised instead
    of silently masking sparse routing problems.

    Returns
    -------
    filtered_distance_matrix, filtered_duration_matrix, filtered_clients,
    filtered_demands, excluded_client_positions, excluded_clients

    ``excluded_client_positions`` contains zero-based positions in the
    original ``clients`` DataFrame.
    """
    distance_matrix = np.asarray(distance_matrix, dtype=float)
    duration_matrix = np.asarray(duration_matrix, dtype=float)

    if distance_matrix.shape != duration_matrix.shape:
        raise OsrmMatrixValidationError(
            "OSRM distance and duration matrices have different shapes: "
            f"{distance_matrix.shape} vs {duration_matrix.shape}."
        )

    expected_size = len(clients) + 1
    if distance_matrix.shape != (expected_size, expected_size):
        raise OsrmMatrixValidationError(
            "OSRM matrix size does not match clients + depot: "
            f"matrix={distance_matrix.shape}, clients={len(clients)}."
        )

    demands = (
        np.ones(len(clients), dtype=float)
        if client_demands is None
        else np.asarray(client_demands, dtype=float)
    )
    if demands.shape != (len(clients),):
        raise ValueError(
            "client_demands must contain one value per client before "
            "OSRM routability filtering."
        )

    invalid_mask = (
        ~np.isfinite(distance_matrix)
        | ~np.isfinite(duration_matrix)
    )

    if not invalid_mask.any():
        print(
            "OSRM matrix validation passed: "
            f"{expected_size} points, no NaN/Inf values."
        )
        return (
            distance_matrix,
            duration_matrix,
            clients.reset_index(drop=True).copy(),
            demands.copy(),
            [],
            clients.iloc[0:0].copy(),
        )

    invalid_outgoing = invalid_mask.sum(axis=1).astype(int)
    invalid_incoming = invalid_mask.sum(axis=0).astype(int)
    threshold = max(10, int(strong_fraction * expected_size))

    problematic_matrix_indices = [
        matrix_index
        for matrix_index in range(1, expected_size)  # never auto-remove depot
        if (
            invalid_outgoing[matrix_index] >= threshold
            or invalid_incoming[matrix_index] >= threshold
        )
    ]

    if not problematic_matrix_indices:
        validate_osrm_matrices(
            distance_matrix,
            duration_matrix,
            clients=clients,
            transport_mode=transport_mode,
        )
        raise AssertionError("validate_osrm_matrices should have raised.")

    excluded_client_positions = [
        matrix_index - 1
        for matrix_index in problematic_matrix_indices
    ]

    keep_client_mask = np.ones(len(clients), dtype=bool)
    keep_client_mask[excluded_client_positions] = False

    keep_matrix_indices = np.concatenate(
        ([0], np.flatnonzero(keep_client_mask) + 1)
    )

    filtered_distance = distance_matrix[np.ix_(keep_matrix_indices, keep_matrix_indices)]
    filtered_duration = duration_matrix[np.ix_(keep_matrix_indices, keep_matrix_indices)]
    filtered_clients = clients.iloc[np.flatnonzero(keep_client_mask)].reset_index(drop=True).copy()
    filtered_demands = demands[keep_client_mask].copy()
    excluded_clients = clients.iloc[excluded_client_positions].copy()

    remaining_invalid = (
        ~np.isfinite(filtered_distance)
        | ~np.isfinite(filtered_duration)
    )

    if remaining_invalid.any():
        print(
            "Strongly disconnected points were identified, but non-finite "
            "OSRM relations remain after removing them. Automatic exclusion "
            "was aborted."
        )
        validate_osrm_matrices(
            filtered_distance,
            filtered_duration,
            clients=filtered_clients,
            transport_mode=transport_mode,
        )
        raise AssertionError("validate_osrm_matrices should have raised.")

    excluded_packages = float(filtered_demands.sum() * 0.0 + demands[~keep_client_mask].sum())

    print("\nOSRM automatic routability filter:")
    print(f"  original customers: {len(clients)}")
    print(f"  excluded customers: {len(excluded_client_positions)}")
    print(f"  retained customers: {len(filtered_clients)}")
    print(f"  excluded packages: {excluded_packages:g}")
    print(f"  diagnostic threshold: {threshold} invalid incoming/outgoing relations")

    id_column = next(
        (
            column
            for column in ("customer_id", "Customer_ID", "ID", "id")
            if column in excluded_clients.columns
        ),
        None,
    )

    for position, (_, row) in zip(
        excluded_client_positions,
        excluded_clients.iterrows(),
    ):
        details = [f"client_position={position}"]
        if id_column is not None:
            details.append(f"{id_column}={row[id_column]}")
        if "Latitude" in excluded_clients.columns:
            details.append(f"lat={row['Latitude']}")
        if "Longitude" in excluded_clients.columns:
            details.append(f"lon={row['Longitude']}")
        if "Demand" in excluded_clients.columns:
            details.append(f"demand={row['Demand']}")
        print("    - " + " | ".join(details))

    return (
        filtered_distance,
        filtered_duration,
        filtered_clients,
        filtered_demands,
        excluded_client_positions,
        excluded_clients,
    )
