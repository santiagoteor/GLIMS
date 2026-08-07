from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class OsrmMatrixValidationReport:
    invalid_distance_cells: int
    invalid_duration_cells: int
    affected_matrix_indices: tuple[int, ...]
    affected_client_positions: tuple[int, ...]

    @property
    def affected_point_count(self) -> int:
        return len(self.affected_matrix_indices)

    @property
    def affected_client_count(self) -> int:
        return len(self.affected_client_positions)


class OsrmMatrixValidationError(RuntimeError):
    """Raised when an OSRM table contains non-finite relationships."""


def analyze_osrm_matrices(
    distance_matrix: np.ndarray,
    duration_matrix: np.ndarray,
) -> OsrmMatrixValidationReport:
    """Return diagnostics for NaN/Inf values in OSRM distance/time matrices."""

    distances = np.asarray(distance_matrix, dtype=float)
    durations = np.asarray(duration_matrix, dtype=float)

    if distances.ndim != 2 or durations.ndim != 2:
        raise ValueError("OSRM distance and duration matrices must be two-dimensional.")

    if distances.shape != durations.shape:
        raise ValueError(
            "OSRM distance and duration matrices must have the same shape. "
            f"Got {distances.shape} and {durations.shape}."
        )

    invalid_distance = ~np.isfinite(distances)
    invalid_duration = ~np.isfinite(durations)
    invalid_any = invalid_distance | invalid_duration

    affected_rows = np.flatnonzero(invalid_any.any(axis=1))
    affected_columns = np.flatnonzero(invalid_any.any(axis=0))
    affected_indices = np.union1d(affected_rows, affected_columns).astype(int)

    # Matrix index 0 is the depot. Client matrix index k corresponds to
    # zero-based clients.iloc[k - 1].
    affected_client_positions = tuple(
        int(matrix_index - 1)
        for matrix_index in affected_indices
        if matrix_index != 0
    )

    return OsrmMatrixValidationReport(
        invalid_distance_cells=int(invalid_distance.sum()),
        invalid_duration_cells=int(invalid_duration.sum()),
        affected_matrix_indices=tuple(int(value) for value in affected_indices),
        affected_client_positions=affected_client_positions,
    )


def validate_osrm_matrices(
    distance_matrix: np.ndarray,
    duration_matrix: np.ndarray,
    *,
    clients: pd.DataFrame,
    transport_mode: str,
    max_examples: int = 20,
) -> OsrmMatrixValidationReport:
    """
    Fail before routing if OSRM returned NaN/Inf values.

    The exception includes the affected matrix/client indices and a short
    sample of the original customer coordinates so a bad demand point can be
    inspected in QGIS without waiting for CWS/ILS to finish.
    """

    report = analyze_osrm_matrices(distance_matrix, duration_matrix)

    if (
        report.invalid_distance_cells == 0
        and report.invalid_duration_cells == 0
    ):
        print(
            "OSRM matrix validation passed: "
            "all distance and duration values are finite."
        )
        return report

    print(
        "OSRM matrix validation FAILED: "
        f"{report.invalid_distance_cells} invalid distance cells, "
        f"{report.invalid_duration_cells} invalid duration cells, "
        f"{report.affected_point_count} affected matrix points."
    )

    if 0 in report.affected_matrix_indices:
        print("  The depot (matrix index 0) is involved in invalid OSRM relations.")

    valid_client_positions = [
        position
        for position in report.affected_client_positions
        if 0 <= position < len(clients)
    ]

    if valid_client_positions:
        preview_positions = valid_client_positions[:max_examples]
        preview = clients.iloc[preview_positions].copy()
        preview.insert(
            0,
            "matrix_index",
            [position + 1 for position in preview_positions],
        )
        preview.insert(1, "client_position", preview_positions)

        preferred_columns = [
            "matrix_index",
            "client_position",
            "customer_id",
            "Latitude",
            "Longitude",
            "Demand",
        ]
        preview_columns = [
            column for column in preferred_columns if column in preview.columns
        ]

        print("Affected client sample:")
        print(preview[preview_columns].to_string(index=False))

        if len(valid_client_positions) > max_examples:
            print(
                f"  ... and {len(valid_client_positions) - max_examples} "
                "additional affected clients."
            )

    affected_text = ", ".join(
        str(index) for index in report.affected_matrix_indices[:50]
    )
    if len(report.affected_matrix_indices) > 50:
        affected_text += ", ..."

    raise OsrmMatrixValidationError(
        "OSRM returned non-finite distance/duration values before routing. "
        "The simulation was stopped intentionally to prevent NaN values from "
        "propagating through CWS/ILS and failing hours later. "
        f"Transport mode: {transport_mode}. "
        f"Affected matrix indices: [{affected_text}]. "
        "Matrix index 0 is the depot; client matrix index k corresponds to "
        "clients.iloc[k - 1]. Inspect the printed coordinates and decide "
        "whether those demand points should be corrected/snapped or removed "
        "from the input instance."
    )
