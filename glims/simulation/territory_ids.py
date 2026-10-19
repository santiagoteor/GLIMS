from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import unicodedata

import pandas as pd

from glims.common.paths import PROJECT_ROOT


DEFAULT_ZONE_IDS_PATH = (
    PROJECT_ROOT
    / "code"
    / "notebooks"
    / "presentation"
    / "tables"
    / "neighborhood_zone_ids.csv"
)


@dataclass(frozen=True)
class TerritoryId:
    sample_id: str
    zone_id: str
    city: str
    zone_name: str
    archetype: str | None = None


def _normalize_text(value: str) -> str:
    """Normalize human labels only for robust lookup; never use as an ID."""
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return " ".join(text.split())


def _first_column(columns: list[str], candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    raise KeyError(
        "Missing required territorial-ID column. Expected one of: "
        + ", ".join(candidates)
    )


@lru_cache(maxsize=4)
def load_territory_ids(path: str | None = None) -> tuple[TerritoryId, ...]:
    """
    Load the frozen mapping created by code.ml.freeze_neighborhood_sample.

    Returning an empty tuple when the file does not exist is intentional: it
    keeps historical/legacy experiments runnable without the new frozen sample.
    """
    csv_path = Path(path) if path else DEFAULT_ZONE_IDS_PATH
    if not csv_path.exists():
        return ()

    frame = pd.read_csv(csv_path)
    columns = list(frame.columns)
    sample_col = _first_column(columns, ("sample_id",))
    zone_id_col = _first_column(columns, ("zone_id",))
    city_col = _first_column(columns, ("_city", "city"))
    zone_col = _first_column(columns, ("zone_original", "zone", "neighborhood", "zona"))
    arch_col = next(
        (c for c in ("archetype_final", "archetype") if c in columns),
        None,
    )

    rows: list[TerritoryId] = []
    seen_zone_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()

    for _, row in frame.iterrows():
        item = TerritoryId(
            sample_id=str(row[sample_col]).strip(),
            zone_id=str(row[zone_id_col]).strip(),
            city=str(row[city_col]).strip().lower(),
            zone_name=str(row[zone_col]).strip(),
            archetype=(
                str(row[arch_col]).strip().upper()
                if arch_col is not None and pd.notna(row[arch_col])
                else None
            ),
        )
        key = (item.sample_id, item.city, _normalize_text(item.zone_name))
        if item.zone_id in seen_zone_ids:
            raise ValueError(f"Duplicate zone_id in {csv_path}: {item.zone_id}")
        if key in seen_keys:
            raise ValueError(
                f"Duplicate city+zone mapping in {csv_path}: "
                f"{item.city} / {item.zone_name}"
            )
        seen_zone_ids.add(item.zone_id)
        seen_keys.add(key)
        rows.append(item)

    return tuple(rows)


def resolve_territory_id(
    city: str,
    zone_name: str,
    *,
    path: Path | None = None,
    sample_id: str | None = None,
) -> TerritoryId | None:
    mapping = load_territory_ids(str(path) if path is not None else None)
    city_key = str(city).strip().lower()
    zone_key = _normalize_text(zone_name)
    matches = [item for item in mapping if item.city == city_key
               and _normalize_text(item.zone_name) == zone_key
               and (sample_id is None or item.sample_id == sample_id)]
    if len(matches) == 1:
        return matches[0]
    # A neighborhood may belong to both NH35 and NH82. Without an explicit
    # sample context, returning one of them would silently mislabel results.
    return None



def resolve_sample_id_for_zones(
    city: str,
    zones: list[str] | None,
    *,
    path: Path | None = None,
) -> str | None:
    """
    Return a sample_id only when every configured zone belongs to one sample.

    This deliberately avoids silently mixing frozen and legacy zones inside a
    sample-specific directory. Unmapped, empty or mixed selections fall back to
    the historical directory convention.
    """
    if not zones:
        return None

    mapping = load_territory_ids(str(path) if path is not None else None)
    requested = [_normalize_text(z) for z in zones]
    if len(requested) != len(set(requested)):
        raise ValueError("Duplicate neighborhood names in configured zones")
    candidates = set()
    for item in mapping:
        if item.city == str(city).strip().lower():
            candidates.add(item.sample_id)
    matches = []
    for sample in sorted(candidates):
        available = {_normalize_text(x.zone_name) for x in mapping
                     if x.city == str(city).strip().lower() and x.sample_id == sample}
        if set(requested).issubset(available):
            matches.append(sample)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        exact = [sample for sample in matches if
                 set(requested) == {_normalize_text(x.zone_name) for x in mapping
                                    if x.city == str(city).strip().lower() and x.sample_id == sample}]
        if len(exact) == 1:
            return exact[0]
        raise ValueError(f"Ambiguous territorial sample for {city}: {matches}. "
                         "Specify a full sample city selection or disambiguate the mapping.")
    return None
