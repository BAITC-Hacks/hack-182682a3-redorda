"""Deterministic audience indexing and filtering."""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

_SEGMENT_FILTER_COLUMNS = {
    "filter_current_tariff": "current_tariff",
    "filter_arpu_segment": "arpu_segment",
    "filter_data_segment": "data_segment",
    "filter_call_segment": "call_segment",
}
_SEGMENT_VALUES = {
    "arpu_segment": ("LOW", "MID", "HIGH"),
    "data_segment": ("NON_USER", "LITE", "HEAVY"),
    "call_segment": ("LOW", "MEDIUM", "HIGH"),
}


@dataclass
class Audience:
    filters: dict[str, str]
    positions: np.ndarray


class SegmentIndex:
    """Index positions follow ID order, including campaign prefixes.

    Missing current tariffs or ARPU segments exclude rows from base cells. Other
    missing segment values remain eligible when that particular filter is absent.
    """

    def __init__(self, profile: pd.DataFrame):
        if not isinstance(profile, pd.DataFrame):
            raise ValueError("customer_profile must be a pandas DataFrame")
        required = {"ID_NUMBER", "predicted_arpu", *_SEGMENT_FILTER_COLUMNS.values()}
        missing = sorted(required.difference(profile.columns))
        if missing:
            raise ValueError(f"customer_profile missing columns: {', '.join(missing)}")
        if profile.columns.duplicated().any():
            raise ValueError("customer_profile contains duplicate column names")
        identifiers = profile["ID_NUMBER"]
        if identifiers.isna().any() or identifiers.duplicated().any():
            raise ValueError("customer_profile requires nonmissing, unique ID_NUMBER values")
        if identifiers.map(lambda value: isinstance(value, str) and not value.strip()).any():
            raise ValueError("customer_profile contains an empty ID_NUMBER")
        try:
            arpu = pd.to_numeric(profile["predicted_arpu"], errors="raise").to_numpy(
                dtype=float, na_value=np.nan
            )
        except (TypeError, ValueError) as error:
            raise ValueError("predicted_arpu must contain finite nonnegative numbers") from error
        if not np.isfinite(arpu).all() or (arpu < 0).any():
            raise ValueError("predicted_arpu must contain finite nonnegative numbers")
        for column, allowed in _SEGMENT_VALUES.items():
            actual = profile[column].dropna()
            if not actual.isin(allowed).all():
                raise ValueError(f"customer_profile contains invalid {column} values")
        actual_tariffs = profile["current_tariff"].dropna()
        if not actual_tariffs.map(
            lambda value: (
                isinstance(value, str)
                and bool(value.strip())
                and value == value.strip()
                and value != "UNKNOWN"
                and ";" not in value
            )
        ).all():
            raise ValueError("customer_profile contains invalid current_tariff values")
        self.profile = profile.copy()
        self.profile["predicted_arpu"] = arpu
        try:
            self.profile = self.profile.sort_values("ID_NUMBER", kind="stable").reset_index(
                drop=True
            )
        except TypeError as error:
            raise ValueError("ID_NUMBER values must have a consistent sortable type") from error
        self.arpu = self.profile["predicted_arpu"].to_numpy(dtype=float, copy=True)
        self.arpu.setflags(write=False)
        eligible = self.profile[["current_tariff", "arpu_segment"]].notna().all(axis=1)
        self.excluded_count = int((~eligible).sum())
        self.cells: dict[tuple[str, str], np.ndarray] = {}
        for key, rows in self.profile.loc[eligible].groupby(
            ["current_tariff", "arpu_segment"], sort=True, observed=True
        ):
            positions = rows.index.to_numpy(dtype=np.int64, copy=True)
            positions.setflags(write=False)
            self.cells[key] = positions
        self._variant_cache: dict[tuple[str, str], list[Audience]] = {}

    def positions_for(self, filters: dict[str, Any]) -> np.ndarray:
        """Apply audience filters; other campaign fields have no effect."""
        unknown = {
            key
            for key in filters
            if key.startswith("filter_") and key not in _SEGMENT_FILTER_COLUMNS
        }
        if unknown:
            raise ValueError(f"Unsupported audience filters: {', '.join(sorted(unknown))}")
        mask = np.ones(len(self.profile), dtype=bool)
        for key, column in _SEGMENT_FILTER_COLUMNS.items():
            value = filters.get(key)
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            if key == "filter_current_tariff":
                if not isinstance(value, str):
                    raise ValueError("filter_current_tariff must be a string")
                wanted = [tariff.strip() for tariff in value.split(";") if tariff.strip()]
                if "UNKNOWN" in wanted:
                    raise ValueError("UNKNOWN is not an allowed campaign filter")
                selected = self.profile[column].isin(wanted)
            else:
                if not isinstance(value, str) or value not in _SEGMENT_VALUES[column]:
                    raise ValueError(f"Invalid {key}")
                selected = self.profile[column].eq(value).fillna(False)
            mask &= selected.to_numpy(dtype=bool)
        positions = np.flatnonzero(mask)
        positions.setflags(write=False)
        return positions

    def variants(self, arm: Any) -> list[Audience]:
        """Base cell, one-dimensional refinements, then their nonempty intersections."""
        key = (arm.current_tariff, arm.arpu_segment)
        if key in self._variant_cache:
            return self._variant_cache[key]
        base = self.cells.get(key)
        if base is None or not len(base):
            self._variant_cache[key] = []
            return self._variant_cache[key]
        filters = {"filter_current_tariff": key[0], "filter_arpu_segment": key[1]}
        audiences = [Audience(filters=filters, positions=base)]
        refinements = []
        for data in _SEGMENT_VALUES["data_segment"]:
            refinements.append({"filter_data_segment": data})
        for call in _SEGMENT_VALUES["call_segment"]:
            refinements.append({"filter_call_segment": call})
        for data in _SEGMENT_VALUES["data_segment"]:
            for call in _SEGMENT_VALUES["call_segment"]:
                refinements.append({"filter_data_segment": data, "filter_call_segment": call})
        for refinement in refinements:
            refined = {**filters, **refinement}
            positions = self.positions_for(refined)
            if len(positions):
                audiences.append(Audience(filters=refined, positions=positions))
        self._variant_cache[key] = audiences
        return audiences
