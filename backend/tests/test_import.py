import csv

import pytest
from apps.campaigns.models import Dataset
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def write_kit(path, rows):
    (path / "data").mkdir(exist_ok=True)
    (path / "data/dict_tariff.csv").write_text("tariff_plan_code\ntariff_1\n")
    with (path / "customer_profile.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID_NUMBER", "predicted_arpu", "current_tariff", "arpu_segment",
                         "data_segment", "call_segment"])
        writer.writerows(rows)


def test_import_is_idempotent_and_aggregates_actual_rows(tmp_path):
    write_kit(tmp_path, [["1", "12.50", "tariff_1", "LOW", "LITE", "LOW"],
                         ["2", "20.25", "tariff_1", "MID", "HEAVY", "MEDIUM"]])
    call_command("import_participant_data", path=tmp_path)
    first_id = Dataset.objects.get().id
    call_command("import_participant_data", path=tmp_path)
    dataset = Dataset.objects.get()
    assert dataset.id == first_id
    assert dataset.customer_count == 2
    assert dataset.summary["baseline_arpu"] == "32.75"
    assert dataset.summary["segments"]["arpu_segment"] == {"LOW": 1, "MID": 1}
    assert hasattr(dataset, "subscribers")
    assert dataset.subscribers.count() == 2
    assert dataset.subscribers.get(id_number="1").profile == {
        "ID_NUMBER": "1", "predicted_arpu": "12.50", "current_tariff": "tariff_1",
        "arpu_segment": "LOW", "data_segment": "LITE", "call_segment": "LOW",
    }


def test_import_preserves_additional_profile_columns(tmp_path):
    write_kit(tmp_path, [["1", "12.50", "tariff_1", "LOW", "LITE", "LOW"]])
    profiles = tmp_path / "customer_profile.csv"
    with profiles.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ID_NUMBER", "predicted_arpu", "current_tariff", "arpu_segment",
                         "data_segment", "call_segment", "ARPU_current"])
        writer.writerow(["1", "12.50", "tariff_1", "LOW", "LITE", "LOW", "3.1400"])
    call_command("import_participant_data", path=tmp_path)
    dataset = Dataset.objects.get()
    assert hasattr(dataset, "subscribers")
    assert dataset.subscribers.get(id_number="1").profile["ARPU_current"] == "3.1400"


@pytest.mark.parametrize("bad_row", [
    ["1", "NaN", "tariff_1", "LOW", "LITE", "LOW"],
    ["1", "20", "unknown", "LOW", "LITE", "LOW"],
    ["1", "20", "tariff_1", "unknown", "LITE", "LOW"],
    ["", "20", "tariff_1", "LOW", "LITE", "LOW"],
])
def test_corrupt_data_does_not_create_dataset(tmp_path, bad_row):
    write_kit(tmp_path, [bad_row])
    with pytest.raises(CommandError):
        call_command("import_participant_data", path=tmp_path)
    assert not Dataset.objects.exists()


def test_duplicate_ids_are_rejected(tmp_path):
    row = ["1", "20", "tariff_1", "LOW", "LITE", "LOW"]
    write_kit(tmp_path, [row, row])
    with pytest.raises(CommandError, match="Duplicate"):
        call_command("import_participant_data", path=tmp_path)
    assert not Dataset.objects.exists()


def test_missing_segments_in_official_data_are_preserved_as_unknown(tmp_path):
    write_kit(tmp_path, [["1", "20", "", "", "", "LOW"]])
    call_command("import_participant_data", path=tmp_path)
    dataset = Dataset.objects.get()
    assert dataset.customer_count == 1
    assert dataset.summary["segments"]["arpu_segment"] == {"UNKNOWN": 1}
    assert dataset.summary["missing_current_tariff"] == 1
