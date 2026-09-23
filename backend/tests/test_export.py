import csv
from io import StringIO

import pytest
from apps.campaigns.services.export import CAMPAIGN_COLUMNS
from apps.campaigns.services.results import InvalidSavedResult, ResultNotReady, iter_submission_csv

from tests.test_results import saved_run


def test_order_columns_unicode_quotes_and_repeated_download(monkeypatch):
    run = saved_run(monkeypatch, campaign={
        "campaign_name": 'Алматы, "Жаңа"\nтариф', "target_tariff": "tariff_2",
        "channel": "sms", "filter_arpu_segment": "MID",
        "filter_current_tariff": "tariff_1;tariff_3",
    })
    run.campaign_results.append(type(run.campaign_results[0])(
        rank=2, campaign={"campaign_name": "Қазақша", "target_tariff": "tariff_4",
                          "channel": "push"}, metrics={}, explanation=""))
    run.campaign_results.reverse()
    first = "".join(iter_submission_csv(run.id))
    second = "".join(iter_submission_csv(run.id))
    assert first == second
    assert first.encode("utf-8").decode("utf-8") == first
    parsed = list(csv.reader(StringIO(first)))
    assert parsed[0] == list(CAMPAIGN_COLUMNS)
    assert parsed[1][0] == 'Алматы, "Жаңа"\nтариф'
    assert parsed[1][4] == "tariff_1;tariff_3"
    assert parsed[2][0] == "Қазақша"
    assert parsed[2][1] == ""


def test_unfinished_run_cannot_be_exported(monkeypatch):
    run = saved_run(monkeypatch, status="running")
    with pytest.raises(ResultNotReady):
        list(iter_submission_csv(run.id))


def test_export_rejects_empty_and_too_many_campaigns(monkeypatch):
    run = saved_run(monkeypatch)
    run.campaign_results.clear()
    with pytest.raises(InvalidSavedResult):
        list(iter_submission_csv(run.id))
    row = type(run.campaign_results)
    run.campaign_results = row([type("CampaignRow", (), {
        "rank": rank, "campaign": {"campaign_name": str(rank),
                                     "target_tariff": "tariff_2", "channel": "sms"},
    })() for rank in range(1, 12)])
    with pytest.raises(InvalidSavedResult):
        list(iter_submission_csv(run.id))
