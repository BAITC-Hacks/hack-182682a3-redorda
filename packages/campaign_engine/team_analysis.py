"""Deterministic, read-only analysis of persisted public engine facts.

A snapshot lacks customer rows and posterior scenarios. Comparison can therefore
retain only an unchanged prefix of saved cohorts; it cannot reoptimize audiences
or reconstruct tail risk. Marginal means are additive only across disjoint cells.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from campaign_engine.contracts import CHANNEL_COSTS

SOURCE = "posterior_forecast_not_official_score"
LIMITATIONS = [
    "Сценарий сохраняет только начальные кампании и их сохранённое число контактов; "
    "новые аудитории, каналы и частичные кампании не оптимизируются.",
    "Это прогноз на прежних наблюдениях, а не новый измеренный результат симулятора.",
    "Риск изменённого портфеля неизвестен без сохранённых сценариев распределения.",
    "CSV нового плана требует отдельного расчёта; рост бюджета здесь не расширяет аудитории.",
]


def _number(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Missing numeric saved forecast") from exc
    if not number.is_finite():
        raise ValueError("Nonfinite saved forecast")
    return number


def _resources(snapshot):
    pilots = snapshot.get("pilots", [])
    cost = sum((_number(row["cost"]) for row in pilots), Decimal(0))
    contacts = sum(row["n_customers"] for row in pilots)
    if cost < 0 or contacts < 0:
        raise ValueError("Invalid pilot resources")
    return cost, contacts


def _artifact(kind, title, snapshot, data):
    return {"type": kind, "title": title, "data": data,
            "evidence_ids": list(snapshot.get("evidence_ids", []))}


def explain(snapshot, campaign_id):
    """Describe a saved selected campaign without claiming causal certainty."""
    matches = [item for item in snapshot.get("campaigns", [])
               if str(item["id"]) == str(campaign_id)
               or item.get("campaign", {}).get("campaign_name") == str(campaign_id)]
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous saved campaign")
    row = matches[0]
    estimates = snapshot.get("estimates") or {}
    return _artifact("explanation", f"Объяснение кампании {campaign_id}", snapshot, {
        "campaign_id": row["id"], "campaign": deepcopy(row.get("campaign", {})),
        "metrics": deepcopy(row.get("metrics", {})), "source": estimates.get("source"),
        "summary": "Кампания выбрана в сохранённый портфель. Прогноз отражает модель "
                   "на исторических данных и наблюдениях пилотов; прирост — вклад "
                   "поверх уже учтённого пилотного эффекта за вычетом стоимости кампании.",
        "simulator_result": None,
        "limitations": list(estimates.get("limitations", [])) + [
            "Snapshot не содержит оценки всех отклонённых альтернатив; "
            "объяснение не доказывает глобальную оптимальность выбора."],
    })


def compare(snapshot, constraints):
    """Forecast a feasible unchanged prefix, retaining all sunk pilot resources."""
    if set(constraints) - {"budget", "allowed_channels"}:
        raise ValueError("Unknown constraints")
    config = snapshot["config"]
    budget = _number(constraints.get("budget", config["budget"]))
    if not 0 < budget <= 100000:
        raise ValueError("Invalid budget")
    channels = constraints.get("allowed_channels")
    if channels is not None and (not isinstance(channels, list) or not channels
                                  or set(channels) - CHANNEL_COSTS.keys()):
        raise ValueError("Invalid channels")
    pilot_cost, pilot_contacts = _resources(snapshot)
    if budget < pilot_cost or pilot_contacts > config["max_contacts"]:
        raise ValueError("Constraints below spent pilot resources")
    estimates = snapshot.get("estimates") or {}
    if estimates.get("source") != SOURCE:
        raise ValueError("Saved posterior forecast unavailable")
    rows = snapshot.get("campaigns", [])
    cells, marginal = set(), []
    for row in rows:
        campaign, metrics = row["campaign"], row["metrics"]
        cell = (campaign.get("filter_current_tariff"), campaign.get("filter_arpu_segment"))
        if not all(cell) or cell in cells:
            raise ValueError("Cannot recombine potentially overlapping campaigns")
        cells.add(cell)
        if (_number(metrics.get("cost")) < 0 or type(metrics.get("n_contacts")) is not int
                or metrics["n_contacts"] <= 0):
            raise ValueError("Invalid saved campaign resources")
        marginal.append(_number(metrics.get("estimated_incremental_net")))
    mean = _number(estimates.get("net_arpu_gain_mean"))
    baseline = mean - sum(marginal, Decimal(0))

    def forecast(selected):
        n = len(selected)
        unchanged = n == len(rows)
        return {
            "campaign_ids": [row["id"] for row in selected],
            "pilot_cost": float(pilot_cost), "pilot_contacts": pilot_contacts,
            "total_cost": float(pilot_cost + sum(
                (_number(row["metrics"]["cost"]) for row in selected), Decimal(0))),
            "total_contacts": pilot_contacts + sum(
                row["metrics"]["n_contacts"] for row in selected),
            "net_arpu_gain_mean": float(baseline + sum(marginal[:n], Decimal(0))),
            "lower_tail_mean_10": estimates.get("lower_tail_mean_10") if unchanged else None,
            "objective": estimates.get("objective") if unchanged else None,
        }

    selected, spent, reached = [], pilot_cost, pilot_contacts
    for row in rows:
        metrics = row["metrics"]
        cost, contacts = _number(metrics.get("cost")), metrics["n_contacts"]
        if (channels is not None and row["campaign"]["channel"] not in channels
                or spent + cost > budget or reached + contacts > config["max_contacts"]):
            break
        selected.append(row)
        spent, reached = spent + cost, reached + contacts
    if not selected:
        raise ValueError("No saved campaign prefix fits the constraints")
    return _artifact("comparison", "Сравнение сохранённого портфеля", snapshot, {
        "source": SOURCE, "method": "saved_portfolio_prefix_fixed_cohorts",
        "constraints": deepcopy(constraints), "original": forecast(rows),
        "alternative": forecast(selected), "simulator_result": None,
        "limitations": LIMITATIONS + list(estimates.get("limitations", [])),
    })
