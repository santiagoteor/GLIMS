"""Filesystem locations used by the GLIMS simulation core."""

import os
from pathlib import Path


def get_project_root() -> Path:
    """Resolve the directory containing data, configs and results."""
    configured_root = os.environ.get("GLIMS_PROJECT_ROOT")

    if configured_root:
        root = Path(configured_root).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(
                f"GLIMS_PROJECT_ROOT does not exist: {root}"
            )
        return root

    # Backward-compatible default for running inside the GLIMS repository.
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = get_project_root()

CODE_DIR = PROJECT_ROOT / "code"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = PROJECT_ROOT / "raw_data"
RESULTS_DIR = PROJECT_ROOT / "results"
DOCS_DIR = PROJECT_ROOT / "docs"
