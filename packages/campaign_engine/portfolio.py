"""Portfolio forecasts with resource limits and probabilistic pilot overlap."""

from dataclasses import dataclass, field

import numpy as np

from campaign_engine.beliefs import belief_scenarios, effect_ratio
from campaign_engine.candidates import Arm
from campaign_engine.contracts import Campaign


@dataclass
class Portfolio:
    campaigns: list[dict] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)
    cost: float = 0.0
    contacts: int = 0
    mean_net: float = 0.0
    lower_tail_net: float = 0.0
    objective: float = 0.0


def portfolio_objective(samples: np.ndarray, risk_weight: float) -> float:
    if not np.isfinite(samples).all() or len(samples) < 2:
        raise ValueError("Portfolio forecast contains nonfinite values")
    mean = float(samples.mean())
    # Fractional order statistic keeps precisely 10% probability mass even at
    # small scenario counts. These are model scenarios, not a confidence interval.
    values = np.sort(samples)
    mass = len(values) * 0.1
    whole = int(mass)
    tail = (values[:whole].sum() + (mass - whole) * values[whole]) / mass
    objective = float(mean - risk_weight * (mean - tail))
    if not np.isfinite(objective):
        raise ValueError("Portfolio objective overflowed")
    return objective


def expected_best_ratio(effects: list[np.ndarray], probabilities: list[float], count: int):
    """Expected maximum conditional on contact; retain negative contacted effects.

    Each pilot's membership is independent of other pilots. Within-pilot
    dependence affects risk of total revenue, not this per-customer expectation.
    """
    if not effects:
        return np.zeros(count)
    ratios = np.stack(effects)
    probs = np.broadcast_to(np.array(probabilities)[:, None], ratios.shape)
    order = np.argsort(-ratios, axis=0, kind="stable")
    sorted_ratios = np.take_along_axis(ratios, order, axis=0)
    sorted_probs = np.take_along_axis(probs, order, axis=0)
    survival = np.concatenate([
        np.ones((1, count)), np.cumprod(1 - sorted_probs, axis=0)[:-1]
    ])
    return (sorted_ratios * sorted_probs * survival).sum(axis=0)


class PortfolioBuilder:
    def __init__(self, index, channels: dict, options, seed: int):
        self.index, self.channels, self.options, self.seed = index, channels, options, seed
        self._prefixes = {}

    def _prefix_arpu(self, audience):
        key = tuple(sorted(audience.filters.items()))
        if key not in self._prefixes:
            self._prefixes[key] = np.concatenate([
                [0.0], np.cumsum(self.index.arpu[audience.positions])
            ])
        return self._prefixes[key]

    def build(self, candidates, beliefs, pilots, *, budget, contacts,
              official_budget, official_contacts, force_nonempty=True) -> Portfolio:
        samples = belief_scenarios(beliefs, self.options.scenario_count, self.seed)
        count = self.options.scenario_count
        pilot_effects, pilot_probs = {}, {}
        pilot_cost = 0.0
        for pilot in pilots:
            req, obs = pilot.request, pilot.observation
            arm = Arm(req["filter_current_tariff"], req["filter_arpu_segment"],
                      req["target_tariff"])
            cell = (arm.current_tariff, arm.arpu_segment)
            # Runner only conducts full-cell pilots. Avoid silently using n/N
            # for a narrower pilot supplied by another caller.
            if req.get("filter_data_segment") or req.get("filter_call_segment"):
                raise ValueError("Portfolio requires homogeneous full-cell pilots")
            size = len(self.index.cells[cell])
            pilot_effects.setdefault(cell, []).append(
                effect_ratio(samples[arm], req["channel"], self.channels))
            pilot_probs.setdefault(cell, []).append(obs["n_customers"] / size)
            pilot_cost += obs["cost"]

        base_by_cell = {}
        baseline = np.full(count, -pilot_cost)
        for cell, effects in pilot_effects.items():
            base = expected_best_ratio(effects, pilot_probs[cell], count)
            base_by_cell[cell] = base
            baseline += self.index.arpu[self.index.cells[cell]].sum() * base

        variants = []
        for candidate in candidates:
            arm = candidate.arm
            cell = (arm.current_tariff, arm.arpu_segment)
            for channel in self.channels:
                ratio = effect_ratio(samples[arm], channel, self.channels)
                combined = expected_best_ratio(
                    pilot_effects.get(cell, []) + [ratio],
                    pilot_probs.get(cell, []) + [1.0], count)
                incremental = combined - base_by_cell.get(cell, np.zeros(count))
                for audience in self.index.variants(arm):
                    variants.append((arm, channel, audience, incremental,
                                     self._prefix_arpu(audience)))

        current = baseline.copy()
        chosen, details, used_cells = [], [], set()
        spent, reached = 0.0, 0
        for _ in range(10):
            best = None
            current_score = portfolio_objective(current, self.options.risk_weight)
            for arm, channel, audience, incremental, prefix in variants:
                cell = (arm.current_tariff, arm.arpu_segment)
                if cell in used_cells:
                    continue
                unit_cost = float(self.channels[channel]["cost_per_contact"])
                # CSV cannot express a custom audience cap; use the environment's prefix.
                n = min(len(audience.positions), 5000, max(0, official_contacts - reached))
                if unit_cost:
                    n = min(n, int(max(0, official_budget - spent) // unit_cost))
                cost = n * unit_cost
                if n <= 0 or n > contacts - reached or cost > budget - spent + 1e-8:
                    continue
                contribution = incremental * prefix[n] - cost
                score = portfolio_objective(current + contribution, self.options.risk_weight)
                # Stable iteration provides deterministic tie-breaking, but
                # explicitly favour fewer contacts and cheaper campaigns at ties.
                key = (score, -n, -cost)
                if best is None or key > best[0]:
                    best = (key, arm, channel, audience, n, cost, contribution)
            if best is None:
                break
            key, arm, channel, audience, n, cost, contribution = best
            if key[0] <= current_score and (chosen or not force_nonempty):
                break
            campaign = Campaign(
                campaign_name=f"evolve_{len(chosen) + 1}_{arm.current_tariff}_{arm.target_tariff}",
                target_tariff=arm.target_tariff, channel=channel, **audience.filters,
            ).to_submission()
            chosen.append(campaign)
            details.append({"campaign_name": campaign["campaign_name"], "n_contacts": n,
                            "cost": cost, "estimated_incremental_net":
                            float(contribution.mean())})
            current += contribution
            spent += cost
            reached += n
            used_cells.add((arm.current_tariff, arm.arpu_segment))
        mean = float(current.mean())
        tail = portfolio_objective(current, 1.0)
        return Portfolio(chosen, details, spent, reached, mean, tail,
                         portfolio_objective(current, self.options.risk_weight))


def validate_portfolio(campaigns, index, tariffs, channels, *, budget, contacts,
                       official_budget, official_contacts):
    """Independently replay executable filters/caps before emitting a final plan."""
    if not 1 <= len(campaigns) <= 10:
        raise ValueError("A completed run must have 1–10 final campaigns")
    money, reach = float(official_budget), int(official_contacts)
    spent, used = 0.0, 0
    for campaign in campaigns:
        parsed = Campaign.model_validate(campaign)
        if parsed.target_tariff not in tariffs or parsed.channel not in channels:
            raise ValueError("Unknown campaign tariff/channel")
        positions = index.positions_for(parsed.to_submission())
        unit_cost = float(channels[parsed.channel]["cost_per_contact"])
        n = min(len(positions), 5000, reach)
        if unit_cost:
            n = min(n, int(money // unit_cost))
        if n <= 0:
            raise ValueError("Final campaign has no executable audience")
        cost = n * unit_cost
        spent, used = spent + cost, used + n
        money, reach = money - cost, reach - n
        if spent > budget + 1e-8 or used > contacts:
            raise ValueError("Final plan exceeds configured resources")
    return {"final_cost": spent, "final_contacts": used}
