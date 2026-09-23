"""SMS exploration policies with uncertainty estimates and resource limits."""

import math
import time
from dataclasses import dataclass

from campaign_engine.beliefs import PILOT_NOISE_SD, Belief
from campaign_engine.candidates import Arm, Candidate
from campaign_engine.engine_types import EngineOptions, PilotRecord
from campaign_engine.portfolio import PortfolioBuilder
from campaign_engine.segments import SegmentIndex


@dataclass(frozen=True)
class PilotChoice:
    arm: Arm
    n_requested: int
    n_actual: int
    score: float
    reason: str


def _pilot_request(
    arm: Arm,
    desired: int,
    index: SegmentIndex,
    sms_cost: float,
    remaining_budget: float,
    remaining_contacts: int,
    official_budget: float,
    official_contacts: int,
    reserve_contacts: int,
    cap_remaining: int,
) -> PilotChoice | None:
    size = len(index.cells.get((arm.current_tariff, arm.arpu_segment), ()))
    available = min(
        cap_remaining,
        remaining_contacts - reserve_contacts,
        official_contacts - reserve_contacts,
    )
    if sms_cost:
        available = min(available, int(min(remaining_budget, official_budget) // sms_cost))
    if size <= 0 or available <= 0:
        return None
    requested = max(10, min(200, desired, available))
    actual = min(size, requested, official_contacts)
    if sms_cost:
        actual = min(actual, int(official_budget // sms_cost))
    # The environment does not know the smaller web limit or our final reserve.
    # In particular, requesting 10 with only 1 locally available is unsafe unless
    # the real environment/audience will cap the executed pilot to that 1 person.
    if actual <= 0 or actual > available:
        return None
    return PilotChoice(arm, requested, actual, 0.0, "")


def _pilot_information_score(
    choice: PilotChoice,
    candidates: list[Candidate],
    beliefs: dict[Arm, Belief],
    index: SegmentIndex,
    sms_cost: float,
    multiplier: float,
    remaining_contacts: int,
) -> float:
    belief = beliefs[choice.arm]
    cell = (choice.arm.current_tariff, choice.arm.arpu_segment)
    competitors = [
        beliefs[candidate.arm].mean
        for candidate in candidates
        if candidate.arm != choice.arm
        and (candidate.arm.current_tariff, candidate.arm.arpu_segment) == cell
    ]
    threshold = max([0.0, *competitors])
    posterior_variance = 1.0 / (
        1.0 / belief.variance + choice.n_actual * multiplier**2 / PILOT_NOISE_SD**2
    )
    # Before observing y, the posterior mean varies by this amount. The normal
    # expected improvement over the competing decision measures the possibility
    # of changing a choice, rather than rewarding a large noisy mean alone.
    learnable_sd = math.sqrt(max(0.0, belief.variance - posterior_variance))
    gap = abs(belief.mean - threshold)
    if learnable_sd <= 0:
        return -choice.n_actual * sms_cost
    z = gap / learnable_sd
    improvement = learnable_sd * math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    improvement -= gap * 0.5 * math.erfc(z / math.sqrt(2.0))
    positions = index.cells[cell]
    reachable = min(5000, len(positions), max(0, remaining_contacts - choice.n_actual))
    reachable_arpu = float(index.arpu[positions[:reachable]].sum())
    average_arpu = float(index.arpu[positions].mean())
    pilot_downside = min(0.0, belief.mean * multiplier * average_arpu * choice.n_actual)
    return reachable_arpu * multiplier * improvement - choice.n_actual * sms_cost + pilot_downside


def _pilot_lookahead(
    actions: list[PilotChoice],
    candidates: list[Candidate],
    beliefs: dict[Arm, Belief],
    index: SegmentIndex,
    multiplier: float,
    sms_cost: float,
    builder: PortfolioBuilder,
    pilots: list[PilotRecord],
    remaining_budget: float,
    remaining_contacts: int,
    official_budget: float,
    official_contacts: int,
    deadline: float,
) -> PilotChoice | None:
    if time.monotonic() >= deadline:
        return None
    baseline = builder.build(
        candidates,
        beliefs,
        pilots,
        budget=remaining_budget,
        contacts=remaining_contacts,
        official_budget=official_budget,
        official_contacts=official_contacts,
    )
    winner = None
    # Three-point Gaussian quadrature: bounded, deterministic predictive outcomes.
    nodes = ((-math.sqrt(3.0), 1.0 / 6.0), (0.0, 2.0 / 3.0), (math.sqrt(3.0), 1.0 / 6.0))
    for action in actions[:3]:
        cost = action.n_actual * sms_cost
        belief = beliefs[action.arm]
        predictive_sd = math.sqrt(
            multiplier**2 * belief.variance + PILOT_NOISE_SD**2 / action.n_actual
        )
        expected_objective = 0.0
        feasible = True
        for node, weight in nodes:
            if time.monotonic() >= deadline:
                return None
            ratio = multiplier * belief.mean + predictive_sd * node
            posterior = belief.updated(ratio, action.n_actual, multiplier)
            trial_beliefs = {**beliefs, action.arm: posterior}
            cell_positions = index.cells[action.arm.current_tariff, action.arm.arpu_segment]
            simulated = PilotRecord(
                request={
                    "target_tariff": action.arm.target_tariff,
                    "channel": "sms",
                    "n_customers": action.n_requested,
                    "filter_current_tariff": action.arm.current_tariff,
                    "filter_arpu_segment": action.arm.arpu_segment,
                },
                observation={
                    "n_customers": action.n_actual,
                    "cost": cost,
                    "observed_lift_ratio": ratio,
                    "observed_lift_total": (
                        ratio * action.n_actual * float(index.arpu[cell_positions].mean())
                    ),
                    "remaining_budget": official_budget - cost,
                    "remaining_contacts": official_contacts - action.n_actual,
                },
                prior=belief.summary(),
                posterior=posterior.summary(),
            )
            trial = builder.build(
                candidates,
                trial_beliefs,
                [*pilots, simulated],
                budget=remaining_budget - cost,
                contacts=remaining_contacts - action.n_actual,
                official_budget=official_budget - cost,
                official_contacts=official_contacts - action.n_actual,
            )
            if not trial.campaigns or not math.isfinite(trial.objective):
                feasible = False
                break
            expected_objective += weight * trial.objective
        if not feasible:
            continue
        # Both objectives already contain all pilot costs and expected effects.
        # Subtracting cost again here would double-charge the simulated action.
        improvement = expected_objective - baseline.objective
        if improvement > 1e-8 and (winner is None or improvement > winner.score):
            winner = PilotChoice(
                action.arm,
                action.n_requested,
                action.n_actual,
                improvement,
                "Bounded three-node portfolio lookahead improves the modeled objective.",
            )
    return winner if time.monotonic() < deadline else None


def choose_pilot(
    candidates: list[Candidate],
    beliefs: dict[Arm, Belief],
    index: SegmentIndex,
    channels: dict,
    options: EngineOptions,
    *,
    successful_pilots: int,
    pilot_contacts: int,
    remaining_budget: float,
    remaining_contacts: int,
    official_budget: float,
    official_contacts: int,
    reserve_contacts: int,
    pilots_left: int,
    blocked_arms: set[Arm],
    builder: PortfolioBuilder,
    pilots: list[PilotRecord],
    deadline: float,
) -> PilotChoice | None:
    """Choose a new full-cell SMS pilot, a repeat, or a stop, without mutating state."""
    if (
        time.monotonic() >= deadline
        or pilots_left <= 0
        or successful_pilots >= 20
        or pilot_contacts >= options.pilot_contact_cap
        or remaining_budget < 0
        or official_budget < 0
    ):
        return None
    sms = channels.get("sms")
    if not sms:
        return None
    sms_cost, multiplier = float(sms["cost_per_contact"]), float(sms["conversion_multiplier"])
    if (
        not math.isfinite(sms_cost)
        or sms_cost < 0
        or not math.isfinite(multiplier)
        or multiplier <= 0
    ):
        raise ValueError("Invalid public SMS channel")

    def feasible(arm: Arm, requested: int) -> PilotChoice | None:
        if arm in blocked_arms or arm not in beliefs:
            return None
        return _pilot_request(
            arm,
            requested,
            index,
            sms_cost,
            remaining_budget,
            remaining_contacts,
            official_budget,
            official_contacts,
            max(0, reserve_contacts),
            options.pilot_contact_cap - pilot_contacts,
        )

    if options.policy in {"fixed_100", "fixed_200", "wide_100"}:
        width = 20 if options.policy == "wide_100" else 10
        rounds = 2 if options.policy == "fixed_100" else 1
        requested = 200 if options.policy == "fixed_200" else 100
        seen = {}
        for candidate in candidates[:width] * rounds:
            arm = candidate.arm
            seen[arm] = seen.get(arm, 0) + 1
            if arm not in beliefs or beliefs[arm].observations >= seen[arm]:
                continue
            action = feasible(arm, requested)
            if action:
                return PilotChoice(
                    arm, action.n_requested, action.n_actual, 0.0, "Predetermined fixed schedule."
                )
        return None

    for candidate in candidates[: options.screening_pilots]:
        arm = candidate.arm
        if arm not in beliefs or beliefs[arm].observations:
            continue
        action = feasible(arm, 100)
        if action:
            return PilotChoice(
                arm, action.n_requested, action.n_actual, 0.0, "Initial broad screening."
            )

    actions = []
    for candidate in candidates:
        best_for_arm = None
        for requested in (50, 100, 200):
            action = feasible(candidate.arm, requested)
            if action is None:
                continue
            score = _pilot_information_score(
                action, candidates, beliefs, index, sms_cost, multiplier, remaining_contacts
            )
            if best_for_arm is None or score > best_for_arm.score:
                best_for_arm = PilotChoice(
                    action.arm,
                    action.n_requested,
                    action.n_actual,
                    score,
                    "Expected value of resolving decision uncertainty exceeds pilot cost.",
                )
        if best_for_arm is not None:
            actions.append(best_for_arm)
    actions.sort(key=lambda action: (-action.score, action.arm, action.n_requested))
    if not actions or time.monotonic() >= deadline:
        return None
    if successful_pilots == 0:
        # Collect the first observation even when its expected benefit is uncertain.
        first = actions[0]
        return PilotChoice(
            first.arm,
            first.n_requested,
            first.n_actual,
            first.score,
            "Required first feasible pilot; modeled benefit is uncertain.",
        )
    if options.policy == "evolve":
        return _pilot_lookahead(
            actions,
            candidates,
            beliefs,
            index,
            multiplier,
            sms_cost,
            builder,
            pilots,
            remaining_budget,
            remaining_contacts,
            official_budget,
            official_contacts,
            deadline,
        )
    return actions[0] if actions[0].score > 1e-8 else None
