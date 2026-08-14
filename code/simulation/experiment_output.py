from __future__ import annotations

from datetime import datetime
import json
import platform
from pathlib import Path
import re
import subprocess
from typing import Any

from code.simulation.experiment_config import ExperimentConfig


def create_experiment_directory(
    *,
    results_root: Path,
    config: ExperimentConfig,
    city: str,
    started_at: datetime,
) -> tuple[str, Path]:
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    zone_label = (
        "all_zones"
        if not config.zones
        else "_".join(slugify(zone) for zone in config.zones)
    )
    experiment_id = "_".join(
        [
            timestamp,
            zone_label,
            slugify(config.demand_scenario),
            str(config.instance_size),
            slugify(config.routing.algorithm),
        ]
    )
    folder = results_root / "experiments" / slugify(city) / experiment_id
    (folder / "routes").mkdir(parents=True, exist_ok=False)
    (folder / "routing").mkdir(parents=True, exist_ok=False)
    (folder / "facilities").mkdir(parents=True, exist_ok=False)
    return experiment_id, folder


def save_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")


def build_metadata(
    *,
    experiment_id: str,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "experiment_id": experiment_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": (
            finished_at.isoformat(timespec="seconds")
            if finished_at is not None
            else None
        ),
        "runtime_seconds": (
            (finished_at - started_at).total_seconds()
            if finished_at is not None
            else None
        ),
        "status": status,
        "git_commit": _git_value(["rev-parse", "HEAD"]),
        "git_branch": _git_value(
            ["rev-parse", "--abbrev-ref", "HEAD"]
        ),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
    if error is not None:
        metadata["error"] = error
    return metadata


def slugify(value: str) -> str:
    normalized = value.strip().lower().replace("'", "")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_") or "unnamed"


def _git_value(arguments: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None
