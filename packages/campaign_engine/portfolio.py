"""Portfolio forecasts with resource limits and probabilistic pilot overlap."""

import time
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
    search_metadata: dict = field(default_factory=dict)


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
              official_budget, official_contacts, force_nonempty=True,
              allowed_channels=None, improve=False, deadline=None) -> Portfolio:
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
                if (allowed_channels is not None and cell in allowed_channels
                        and channel not in allowed_channels[cell]):
                    continue
                ratio = effect_ratio(samples[arm], channel, self.channels)
                combined = expected_best_ratio(
                    pilot_effects.get(cell, []) + [ratio],
                    pilot_probs.get(cell, []) + [1.0], count)
                incremental = combined - base_by_cell.get(cell, np.zeros(count))
                for audience in self.index.variants(arm):
                    variants.append((arm, channel, audience, incremental,
                                     self._prefix_arpu(audience)))

        current = baseline.copy()
        chosen, details, used_cells, selected = [], [], set(), []
        spent, reached = 0.0, 0
        for _ in range(10):
            best = None
            current_score = portfolio_objective(current, self.options.risk_weight)
            for variant_id, (arm, channel, audience, incremental, prefix) in enumerate(variants):
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
                    best = (key, arm, channel, audience, n, cost, contribution, variant_id)
            if best is None:
                break
            key, arm, channel, audience, n, cost, contribution, variant_id = best
            if key[0] <= current_score and (chosen or not force_nonempty):
                break
            campaign = Campaign(
                campaign_name=f"evolve_{len(chosen) + 1}_{arm.current_tariff}_{arm.target_tariff}",
                target_tariff=arm.target_tariff, channel=channel, **audience.filters,
            ).to_submission()
            chosen.append(campaign)
            selected.append(variant_id)
            details.append({"campaign_name": campaign["campaign_name"], "n_contacts": n,
                            "cost": cost, "estimated_incremental_net":
                            float(contribution.mean())})
            current += contribution
            spent += cost
            reached += n
            used_cells.add((arm.current_tariff, arm.arpu_segment))
        mean = float(current.mean())
        tail = portfolio_objective(current, 1.0)
        original = Portfolio(chosen, details, spent, reached, mean, tail,
                             portfolio_objective(current, self.options.risk_weight))
        if not improve or not chosen:
            return original
        search = _PortfolioSearch(
            variants, baseline, self.index, self.channels, self.options,
            budget=budget, contacts=contacts, official_budget=official_budget,
            official_contacts=official_contacts, force_nonempty=force_nonempty,
        )
        return search.improve(original, tuple(selected), deadline)


@dataclass
class _PortfolioSearchState:
    values: np.ndarray
    selected: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    cells: set = field(default_factory=set)
    spent: float = 0.0
    reached: int = 0


class _PortfolioSearch:
    """Bounded edits of an ordered plan with a freshly optimized suffix.

    Effects and pilot membership expectations are frozen by the caller. A forced
    edit may lose locally; only the completed portfolio is compared to the incumbent.
    """

    def __init__(self, variants, baseline, index, channels, options, *, budget, contacts,
                 official_budget, official_contacts, force_nonempty):
        self.variants, self.baseline = variants, baseline
        self.index, self.channels, self.options = index, channels, options
        self.resources = dict(budget=budget, contacts=contacts, official_budget=official_budget,
                              official_contacts=official_contacts)
        self.force_nonempty = force_nonempty
        self.units = np.array([channels[v[1]]["cost_per_contact"] for v in variants], dtype=float)
        self.caps = np.array([min(len(v[2].positions), 5000) for v in variants])
        self.effects = np.stack([v[3] for v in variants])
        self.cells = [(v[0].current_tariff, v[0].arpu_segment) for v in variants]
        self.tariffs = {v[0].target_tariff for v in variants}
        self.channel_variants = {}
        for i, variant in enumerate(variants):
            arm, _channel, audience, _effect, _prefix = variant
            key = (arm, tuple(sorted(audience.filters.items())))
            self.channel_variants.setdefault(key, []).append(i)

    def _expired(self):
        return time.monotonic() >= self.deadline

    def _available(self, state):
        n = np.minimum(self.caps, max(0, self.resources["official_contacts"] - state.reached))
        affordable = np.floor(max(0, self.resources["official_budget"] - state.spent)
                              / np.maximum(self.units, 1)).astype(int)
        n = np.where(self.units > 0, np.minimum(n, affordable), n)
        costs = n * self.units
        eligible = (n > 0) & (n <= self.resources["contacts"] - state.reached)
        eligible &= costs <= self.resources["budget"] - state.spent + 1e-8
        eligible &= np.array([cell not in state.cells for cell in self.cells])
        return n, costs, np.flatnonzero(eligible)

    def _contributions(self, ids, n, costs):
        arpu = np.array([self.variants[i][4][n[i]] for i in ids])
        return self.effects[ids] * arpu[:, None] - costs[ids, None]

    def _scores(self, values):
        mean = values.mean(axis=1)
        ordered = np.sort(values, axis=1)
        mass = values.shape[1] * 0.1
        whole = int(mass)
        tail = (ordered[:, :whole].sum(axis=1) + (mass - whole) * ordered[:, whole]) / mass
        scores = mean - self.options.risk_weight * (mean - tail)
        if not np.isfinite(scores).all():
            raise ValueError("Portfolio forecast contains nonfinite values")
        return scores

    def _append(self, state, variant_id, n, cost):
        contribution = self.variants[variant_id][3] * self.variants[variant_id][4][n] - cost
        state.values += contribution
        state.selected.append(variant_id)
        state.rows.append((n, float(cost), float(contribution.mean())))
        state.spent += float(cost)
        state.reached += int(n)
        state.cells.add(self.cells[variant_id])

    def _complete(self, seed_order):
        state = _PortfolioSearchState(self.baseline.copy())
        for variant_id in seed_order:
            if self._expired():
                return None
            n, costs, eligible = self._available(state)
            if variant_id not in eligible:
                return None
            self._append(state, variant_id, n[variant_id], costs[variant_id])
        while len(state.selected) < 10:
            if self._expired():
                return None
            n, costs, ids = self._available(state)
            if not len(ids):
                break
            scores = self._scores(state.values + self._contributions(ids, n, costs))
            # Same score/contact/cost tie-breaking as the original greedy builder.
            winner = np.lexsort((ids, costs[ids], n[ids], -scores))[0]
            current_score = portfolio_objective(state.values, self.options.risk_weight)
            if scores[winner] <= current_score and (state.selected or not self.force_nonempty):
                break
            i = ids[winner]
            self._append(state, i, n[i], costs[i])
        if not state.selected:
            return None
        return state

    def _portfolio(self, state):
        campaigns, details = [], []
        for rank, (i, (n, cost, effect)) in enumerate(zip(state.selected, state.rows), 1):
            arm, channel, audience, _incremental, _prefix = self.variants[i]
            name = f"evolve_{rank}_{arm.current_tariff}_{arm.target_tariff}"
            campaign = Campaign(campaign_name=name,
                                target_tariff=arm.target_tariff, channel=channel,
                                **audience.filters).to_submission()
            campaigns.append(campaign)
            details.append({"campaign_name": campaign["campaign_name"], "n_contacts": int(n),
                            "cost": cost, "estimated_incremental_net": effect})
        return Portfolio(campaigns, details, state.spent, state.reached, float(state.values.mean()),
                         portfolio_objective(state.values, 1.0),
                         portfolio_objective(state.values, self.options.risk_weight))

    def _validate(self, portfolio):
        actual = validate_portfolio(portfolio.campaigns, self.index, self.tariffs,
                                    self.channels, **self.resources)
        if actual != {"final_cost": portfolio.cost, "final_contacts": portfolio.contacts}:
            raise ValueError("Search forecast and executable resources disagree")

    def _channel_moves(self, order):
        for slot, i in enumerate(order):
            arm, _channel, audience, _incremental, _prefix = self.variants[i]
            key = (arm, tuple(sorted(audience.filters.items())))
            for replacement in self.channel_variants[key]:
                if replacement != i:
                    yield order[:slot] + (replacement,)

    def _order_moves(self, order):
        for distance in range(1, len(order)):
            for left in range(len(order) - distance):
                right = left + distance
                changed = list(order)
                changed[left], changed[right] = changed[right], changed[left]
                # Refill after the changed prefix, then also check the complete permutation.
                yield tuple(changed[:right + 1])
                yield tuple(changed)
                moved = list(order)
                moved.insert(left, moved.pop(right))
                yield tuple(moved[:left + 1])
                yield tuple(moved)
                moved = list(order)
                moved.insert(right, moved.pop(left))
                yield tuple(moved[:right + 1])
                yield tuple(moved)

    def _replacement_moves(self, order):
        # Full cells first, followed by refinements; round-robin over slots avoids
        # spending the entire limit on alternatives for the first campaign.
        replacement_ids = sorted(range(len(self.variants)), key=lambda i: (
            len(self.variants[i][2].filters), i))
        for alternative in replacement_ids:
            for slot, selected in enumerate(order):
                if alternative != selected and self.cells[alternative] not in {
                    self.cells[i] for i in order[:slot]
                }:
                    yield order[:slot] + (alternative,)

    def _neighbors(self, order):
        streams = [iter(self._channel_moves(order)), iter(self._order_moves(order)),
                   iter(self._replacement_moves(order))]
        while streams:
            for stream in streams[:]:
                try:
                    yield next(stream)
                except StopIteration:
                    streams.remove(stream)

    def improve(self, original, order, deadline):
        started = time.monotonic()
        self.deadline = min(started + self.options.portfolio_search_seconds,
                            float("inf") if deadline is None else deadline)
        self._validate(original)
        best = original
        evaluated = accepted = 0
        seen = {order}
        termination = "neighborhood_exhausted"
        while True:
            improved = False
            for seed in self._neighbors(order):
                if self._expired():
                    termination = "time_limit"
                    break
                if evaluated >= self.options.portfolio_search_max_evaluations:
                    termination = "evaluation_limit"
                    break
                if seed in seen:
                    continue
                seen.add(seed)
                evaluated += 1
                trial = self._complete(seed)
                if trial is None:
                    continue
                candidate = self._portfolio(trial)
                if candidate.objective > best.objective:
                    self._validate(candidate)
                    best, order = candidate, tuple(trial.selected)
                    accepted += 1
                    improved = True
                    break
            if termination != "neighborhood_exhausted" or not improved:
                break
        if termination == "neighborhood_exhausted" and self._expired():
            termination = "time_limit"
        best.search_metadata = {"evaluations": evaluated, "accepted": accepted,
                                "termination": termination,
                                "baseline_objective": original.objective,
                                "best_objective": best.objective,
                                "elapsed_seconds": time.monotonic() - started}
        return best


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
