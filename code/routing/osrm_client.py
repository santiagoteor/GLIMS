import requests
from code.common.constants import (
    OSRM_PORTS,
    OSRM_PROBE_COORDS,
    OSRM_TABLE_BLOCK_SIZE,
    OSRM_TABLE_MIN_BLOCK_SIZE,
)
import numpy as np

def get_osrm_host(city: str, profile: str) -> str:
    """Return the city- and profile-specific local OSRM endpoint."""

    return f"http://localhost:{OSRM_PORTS[city][profile]}"

def check_osrm_server(city: str, host: str, profile: str) -> None:
    """
    Fail fast with a clear message if no OSRM server is reachable.
    """

    probe_coords = _format_coords(OSRM_PROBE_COORDS[city])
    probe_url = f"{host}/table/v1/{profile}/{probe_coords}"

    try:
        response = requests.get(probe_url, timeout=5)
        response.raise_for_status()
        payload = response.json()

    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Could not reach OSRM server at {host}. "
            "Make sure osrm-routed is running. "
            f"Original error: {exc}"
        ) from exc

    if payload.get("code") != "Ok":
        raise RuntimeError(
            "OSRM server responded, but the test route failed: "
            f"{payload.get('code')} - {payload.get('message', '')}"
        )

def _format_coords(coords):
    return ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)


def osrm_table(
    coords,
    sources=None,
    destinations=None,
    *,
    host: str,
    profile: str,
):
    """
    Query the OSRM /table service for a set of (lon, lat) coordinates
    and return the driving-distance matrix in kilometers.
    """

    url = f"{host}/table/v1/{profile}/{_format_coords(coords)}"

    params = {"annotations": "distance"}

    if sources is not None:
        params["sources"] = ";".join(str(i) for i in sources)

    if destinations is not None:
        params["destinations"] = ";".join(str(i) for i in destinations)

    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM /table error: {payload.get('code')} - {payload.get('message', '')}"
        )

    distances_m = np.array(payload["distances"], dtype=float)

    return distances_m / 1000.0

def _request_osrm_distance_duration_table(
    coords,
    *,
    host: str,
    profile: str,
    sources=None,
    destinations=None,
) -> tuple[np.ndarray, np.ndarray]:
    """Request one OSRM table, optionally as a rectangular submatrix."""

    url = f"{host}/table/v1/{profile}/{_format_coords(coords)}"
    params = {"annotations": "distance,duration"}

    if sources is not None:
        params["sources"] = ";".join(str(index) for index in sources)
    if destinations is not None:
        params["destinations"] = ";".join(
            str(index) for index in destinations
        )

    response = requests.get(url, params=params, timeout=180)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        details = response.text[:500].strip()
        raise requests.exceptions.HTTPError(
            f"{exc}. OSRM response: {details}",
            response=response,
        ) from exc

    payload = response.json()

    if payload.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM /table error: {payload.get('code')} - "
            f"{payload.get('message', '')}"
        )

    distance_matrix = np.array(payload["distances"], dtype=float) / 1000.0
    duration_matrix = np.array(payload["durations"], dtype=float) / 60.0

    return distance_matrix, duration_matrix

def _build_chunked_osrm_table(
    coords,
    *,
    host: str,
    profile: str,
    block_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Assemble a complete matrix from rectangular OSRM table blocks."""

    point_count = len(coords)
    distance_matrix = np.full((point_count, point_count), np.nan, dtype=float)
    duration_matrix = np.full((point_count, point_count), np.nan, dtype=float)

    blocks = [
        (start, min(start + block_size, point_count))
        for start in range(0, point_count, block_size)
    ]
    request_count = len(blocks) ** 2
    completed = 0

    print(
        f"OSRM table is too large for one request; using "
        f"{len(blocks)}x{len(blocks)} blocks ({request_count} requests, "
        f"block size {block_size})."
    )

    for source_start, source_end in blocks:
        source_coords = coords[source_start:source_end]
        source_count = source_end - source_start

        for destination_start, destination_end in blocks:
            destination_coords = coords[destination_start:destination_end]
            destination_count = destination_end - destination_start

            request_coords = source_coords + destination_coords
            sources = range(source_count)
            destinations = range(
                source_count,
                source_count + destination_count,
            )

            block_distances, block_durations = (
                _request_osrm_distance_duration_table(
                    request_coords,
                    host=host,
                    profile=profile,
                    sources=sources,
                    destinations=destinations,
                )
            )

            distance_matrix[
                source_start:source_end,
                destination_start:destination_end,
            ] = block_distances
            duration_matrix[
                source_start:source_end,
                destination_start:destination_end,
            ] = block_durations

            completed += 1
            if completed == request_count or completed % 10 == 0:
                print(
                    f"  OSRM matrix blocks: {completed}/{request_count}",
                    end="\r" if completed < request_count else "\n",
                )

    return distance_matrix, duration_matrix

def osrm_distance_duration_table(
    coords,
    *,
    host: str,
    profile: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return complete OSRM matrices, chunking large requests automatically."""

    try:
        return _request_osrm_distance_duration_table(
            coords,
            host=host,
            profile=profile,
        )
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code not in {400, 414}:
            raise

        block_size = min(OSRM_TABLE_BLOCK_SIZE, max(1, len(coords)))

        while block_size >= OSRM_TABLE_MIN_BLOCK_SIZE:
            try:
                return _build_chunked_osrm_table(
                    coords,
                    host=host,
                    profile=profile,
                    block_size=block_size,
                )
            except requests.exceptions.HTTPError as chunk_exc:
                chunk_status = getattr(chunk_exc.response, "status_code", None)
                if chunk_status not in {400, 414}:
                    raise

                next_block_size = block_size // 2
                if next_block_size < OSRM_TABLE_MIN_BLOCK_SIZE:
                    raise RuntimeError(
                        "OSRM rejected the table request even after chunking. "
                        "Increase the OSRM --max-table-size setting or reduce "
                        "OSRM_TABLE_MIN_BLOCK_SIZE. "
                        f"Last error: {chunk_exc}"
                    ) from chunk_exc

                print(
                    f"OSRM rejected block size {block_size}; retrying with "
                    f"{next_block_size}."
                )
                block_size = next_block_size

        raise RuntimeError(
            "Could not build the OSRM table with the configured block sizes."
        ) from exc



def osrm_route_geometry(
    coords,
    *,
    host: str,
    profile: str,
) -> list[list[float]]:
    """
    Query the OSRM /route service for an ordered list of (lon, lat) points
    and return the road-following geometry as a list of [lon, lat] pairs.

    The coordinates are visited in the given order, so the caller is
    responsible for arranging them as depot -> stops -> depot.
    """

    url = f"{host}/route/v1/{profile}/{_format_coords(coords)}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "continue_straight": "false",
    }

    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(
            f"OSRM /route error: {payload.get('code')} - "
            f"{payload.get('message', '')}"
        )

    return payload["routes"][0]["geometry"]["coordinates"]