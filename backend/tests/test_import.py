import csv

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from apps.campaigns.services.datasets import REQUIRED_COLUMNS
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def write_kit(path, rows):
    (path / "data").mkdir(exist_ok=True)
    data = {
        "data/dict_tariff.csv": {"tariff_plan_code": "tariff_1", "price_tariff": "10"},
        "tariff_dictionary.csv": {"tariff_plan_code": "tariff_1", "price_tariff": "10"},
        "feature_dictionary.csv": {"feature": "ID_NUMBER", "unit": "-",
                                   "description": "Identifier"},
        "data/traffic.csv": {"ID_NUMBER": "1", "time_key": "2026-01-01",
                             "tariff_plan_code": "tariff_1"},
        "data/arpu_monthly.csv": {"ID_NUMBER": "1", "TIME_KEY": "2026-01-01",
                                  "ARPU_1M": "10"},
        "data/change_tariff.csv": {"ID_NUMBER": "1", "TIME_KEY": "2026-01-01",
                                   "tariff_plan_code_from": "tariff_1",
                                   "tariff_plan_code_to": "tariff_1",
                                   "AVG_ARPU_PREV_3M": "10", "AVG_ARPU_NEXT_3M": "11"},
    }
    for name, values in data.items():
        with (path / name).open("w", newline="") as file:
            header = sorted(REQUIRED_COLUMNS[name])
            writer = csv.DictWriter(file, fieldnames=header)
            writer.writeheader()
            writer.writerow(values)
    with (path / "customer_profile.csv").open("w", newline="") as file:
        header = sorted(REQUIRED_COLUMNS["customer_profile.csv"])
        writer = csv.DictWriter(file, fieldnames=header)
        writer.writeheader()
        columns = ["ID_NUMBER", "predicted_arpu", "current_tariff", "arpu_segment",
                   "data_segment", "call_segment"]
        for row in rows:
            if len(row) != len(columns):
                file.write(",".join(row) + "\n")
            else:
                writer.writerow(dict(zip(columns, row, strict=True)))


def test_import_is_idempotent_and_aggregates_actual_rows(tmp_path):
    write_kit(tmp_path, [["1", "12.50", "tariff_1", "LOW", "LITE", "LOW"],
                         ["2", "20.25", "tariff_1", "MID", "HEAVY", "MEDIUM"]])
    call_command("import_participant_data", path=tmp_path)
    dataset = Dataset.objects.get()
    run = CampaignRun.objects.create(name="Existing", dataset=dataset)
    call_command("import_participant_data", path=tmp_path)
    assert Dataset.objects.count() == 1
    assert Dataset.objects.get().id == dataset.id
    assert CampaignRun.objects.get(pk=run.pk).dataset_id == dataset.id
    assert dataset.customer_count == 2
    assert dataset.summary["baseline_arpu"] == "32.75"
    assert dataset.summary["segments"]["arpu_segment"] == {"LOW": 1, "MID": 1}


@pytest.mark.parametrize("bad_row", [
    ["1", "NaN", "tariff_1", "LOW", "LITE", "LOW"],
    ["1", "-1", "tariff_1", "LOW", "LITE", "LOW"],
    ["1", "20", "unknown", "LOW", "LITE", "LOW"],
    ["1", "20", "tariff_1", "unknown", "LITE", "LOW"],
    ["", "20", "tariff_1", "LOW", "LITE", "LOW"],
    ["1", "20", "tariff_1", "LOW", "LITE"],
    ["1", "20", "tariff_1", "LOW", "LITE", "LOW", "extra"],
])
def test_corrupt_data_does_not_create_dataset(tmp_path, bad_row):
    write_kit(tmp_path, [bad_row])
    with pytest.raises(CommandError):
        call_command("import_participant_data", path=tmp_path)
    assert not Dataset.objects.exists()


def test_duplicate_ids_are_rejected_without_partial_import(tmp_path):
    row = ["1", "20", "tariff_1", "LOW", "LITE", "LOW"]
    write_kit(tmp_path, [row, row])
    with pytest.raises(CommandError, match="Duplicate"):
        call_command("import_participant_data", path=tmp_path)
    assert not Dataset.objects.exists()


def test_missing_segments_in_official_data_are_preserved_as_unknown(tmp_path):
    write_kit(tmp_path, [["1", "20", "", "", "", "LOW"]])
    call_command("import_participant_data", path=tmp_path)
    dataset = Dataset.objects.get()
    assert dataset.summary["segments"]["arpu_segment"] == {"UNKNOWN": 1}
    assert dataset.summary["segments"]["data_segment"] == {"UNKNOWN": 1}
    assert dataset.summary["missing_current_tariff"] == 1


@pytest.mark.parametrize("filename,content", [
    ("data/dict_tariff.csv", "tariff_plan_code,price_tariff\ntariff_1,NaN\n"),
    ("data/dict_tariff.csv", "tariff_plan_code,price_tariff\ntariff_1,10\ntariff_1,10\n"),
    ("tariff_dictionary.csv", "tariff_plan_code,price_tariff\ntariff_2,10\n"),
    ("feature_dictionary.csv", "feature,unit,description\nID_NUMBER,-,ok\nID_NUMBER,-,bad\n"),
    ("data/arpu_monthly.csv", "ID_NUMBER,TIME_KEY,ARPU_1M\n1,2026-01-01,bad\n"),
    ("data/change_tariff.csv", "ID_NUMBER,TIME_KEY,tariff_plan_code_from,"
     "tariff_plan_code_to,AVG_ARPU_PREV_3M,AVG_ARPU_NEXT_3M\n"
     "1,2026-01-01,tariff_1,tariff_2,10,11\n"),
    ("data/traffic.csv", "ID_NUMBER,time_key,tariff_plan_code\n1,2026-01-01,unknown\n"),
    ("customer_profile.csv", "ID_NUMBER,predicted_arpu,current_tariff,"
     "arpu_segment,data_segment,call_segment,call_segment\n"
     "1,20,tariff_1,LOW,LITE,LOW,LOW\n"),
])
def test_corrupt_csv_or_dictionary_keeps_existing_dataset(tmp_path, filename, content):
    write_kit(tmp_path, [["1", "20", "tariff_1", "LOW", "LITE", "LOW"]])
    call_command("import_participant_data", path=tmp_path)
    old_id = Dataset.objects.get().id
    (tmp_path / filename).write_text(content)
    with pytest.raises(CommandError):
        call_command("import_participant_data", path=tmp_path)
    assert list(Dataset.objects.values_list("id", flat=True)) == [old_id]


def test_missing_file_is_rejected(tmp_path):
    write_kit(tmp_path, [["1", "20", "tariff_1", "LOW", "LITE", "LOW"]])
    (tmp_path / "data/traffic.csv").unlink()
    with pytest.raises(CommandError, match="Missing participant CSVs"):
        call_command("import_participant_data", path=tmp_path)
    assert not Dataset.objects.exists()


@pytest.mark.parametrize("filename,column,value", [
    ("customer_profile.csv", "ARPU_current", "broken"),
    ("data/traffic.csv", "LTE_DATA_VOLUME", "Infinity"),
    ("data/arpu_monthly.csv", "ARPU_1M", "broken"),
    ("data/change_tariff.csv", "tariff_plan_code_to", "tariff_99"),
    ("data/dict_tariff.csv", "price_tariff", "-1"),
])
def test_invalid_value_in_complete_csv_is_rejected(tmp_path, filename, column, value):
    write_kit(tmp_path, [["1", "20", "tariff_1", "LOW", "LITE", "LOW"]])
    file_path = tmp_path / filename
    with file_path.open(newline="") as file:
        reader = csv.DictReader(file)
        header = reader.fieldnames
        row = next(reader)
    row[column] = value
    with file_path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=header)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(CommandError):
        call_command("import_participant_data", path=tmp_path)
    assert not Dataset.objects.exists()
