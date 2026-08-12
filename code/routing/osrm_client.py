import hashlib
from pathlib import Path
from time import perf_counter

import requests
from code.common.constants import (
    OSRM_PORTS,
    OSRM_PROBE_COORDS,
    OSRM_TABLE_BLOCK_SIZE,
    OSRM_TABLE_MIN_BLOCK_SIZE,
)
import numpy as np


_OSRM_CACHE_ENABLED = False
_OSRM_CACHE_DIRECTORY = Path(".glims_cache") / "osrm"
_OSRM_CACHE_FORMAT_VERSION = "v1"

_OSRM_CACHE_STATS = {
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_load_seconds": 0.0,
    "cache_write_seconds": 0.0,
    "http_requests": 0,
    "http_seconds": 0.0,
}


def configure_osrm_matrix_cache(
    *,
    enabled: bool,
    directory: str | Path = ".glims_cache/osrm",
) -> None:
    """Configure the local content-addressed OSRM matrix cache."""
    global _OSRM_CACHE_ENABLED, _OSRM_CACHE_DIRECTORY
    _OSRM_CACHE_ENABLED = bool(enabled)
    _OSRM_CACHE_DIRECTORY = Path(directory)
    if _OSRM_CACHE_ENABLED:
        _OSRM_CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)


def reset_osrm_cache_stats() -> None:
    for key in _OSRM_CACHE_STATS:
        _OSRM_CACHE_STATS[key] = (
            0 if key in {"cache_hits", "cache_misses", "http_requests"} else 0.0
        )


def get_osrm_cache_stats() -> dict[str, int | float]:
    return dict(_OSRM_CACHE_STATS)


def _normalized_coord_bytes(coords) -> bytes:
    return ";".join(
        f"{float(lon):.6f},{float(lat):.6f}"
        for lon, lat in coords
    ).encode("utf-8")


def _matrix_cache_path(
    *,
    matrix_kind: str,
    host: str,
    profile: str,
    source_coords,
    destination_coords=None,
) -> Path:
    digest = hashlib.sha256()
    for part in (
        _OSRM_CACHE_FORMAT_VERSION,
        matrix_kind,
        host,
        profile,
    ):
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    digest.update(_normalized_coord_bytes(source_coords))
    if destination_coords is not None:
        digest.update(b"\0DEST\0")
        digest.update(_normalized_coord_bytes(destination_coords))
    return _OSRM_CACHE_DIRECTORY / f"{matrix_kind}_{digest.hexdigest()}.npz"


def _load_cached_matrices(path: Path):
    if not _OSRM_CACHE_ENABLED:
        return None
    if not path.exists():
        _OSRM_CACHE_STATS["cache_misses"] += 1
        return None

    started = perf_counter()
    try:
        with np.load(path, allow_pickle=False) as payload:
            distance_matrix = np.asarray(payload["distance"], dtype=float)
            duration_matrix = np.asarray(payload["duration"], dtype=float)
    except Exception as exc:
        print(
            f"OSRM cache entry unreadable; rebuilding {path.name}: "
            f"{type(exc).__name__}: {exc}"
        )
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        _OSRM_CACHE_STATS["cache_misses"] += 1
        return None

    elapsed = perf_counter() - started
    _OSRM_CACHE_STATS["cache_hits"] += 1
    _OSRM_CACHE_STATS["cache_load_seconds"] += elapsed
    print(
        f"OSRM matrix cache HIT: {path.name} "
        f"{distance_matrix.shape} in {elapsed:.2f} s"
    )
    return distance_matrix, duration_matrix


def _save_cached_matrices(
    path: Path,
    distance_matrix: np.ndarray,
    duration_matrix: np.ndarray,
) -> None:
    if not _OSRM_CACHE_ENABLED:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.npz")
    started = perf_counter()

    # Uncompressed and float64 on purpose: faster I/O and no routing precision
    # changes versus the matrices returned directly by OSRM.
    np.savez(
        temporary,
        distance=np.asarray(distance_matrix, dtype=np.float64),
        duration=np.asarray(duration_matrix, dtype=np.float64),
    )
    temporary.replace(path)

    elapsed = perf_counter() - started
    _OSRM_CACHE_STATS["cache_write_seconds"] += elapsed
    print(
        f"OSRM matrix cache WRITE: {path.name} "
        f"{distance_matrix.shape} in {elapsed:.2f} s"
    )


def _record_http_request(started: float) -> None:
    _OSRM_CACHE_STATS["http_requests"] += 1
    _OSRM_CACHE_STATS["http_seconds"] += perf_counter() - started


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

    _http_started = perf_counter()
    response = requests.get(url, params=params, timeout=60)
    _record_http_request(_http_started)
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

    _http_started = perf_counter()
    response = requests.get(url, params=params, timeout=180)
    _record_http_request(_http_started)

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

def osrm_distance_duration_table_rectangular(
    source_coords,
    destination_coords,
    *,
    host: str,
    profile: str,
    block_size: int = OSRM_TABLE_BLOCK_SIZE,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Return an OSRM source -> destination distance/duration matrix.

    Unlike osrm_distance_duration_table(), this function does not build
    a complete square matrix. It calculates only the requested
    source-to-destination relationships.

    This is intended for operations such as customer -> facility
    assignment, where customer-to-customer and facility-to-facility
    distances are unnecessary.
    """

    source_coords = list(source_coords)
    destination_coords = list(destination_coords)

    cache_path = _matrix_cache_path(
        matrix_kind="rect",
        host=host,
        profile=profile,
        source_coords=source_coords,
        destination_coords=destination_coords,
    )
    cached = _load_cached_matrices(cache_path)
    if cached is not None:
        return cached

    source_count = len(source_coords)
    destination_count = len(destination_coords)

    if source_count == 0:
        return (
            np.empty((0, destination_count), dtype=float),
            np.empty((0, destination_count), dtype=float),
        )

    if destination_count == 0:
        raise ValueError(
            "At least one destination coordinate is required."
        )

    distance_matrix = np.full(
        (source_count, destination_count),
        np.nan,
        dtype=float,
    )
    duration_matrix = np.full(
        (source_count, destination_count),
        np.nan,
        dtype=float,
    )

    source_blocks = [
        (start, min(start + block_size, source_count))
        for start in range(0, source_count, block_size)
    ]

    destination_blocks = [
        (start, min(start + block_size, destination_count))
        for start in range(0, destination_count, block_size)
    ]

    request_count = len(source_blocks) * len(destination_blocks)
    completed = 0

    print(
        f"Building rectangular OSRM table: "
        f"{source_count} sources x {destination_count} destinations; "
        f"{len(source_blocks)}x{len(destination_blocks)} blocks "
        f"({request_count} requests, block size {block_size})."
    )

    for source_start, source_end in source_blocks:
        source_block = source_coords[source_start:source_end]
        current_source_count = len(source_block)

        for destination_start, destination_end in destination_blocks:
            destination_block = destination_coords[
                destination_start:destination_end
            ]
            current_destination_count = len(destination_block)

            request_coords = source_block + destination_block

            sources = range(current_source_count)

            destinations = range(
                current_source_count,
                current_source_count + current_destination_count,
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
                    f"  OSRM rectangular blocks: "
                    f"{completed}/{request_count}",
                    end="\r" if completed < request_count else "\n",
                )

    _save_cached_matrices(cache_path, distance_matrix, duration_matrix)
    return distance_matrix, duration_matrix

def osrm_distance_duration_table(
    coords,
    *,
    host: str,
    profile: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return complete OSRM matrices, chunking and caching automatically."""
    coords = list(coords)
    cache_path = _matrix_cache_path(
        matrix_kind="square",
        host=host,
        profile=profile,
        source_coords=coords,
    )
    cached = _load_cached_matrices(cache_path)
    if cached is not None:
        return cached

    try:
        result = _request_osrm_distance_duration_table(
            coords,
            host=host,
            profile=profile,
        )
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        if status_code not in {400, 414}:
            raise

        block_size = min(OSRM_TABLE_BLOCK_SIZE, max(1, len(coords)))
        result = None

        while block_size >= OSRM_TABLE_MIN_BLOCK_SIZE:
            try:
                result = _build_chunked_osrm_table(
                    coords,
                    host=host,
                    profile=profile,
                    block_size=block_size,
                )
                break
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

        if result is None:
            raise RuntimeError(
                "Could not build the OSRM table with the configured block sizes."
            ) from exc

    distance_matrix, duration_matrix = result
    _save_cached_matrices(cache_path, distance_matrix, duration_matrix)
    return distance_matrix, duration_matrix



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

    _http_started = perf_counter()
    response = requests.get(url, params=params, timeout=60)
    _record_http_request(_http_started)
    response.raise_for_status()
    payload = response.json()

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(
            f"OSRM /route error: {payload.get('code')} - "
            f"{payload.get('message', '')}"
        )

    return payload["routes"][0]["geometry"]["coordinates"]