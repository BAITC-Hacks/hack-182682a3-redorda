"""Gaussian updates for pilot observations within homogeneous audience cells."""

import math
from dataclasses import dataclass
from numbers import Integral

import numpy as np

from campaign_engine.candidates import Arm, Candidate

PILOT_NOISE_SD = 0.804


@dataclass
class Belief:
    mean: float = 0.0
    variance: float = 0.25
    observations: int = 0
    customers: int = 0

    def updated(self, ratio: float, n_actual: int, multiplier: float) -> "Belief":
        if (not math.isfinite(ratio) or not math.isfinite(multiplier)
                or multiplier <= 0 or isinstance(n_actual, bool)
                or not isinstance(n_actual, Integral) or n_actual <= 0):
            raise ValueError("Invalid pilot observation")
        if (not math.isfinite(self.mean) or not math.isfinite(self.variance)
                or self.variance <= 0):
            raise ValueError("Invalid prior variance")
        precision = n_actual / PILOT_NOISE_SD**2
        variance = 1 / (1 / self.variance + precision * multiplier**2)
        mean = variance * (self.mean / self.variance + precision * multiplier * ratio)
        if not math.isfinite(mean) or not math.isfinite(variance) or variance <= 0:
            raise ValueError("Pilot observation produced a nonfinite posterior")
        return Belief(mean, variance, self.observations + 1, self.customers + n_actual)

    def summary(self) -> dict:
        return {"mean": self.mean, "sd": math.sqrt(self.variance),
                "observations": self.observations, "customers": self.customers}


def initialize_beliefs(candidates: list[Candidate]) -> dict[Arm, Belief]:
    return {c.arm: Belief(c.prior_mean, c.prior_variance) for c in candidates}


def effect_ratio(theta, channel: str, channels: dict):
    """Conservative call bound; exact scaling for non-saturating channels.

    Assumes latent conversion is a probability in [0, 1].
    """
    multiplier = float(channels[channel]["conversion_multiplier"])
    theta = np.asarray(theta)
    if multiplier > 1:
        return np.where(theta >= 0, theta, multiplier * theta)
    return multiplier * theta


def belief_scenarios(beliefs: dict[Arm, Belief], count: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    result = {}
    # Stable arm order and antithetic draws reduce scenario comparison noise.
    for arm in sorted(beliefs):
        b = beliefs[arm]
        half = rng.standard_normal((count + 1) // 2)
        z = np.concatenate([half, -half])[:count]
        z = (z - z.mean()) / max(float(z.std()), 1e-12)
        result[arm] = b.mean + math.sqrt(b.variance) * z
    return result
