"""Streaming CSV of the already persisted final campaigns."""

import csv
from io import StringIO
from typing import Iterable

from .results import _campaign, _saved_result

CAMPAIGN_COLUMNS = (
    "campaign_name", "filter_arpu_segment", "filter_data_segment",
    "filter_call_segment", "filter_current_tariff", "target_tariff", "channel",
)


def iter_submission_csv(run_id) -> Iterable[str]:
    """Yield deterministic UTF-8-compatible CSV text, including the header."""
    _run, _result, rows = _saved_result(run_id)
    campaigns = [_campaign(row.campaign) for row in rows]
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CAMPAIGN_COLUMNS)
    yield buffer.getvalue()
    for campaign in campaigns:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow([campaign.get(column, "") for column in CAMPAIGN_COLUMNS])
        yield buffer.getvalue()
