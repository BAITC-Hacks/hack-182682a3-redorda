"""Weak historical evidence ranks pilot hypotheses, never contact conversion rates."""

import hashlib
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from campaign_engine.segments import SegmentIndex

_HISTORY_COLUMNS = (
    "AVG_ARPU_PREV_3M",
    "AVG_ARPU_NEXT_3M",
    "tariff_plan_code_from",
    "tariff_plan_code_to",
)


@dataclass(frozen=True, order=True)
class Arm:
    current_tariff: str
    arpu_segment: str
    target_tariff: str


@dataclass
class Candidate:
    arm: Arm
    prior_mean: float = 0.0
    # Equivalent to approximately 6.12 SMS observations.
    prior_variance: float = 0.25
    priority: float = 0.0
    rationale: str = ""


def _candidate_repository_root() -> Path:
    """Source checkout root, independent of the process working directory."""
    source = Path(__file__).resolve()
    if source.parent.name == "campaign_engine" and source.parent.parent.name == "packages":
        return source.parents[2]
    return source.parent


def load_history(data_dir: str | Path | None = None) -> pd.DataFrame | None:
    """Load change_tariff.csv from the configured dataset directory.

    Explicit paths take precedence, then absolute PARTICIPANT_KIT_DIR, then the
    documented repository kit. A portable agent can also sit beside the kit's
    data directory. Invalid or unavailable history selects the history-free path.
    """
    root = _candidate_repository_root()
    if data_dir is not None:
        base = Path(data_dir).expanduser()
        if not base.is_absolute():
            base = root / base
        if base.name == "change_tariff.csv":
            paths = [base]
        elif base.suffix.lower() == ".csv":
            paths = []
        elif base.name == "data":
            paths = [base / "change_tariff.csv"]
        else:
            paths = [base / "data" / "change_tariff.csv"]
    else:
        configured = os.environ.get("PARTICIPANT_KIT_DIR")
        if configured:
            base = Path(configured).expanduser()
            # Relative environment configuration must not silently depend on cwd.
            paths = [base / "data" / "change_tariff.csv"] if base.is_absolute() else []
        else:
            paths = [root / "data" / "participant-kit" / "data" / "change_tariff.csv"]
            if Path(__file__).resolve().parent == root:
                paths.append(root / "data" / "change_tariff.csv")
    for path in paths:
        try:
            history = pd.read_csv(path, usecols=list(_HISTORY_COLUMNS))
        except (OSError, ValueError, pd.errors.ParserError, UnicodeError):
            continue
        history.attrs["public_source"] = str(path.resolve())
        return history
    return None


def _candidate_history_scores(
    history: pd.DataFrame | None, codes: set[str]
) -> dict[Arm, tuple[float, int]]:
    if (
        not isinstance(history, pd.DataFrame)
        or history.columns.duplicated().any()
        or not set(_HISTORY_COLUMNS).issubset(history.columns)
    ):
        return {}
    frame = history.loc[:, list(_HISTORY_COLUMNS)].copy()
    previous = pd.to_numeric(frame["AVG_ARPU_PREV_3M"], errors="coerce")
    following = pd.to_numeric(frame["AVG_ARPU_NEXT_3M"], errors="coerce")
    valid = (
        np.isfinite(previous)
        & np.isfinite(following)
        & previous.gt(0)
        & frame["tariff_plan_code_from"].isin(codes)
        & frame["tariff_plan_code_to"].isin(codes)
        & frame["tariff_plan_code_from"].ne(frame["tariff_plan_code_to"])
    )
    frame = frame.loc[valid].copy()
    if frame.empty:
        return {}
    previous = previous.loc[valid]
    following = following.loc[valid]
    # Derive the historical segment from the pre-transition ARPU.
    frame["arpu_segment"] = np.where(
        previous.lt(1000), "LOW", np.where(previous.le(5000), "MID", "HIGH")
    )
    # Median handles outliers; the explicit [-1, 3] bound limits the influence of
    # tiny positive denominators on *ranking only*. This is not an effect prior.
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        ratios = ((following - previous) / previous).clip(lower=-1.0, upper=3.0)
    frame["historical_ratio"] = ratios
    frame = frame.loc[np.isfinite(frame["historical_ratio"])]
    scores = {}
    for key, group in frame.groupby(
        ["tariff_plan_code_from", "arpu_segment", "tariff_plan_code_to"], sort=True
    ):
        count = len(group)
        # Count shrinks a conditional historical summary, never estimates the
        # chance of a contacted customer converting (no non-converter data exists).
        score = float(group["historical_ratio"].median()) * count / (count + 10)
        scores[Arm(*key)] = (score, count)
    return scores


def _candidate_exploration_key(arm: Arm) -> bytes:
    # Unlike Python hash(), this ordering is stable across processes and seeds.
    identity = "\0".join((arm.current_tariff, arm.arpu_segment, arm.target_tariff))
    return hashlib.sha256(identity.encode("utf-8")).digest()


def build_candidates(
    index: SegmentIndex,
    tariffs: pd.DataFrame,
    history: pd.DataFrame | None = None,
    limit: int = 24,
) -> list[Candidate]:
    """Return diverse base cells with a leader and an exploratory alternative.

    Historical uplift is conditional on an observed tariff change, from a different
    population. Consequently all effect priors remain zero with broad variance.
    ``priority`` is only an exploration ranking, not expected money or profit.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("candidate limit must be a nonnegative integer")
    if not isinstance(tariffs, pd.DataFrame) or "tariff_plan_code" not in tariffs:
        raise ValueError("tariffs must contain tariff_plan_code")
    values = tariffs["tariff_plan_code"]
    if (
        values.isna().any()
        or not values.map(
            lambda value: isinstance(value, str) and bool(value.strip()) and value == value.strip()
        ).all()
    ):
        raise ValueError("tariff_plan_code must contain nonempty strings")
    codes = sorted(set(values))
    if limit == 0 or len(codes) < 2:
        return []
    history_scores = _candidate_history_scores(history, set(codes))
    cell_options: list[tuple[float, tuple[str, str], list[Candidate]]] = []
    cell_masses: dict[tuple[str, str], float] = {}
    for cell, positions in index.cells.items():
        if cell[0] not in codes:
            continue
        arms = [Arm(*cell, target) for target in codes if target != cell[0]]
        observed = [arm for arm in arms if arm in history_scores]
        if observed:
            leader = min(observed, key=lambda arm: (-history_scores[arm][0], arm))
            score, count = history_scores[leader]
            explanation = (
                f"Historical conditional ARPU-change ranking from {count} valid transitions; "
                "contact effect and conversion remain unknown until pilots."
            )
        else:
            leader = min(arms, key=_candidate_exploration_key)
            score = 0.0
            explanation = "No valid historical transition evidence; deterministic exploration."
        # Campaign caps select an ID prefix.
        potential_arpu = float(index.arpu[positions[:5000]].sum())
        # Bound historical weight to preserve the influence of reachable ARPU.
        priority = potential_arpu * (1.0 + 0.25 * math.tanh(score))
        cell_masses[cell] = potential_arpu
        options = [Candidate(arm=leader, priority=priority, rationale=explanation)]
        alternatives = [arm for arm in arms if arm != leader]
        if alternatives:
            alternative = min(
                alternatives,
                key=lambda arm: (arm in history_scores, _candidate_exploration_key(arm)),
            )
            options.append(
                Candidate(
                    arm=alternative,
                    priority=priority * 0.9,
                    rationale="Exploratory alternative; prefers an unseen transition.",
                )
            )
        cell_options.append((priority, cell, options))
    cell_options.sort(key=lambda item: (-item[0], item[1]))
    # Balance the shortlist across ARPU strata before filling remaining slots.
    alternatives_per_cell = min(2, len(codes) - 1)
    cell_limit = math.ceil(limit / alternatives_per_cell)
    groups = {}
    for option in cell_options:
        groups.setdefault(option[1][1], []).append(option)
    levels = sorted(
        groups,
        key=lambda level: (-max(cell_masses[item[1]] for item in groups[level]), level),
    )
    selected = []
    round_number = 0
    while len(selected) < cell_limit:
        available = [
            groups[level][round_number] for level in levels if len(groups[level]) > round_number
        ]
        if not available:
            break
        selected.extend(available[: cell_limit - len(selected)])
        round_number += 1
    # Screening consumes a prefix of this list. Interleave strata there too,
    # rather than undoing the quota by globally sorting the final hypotheses.
    candidates = [
        options[round_number]
        for round_number in range(alternatives_per_cell)
        for _, _, options in selected
        if len(options) > round_number
    ]
    return candidates[:limit]
