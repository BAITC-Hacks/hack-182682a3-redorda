"""Shared campaign runner with events, cancellation, and resource accounting."""

import copy
import math
import time
from dataclasses import asdict

from campaign_engine.beliefs import initialize_beliefs
from campaign_engine.candidates import Arm, Candidate, build_candidates
from campaign_engine.contracts import CHANNEL_COSTS, HypothesisBatch, RunConfig
from campaign_engine.engine_types import EngineOptions, EngineResult, ObserverFailure, PilotRecord
from campaign_engine.openai_gateway import (
    PlanningUnavailable,
    propose_hypotheses,
    summary_fingerprint,
)
from campaign_engine.pilots import choose_pilot
from campaign_engine.portfolio import PortfolioBuilder, validate_portfolio
from campaign_engine.segments import SegmentIndex
from campaign_engine.team_events import TeamJournal


def public_planning_summary(index, tariffs, candidates) -> dict:
    """Build aggregate input for hypothesis generation."""
    return {
        "tariffs": sorted(str(t) for t in tariffs["tariff_plan_code"]),
        "cells": [{"current_tariff": cell[0], "arpu_segment": cell[1],
                   "customers": len(positions),
                   "predicted_arpu_sum": float(index.arpu[positions].sum())}
                  for cell, positions in sorted(index.cells.items())],
        "historical_candidates": [{"current_tariff": c.arm.current_tariff,
                                   "arpu_segment": c.arm.arpu_segment,
                                   "target_tariff": c.arm.target_tariff,
                                   "rationale": c.rationale} for c in candidates],
        "allowed_channels": list(CHANNEL_COSTS),
    }


def _runner_channels(raw: dict) -> dict:
    channels = {}
    for name, expected_cost in CHANNEL_COSTS.items():
        if name not in raw:
            continue
        cost = float(raw[name]["cost_per_contact"])
        multiplier = float(raw[name]["conversion_multiplier"])
        if cost != expected_cost or not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("Channel catalogue does not match public case mechanics")
        channels[name] = {"cost_per_contact": cost, "conversion_multiplier": multiplier}
    if "sms" not in channels or "push" not in channels:
        raise ValueError("The SMS exploration and push fallback channels are required")
    return channels


def _runner_resources(env) -> tuple[float, int, int]:
    money, contacts, pilots = float(env.remaining_budget), env.remaining_contacts, env.pilots_left
    if (not math.isfinite(money) or money < 0 or int(contacts) != contacts or contacts < 0
            or int(pilots) != pilots or not 0 <= pilots <= 20):
        raise ValueError("Invalid public resource counters")
    return money, int(contacts), int(pilots)


def run_campaigns(env, config=None, *, history=None, observer=None, should_cancel=None,
                  hypothesis_provider=None, options=None) -> EngineResult:
    """Execute a fresh run; return forecasts, actual observations, and safe events.

    hypothesis_provider(summary) may supply a validated replay in any mode.
    Live calls happen only for config.strategy == 'openai'. Agent() deliberately
    uses baseline irrespective of credentials. Cancellation is cooperative.
    """
    result = EngineResult()
    initial_resources = None
    started = time.monotonic()
    config = RunConfig.model_validate(config or {})
    options = options or EngineOptions()
    deadline = started + options.runtime_seconds
    result.metadata = {"engine_version": "0.2.0", "seed": config.seed,
                       "strategy": config.strategy, "options": asdict(options),
                       "hypothesis_source": "deterministic"}

    def emit(kind, **payload):
        event = {"sequence": len(result.events) + 1, "type": kind, "data": payload}
        result.events.append(event)
        if observer is not None:
            try:
                observer(copy.deepcopy(event))
            except Exception as exc:
                raise ObserverFailure("Event persistence failed; do not retry actions") from exc

    def cancelled():
        return should_cancel is not None and bool(should_cancel())

    team = TeamJournal(emit)
    try:
        emit("run_started", config=config.model_dump(), policy=options.policy)
        if cancelled():
            result.status, result.stop_reason = "cancelled", "cancel_requested"
            emit("run_cancelled", reason=result.stop_reason)
            return result
        team.begin('prepare', 'lead', 'Проверка ресурсов и ограничений запуска')
        if env.pilot_history:
            raise ValueError("A fresh environment is required; automatic resume is unsupported")
        initial_money, initial_contacts, initial_pilots = _runner_resources(env)
        initial_resources = (initial_money, initial_contacts, initial_pilots)
        if initial_money > 100_000 or initial_contacts > 15_000:
            raise ValueError("Environment exceeds official case limits")
        limit_money = min(config.budget, initial_money)
        limit_contacts = min(config.max_contacts, initial_contacts)
        limit_pilots = min(config.max_pilots, initial_pilots)
        if limit_contacts < 2 or limit_pilots < 1:
            raise ValueError("Resources cannot support a pilot and a final campaign")

        team.complete('validation', {'config': config.model_dump(), 'budget': limit_money,
                                    'contacts': limit_contacts, 'pilots': limit_pilots})
        team.begin('hypotheses', 'analyst', 'Анализ аудитории и подготовка гипотез')
        index = SegmentIndex(env.customer_profile)
        tariff_codes = set(str(t) for t in env.tariffs["tariff_plan_code"])
        if any(cell[0] not in tariff_codes for cell in index.cells):
            raise ValueError("Profile has an unknown current tariff")
        channels = _runner_channels(env.channels)
        candidates = build_candidates(index, env.tariffs, history, options.candidate_limit)
        if not candidates:
            raise ValueError("No eligible campaign hypotheses")
        if history is None:
            result.warnings.append("history_unavailable: using broad priors and public catalogues")
        result.metadata["excluded_base_cell_customers"] = index.excluded_count
        emit("candidates_ready", count=len(candidates), excluded_customers=index.excluded_count)

        if config.strategy == "openai" or hypothesis_provider is not None:
            if cancelled():
                result.status, result.stop_reason = "cancelled", "cancel_requested"
                emit("run_cancelled", reason=result.stop_reason)
                return result
            summary = public_planning_summary(index, env.tariffs, candidates)
            result.metadata["summary_sha256"] = summary_fingerprint(summary)
            try:
                remaining_seconds = deadline - time.monotonic() - 5
                if remaining_seconds <= 0:
                    raise PlanningUnavailable("No time remains for hypothesis generation")
                batch = (hypothesis_provider(summary) if hypothesis_provider is not None
                         else propose_hypotheses(summary, timeout_seconds=remaining_seconds))
                if cancelled():
                    result.status, result.stop_reason = "cancelled", "cancel_requested"
                    emit("run_cancelled", reason=result.stop_reason)
                    return result
                batch = HypothesisBatch.model_validate(batch)
                proposals, seen = [], set()
                for hypothesis in batch.hypotheses:
                    campaign = hypothesis.campaign
                    arm = Arm(campaign.filter_current_tariff or "",
                              campaign.filter_arpu_segment or "", campaign.target_tariff)
                    if ((arm.current_tariff, arm.arpu_segment) not in index.cells
                            or arm.target_tariff not in tariff_codes
                            or arm.target_tariff == arm.current_tariff
                            or len(index.positions_for(campaign.to_submission())) == 0):
                        continue
                    if arm not in seen:
                        proposals.append(Candidate(arm, rationale=hypothesis.rationale))
                        seen.add(arm)
                if not proposals:
                    raise PlanningUnavailable("Model proposals contain no eligible cells")
                # Keep a numerical exploration pool; the model cannot eliminate it.
                candidates = (proposals[:6] + [c for c in candidates if c.arm not in seen])[
                    :options.candidate_limit]
                result.metadata["hypothesis_source"] = (
                    "injected" if hypothesis_provider is not None else "openai")
                result.metadata["hypotheses"] = batch.model_dump()
                emit("hypotheses_ready", accepted=min(len(proposals), 6), count=len(candidates))
            except ObserverFailure:
                raise
            except Exception as exc:
                # Provider failure preserves the deterministic candidate pool.
                result.warnings.append(f"hypothesis_fallback:{type(exc).__name__}")
                emit("fallback_used", reason="hypothesis_provider_unavailable")

        beliefs = initialize_beliefs(candidates)
        team.complete('hypotheses', {'candidates': [asdict(c) for c in candidates],
                                    'source': result.metadata['hypothesis_source'],
                                    'excluded_customers': index.excluded_count})
        builder = PortfolioBuilder(index, channels, options, config.seed)
        # At full environment reach, a final campaign can be capped to the last
        # contact. A stricter web limit cannot invent that CSV cap.
        reserve_contacts = 1 if limit_contacts == initial_contacts else min(
            min(len(a.positions), 5000, initial_contacts)
            for c in candidates for a in index.variants(c.arm))
        if reserve_contacts >= limit_contacts:
            raise ValueError("Configured reach cannot express a final campaign after a pilot")
        blocked_arms = set()
        pilot_cost, pilot_contacts = 0.0, 0
        current = None

        def build_current(*, improve=False):
            money, contacts, _ = _runner_resources(env)
            return builder.build(
                candidates, beliefs, result.pilots, budget=limit_money - pilot_cost,
                contacts=limit_contacts - pilot_contacts, official_budget=money,
                official_contacts=contacts, improve=improve, deadline=deadline - 1)

        for attempt in range(40):  # deterministic bound, including failed attempts
            money, contacts, pilots_left = _runner_resources(env)
            if cancelled():
                result.status, result.stop_reason = "cancelled", "cancel_requested"
                emit("run_cancelled", pilots=len(result.pilots), reason=result.stop_reason)
                break
            if time.monotonic() >= deadline - 1:
                result.stop_reason = "time_limit"
                break
            if len(result.pilots) >= limit_pilots or pilots_left <= 0:
                result.stop_reason = "pilot_limit"
                break
            choice = choose_pilot(
                candidates, beliefs, index, channels, options,
                successful_pilots=len(result.pilots), pilot_contacts=pilot_contacts,
                remaining_budget=min(limit_money - pilot_cost, money),
                remaining_contacts=min(limit_contacts - pilot_contacts, contacts),
                official_budget=money, official_contacts=contacts,
                reserve_contacts=reserve_contacts,
                pilots_left=min(limit_pilots - len(result.pilots), pilots_left),
                blocked_arms=blocked_arms, builder=builder, pilots=result.pilots, deadline=deadline,
            )
            if choice is None:
                result.stop_reason = "exploration_not_worthwhile_or_infeasible"
                break
            arm = choice.arm
            request = {"target_tariff": arm.target_tariff, "channel": "sms",
                       "n_customers": choice.n_requested,
                       "filter_current_tariff": arm.current_tariff,
                       "filter_arpu_segment": arm.arpu_segment}
            before = _runner_resources(env)
            history_length_before = len(env.pilot_history)
            prior = beliefs[arm].summary()
            emit("pilot_started", request=request, reason=choice.reason,
                 selection_value=choice.score, prior=prior)
            # Observer/cancel may take time: check again before the paid call.
            if cancelled():
                result.status, result.stop_reason = "cancelled", "cancel_requested"
                emit("run_cancelled", pilots=len(result.pilots), reason=result.stop_reason)
                break
            if time.monotonic() >= deadline - 1:
                result.stop_reason = "time_limit"
                break
            team.begin(f'pilot:{attempt + 1}', 'experiment',
                       f'Пилот {len(result.pilots) + 1}: {arm.target_tariff}')
            try:
                observation = env.run_pilot(**request)
            except Exception:
                after = _runner_resources(env)
                history_changed = len(env.pilot_history) != history_length_before
                emit("pilot_failed", request=request, resources_changed=after != before,
                     history_changed=history_changed,
                     resources_before=before, resources_after=after)
                team.fail('Пилот не завершён; подтверждённого результата нет.')
                if after != before or history_changed:
                    result.metadata["unreconciled_pilot_request"] = request.copy()
                    raise ValueError(
                        "Pilot failed after public state mutation; manual reconciliation")
                blocked_arms.add(arm)
                result.warnings.append("pilot_failed_without_resource_change")
                continue

            after = _runner_resources(env)
            result.metadata["unreconciled_pilot_request"] = request.copy()
            n = observation.get("n_customers")
            cost = float(observation.get("cost", float("nan")))
            ratio = float(observation.get("observed_lift_ratio", float("nan")))
            if (not isinstance(n, int) or isinstance(n, bool) or n != choice.n_actual
                    or not math.isfinite(cost) or not math.isfinite(ratio)
                    or not math.isclose(cost, n * channels["sms"]["cost_per_contact"])
                    or not math.isclose(after[0], before[0] - cost, abs_tol=1e-7)
                    or after[1] != before[1] - n or after[2] != before[2] - 1):
                raise ValueError("Pilot response is inconsistent; no automatic retry")
            posterior = beliefs[arm].updated(ratio, n, channels["sms"]["conversion_multiplier"])
            beliefs[arm] = posterior
            # Persist only the observation fields consumed by the engine.
            clean_observation = {"n_customers": n, "cost": cost,
                                 "observed_lift_ratio": ratio,
                                 "remaining_budget": after[0], "remaining_contacts": after[1]}
            result.pilots.append(PilotRecord(request.copy(), clean_observation,
                                            prior, posterior.summary()))
            result.metadata.pop("unreconciled_pilot_request", None)
            pilot_cost += cost
            pilot_contacts += n
            result.resource_usage = {"pilot_cost": pilot_cost, "pilot_contacts": pilot_contacts,
                                     "n_pilots": len(result.pilots)}
            emit("pilot_completed", request=request, observation=clean_observation,
                 posterior=posterior.summary())
            team.complete('pilot_observation', {'sequence': len(result.pilots),
                          'request': request, 'observation': clean_observation,
                          'posterior': posterior.summary()})
            team.begin(f'portfolio:{attempt + 1}', 'finance',
                       'Оценка портфеля с учётом расходов пилотов')
            current = build_current()
            emit("portfolio_updated", campaigns=current.campaigns,
                 forecast_net=current.mean_net, forecast_source="public_pilot_posterior",
                 final_cost=current.cost, final_contacts=current.contacts)
            team.complete('portfolio', {'campaigns': current.campaigns,
                          'forecast_net': current.mean_net, 'final_cost': current.cost,
                          'final_contacts': current.contacts, 'pilot_cost': pilot_cost,
                          'pilot_contacts': pilot_contacts, 'simulator_result': None})

        if result.status == "cancelled":
            return result
        if not result.pilots:
            raise ValueError("No successful pilot; mandatory condition not satisfied")
        team.begin('final_portfolio', 'finance', 'Уточнение итогового портфеля')
        current = build_current(improve=options.portfolio_search)
        result.metadata["portfolio_search"] = current.search_metadata
        if cancelled():
            result.status, result.stop_reason = "cancelled", "cancel_requested"
            emit("run_cancelled", pilots=len(result.pilots), reason=result.stop_reason)
            return result
        team.complete('portfolio', {'campaigns': current.campaigns,
                      'forecast_net': current.mean_net, 'final_cost': current.cost,
                      'final_contacts': current.contacts, 'pilot_cost': pilot_cost,
                      'pilot_contacts': pilot_contacts, 'simulator_result': None})
        team.begin('validate', 'control', 'Проверка итоговых кампаний и лимитов')
        money, contacts, _ = _runner_resources(env)
        final_usage = validate_portfolio(
            current.campaigns, index, tariff_codes, channels, budget=limit_money - pilot_cost,
            contacts=limit_contacts - pilot_contacts, official_budget=money,
            official_contacts=contacts)
        result.campaigns = current.campaigns
        result.resource_usage.update(final_usage)
        result.resource_usage.update({"total_cost": pilot_cost + current.cost,
                                      "total_contacts": pilot_contacts + current.contacts})
        result.estimates = {
            "source": "posterior_forecast_not_official_score",
            "net_arpu_gain_mean": current.mean_net,
            "lower_tail_mean_10": current.lower_tail_net,
            "objective": current.objective, "campaigns": current.details,
            "limitations": ["Pilot IDs unknown: membership is averaged analytically",
                            "Tail reflects effect scenarios, not full membership risk",
                            "Unpiloted call uses a conservative saturation bound"],
        }
        result.stop_reason = result.stop_reason or "iteration_limit"
        result.status = "completed"
        team.complete('validation', {'resource_usage': result.resource_usage,
                                    'campaign_count': len(result.campaigns),
                                    'limits_satisfied': True})
        emit("run_completed", resource_usage=result.resource_usage, reason=result.stop_reason)
    except ObserverFailure:
        result.status, result.stop_reason = "failed", "observer_failed_do_not_retry"
        result.warnings.append("Event persistence failed; retain pilot ledger for reconciliation")
    except Exception as exc:
        result.status = "failed"
        result.stop_reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        try:
            team.fail('Шаг расчёта завершился с ошибкой; результат не подтверждён.')
            emit("run_failed", reason=result.stop_reason)
        except ObserverFailure:
            result.warnings.append("Event persistence also failed")
    finally:
        if initial_resources is not None:
            try:
                final_money, final_contacts, final_pilots = _runner_resources(env)
                # Environment deltas remain evidence of spending even if a paid
                # call throws or its observation cannot be used for inference.
                result.resource_usage.update({
                    "pilot_cost": initial_resources[0] - final_money,
                    "pilot_contacts": initial_resources[1] - final_contacts,
                    "n_pilots": initial_resources[2] - final_pilots,
                    "confirmed_pilots": len(result.pilots),
                })
            except (TypeError, ValueError):
                result.warnings.append("Public resource counters require reconciliation")
        result.metadata["elapsed_seconds"] = time.monotonic() - started
    return result
