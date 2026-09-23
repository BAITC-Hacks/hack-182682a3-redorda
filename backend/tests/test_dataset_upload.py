import csv
import io
from pathlib import Path

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from .test_import import write_kit

pytestmark = pytest.mark.django_db
FILENAMES = ("dict_tariff.csv", "traffic.csv", "arpu_monthly.csv", "change_tariff.csv")
UPLOAD_URL = "/api/v1/datasets/import/"
DEMO_URL = "/api/v1/datasets/import-demo/"


@pytest.fixture
def client():
    result = APIClient()
    result.force_authenticate(user=get_user_model().objects.create_user(username="importer"))
    return result


@pytest.fixture
def raw_files(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.ROOT_DIR = tmp_path
    source = tmp_path / "data" / "demo"
    source.mkdir(parents=True)
    write_kit(source, [["1", "12.50", "tariff_1", "LOW", "LITE", "LOW"]])
    for name in FILENAMES:
        (source / name).write_bytes((source / "data" / name).read_bytes())
    return {name: (source / name).read_bytes() for name in FILENAMES}


def uploads(files):
    return [SimpleUploadedFile(name, content, content_type="text/csv")
            for name, content in files.items()]


def replace_value(content, column, value):
    reader = csv.DictReader(io.StringIO(content.decode()))
    row = next(reader)
    row[column] = value
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode()


def assert_invalid(response):
    assert response.status_code == 400, response.content
    assert response.data["error"]["code"] == "invalid_dataset"
    assert set(response.data["error"]) == {"code", "message", "fields"}


def test_upload_persists_only_four_raw_files_and_reports_actual_counts(client, raw_files, settings):
    # Repeat one subscriber in traffic: customer_count must use distinct traffic IDs.
    lines = raw_files["traffic.csv"].splitlines(keepends=True)
    raw_files["traffic.csv"] += lines[1]
    response = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert response.status_code == 201, response.content
    dataset = Dataset.objects.get()
    assert response.data["id"] == str(dataset.id)
    assert response.data["customer_count"] == 1
    assert "source_dir" not in response.data
    assert response.data["summary"] == {
        "baseline_arpu": None,
        "tariff_count": 1,
        "synthetic": None,
        "segments": {"arpu_segment": {}, "data_segment": {}, "call_segment": {}},
        "source_kind": "upload",
        "file_rows": {"dict_tariff.csv": 1, "traffic.csv": 2,
                      "arpu_monthly.csv": 1, "change_tariff.csv": 1},
        "format": "raw_csv",
    }
    source = Path(dataset.source_dir)
    assert source.is_relative_to(settings.MEDIA_ROOT / "datasets")
    assert {str(path.relative_to(source)) for path in source.rglob("*") if path.is_file()} == {
        f"data/{name}" for name in FILENAMES
    }
    for name, content in raw_files.items():
        assert (source / "data" / name).read_bytes() == content
    assert client.get("/api/v1/datasets/current/").data["id"] == str(dataset.id)


def test_duplicate_reactivates_existing_dataset_and_keeps_old_run_reference(client, raw_files,
                                                                          settings):
    first = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert first.status_code == 201, first.content
    original = Dataset.objects.get()
    original_path = original.source_dir
    run = CampaignRun.objects.create(name="Original", dataset=original)
    changed = dict(raw_files)
    changed["arpu_monthly.csv"] = replace_value(changed["arpu_monthly.csv"], "ARPU_1M", "27")
    second = client.post(UPLOAD_URL, {"files": uploads(changed)}, format="multipart")
    assert second.status_code == 201
    assert second.data["id"] != first.data["id"]
    again = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert again.status_code == 200
    assert again.data["id"] == first.data["id"]
    assert client.get("/api/v1/datasets/current/").data["id"] == first.data["id"]
    assert Dataset.objects.count() == 2
    original.refresh_from_db()
    run.refresh_from_db()
    assert original.source_dir == original_path
    assert run.dataset_id == original.id
    assert len(list((settings.MEDIA_ROOT / "datasets").iterdir())) == 2


def test_demo_copies_bundled_files_and_is_idempotent(client, raw_files, settings):
    response = client.post(DEMO_URL, {}, format="json")
    assert response.status_code == 201, response.content
    dataset = Dataset.objects.get()
    assert response.data["summary"]["source_kind"] == "demo"
    assert response.data["summary"]["synthetic"] is True
    assert dataset.source_dir != str(settings.ROOT_DIR / "data" / "demo")
    assert client.post(DEMO_URL).status_code == 200
    assert Dataset.objects.count() == 1
    for name, content in raw_files.items():
        assert (Path(dataset.source_dir) / "data" / name).read_bytes() == content


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "wrong_case", "empty",
                                 "unknown_field", "text_in_files", "five_files"])
def test_rejects_bad_upload_set_without_changing_current(client, raw_files, settings, case):
    old = Dataset.objects.create(name="Old", checksum="a" * 64,
                                 source_dir="/old", customer_count=2)
    files = uploads(raw_files)
    payload = {"files": files}
    if case == "missing":
        files.pop()
    elif case == "duplicate":
        files[-1] = uploads(raw_files)[0]
    elif case == "unknown":
        files[-1] = SimpleUploadedFile("unwanted.csv", b"x")
    elif case == "wrong_case":
        files[-1].name = "CHANGE_TARIFF.csv"
    elif case == "empty":
        files[-1] = SimpleUploadedFile("change_tariff.csv", b"")
    elif case == "unknown_field":
        payload["source_dir"] = "/private/path"
    elif case == "text_in_files":
        files.append("not a file")
    elif case == "five_files":
        files.append(uploads(raw_files)[0])
    assert_invalid(client.post(UPLOAD_URL, payload, format="multipart"))
    assert Dataset.objects.get().id == old.id
    assert not settings.MEDIA_ROOT.exists() or not list(settings.MEDIA_ROOT.rglob("*.csv"))


@pytest.mark.parametrize("filename,column,value", [
    ("dict_tariff.csv", "price_tariff", "NaN"),
    ("dict_tariff.csv", "price_tariff", "-1"),
    ("traffic.csv", "LTE_DATA_VOLUME", "Infinity"),
    ("traffic.csv", "ID_NUMBER", ""),
    ("traffic.csv", "tariff_plan_code", "missing"),
    ("arpu_monthly.csv", "ARPU_1M", "broken"),
    ("change_tariff.csv", "tariff_plan_code_to", "missing"),
])
def test_rejects_invalid_raw_values_and_cleans_staging(client, raw_files, settings,
                                                     filename, column, value):
    raw_files[filename] = replace_value(raw_files[filename], column, value)
    response = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert_invalid(response)
    assert filename in response.data["error"]["message"]
    assert not Dataset.objects.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.csv"))
    assert str(settings.MEDIA_ROOT) not in str(response.data)


@pytest.mark.parametrize("content", [b"", b"ID_NUMBER,TIME_KEY,ARPU_1M\n",
                                    b"ID_NUMBER,TIME_KEY,ARPU_1M\n1,t,\xff",
                                    b"ID_NUMBER,TIME_KEY,ARPU_1M\n1,t,10,extra\n",
                                    b"ID_NUMBER,TIME_KEY\n1,t\n"])
def test_rejects_empty_malformed_and_invalid_encoding_csv(client, raw_files, settings, content):
    raw_files["arpu_monthly.csv"] = content
    response = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert_invalid(response)
    assert not Dataset.objects.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.csv"))
    assert str(settings.MEDIA_ROOT) not in str(response.data)


@pytest.mark.parametrize("url,payload,content_type", [
    (UPLOAD_URL, b"{", "application/json"),
    (UPLOAD_URL, b"bad multipart", "multipart/form-data"),
    (DEMO_URL, b"{", "application/json"),
    (DEMO_URL, b'{"path":"/private"}', "application/json"),
    (DEMO_URL, b"[]", "application/json"),
])
def test_invalid_request_uses_error_envelope(client, raw_files, url, payload, content_type):
    assert_invalid(client.post(url, payload, content_type=content_type))
    assert not Dataset.objects.exists()


def test_size_limits_reject_before_activation(client, raw_files, settings, monkeypatch):
    from apps.campaigns.services import raw_datasets

    monkeypatch.setattr(raw_datasets, "MAX_FILE_BYTES", 100)
    assert_invalid(client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart"))
    monkeypatch.setattr(raw_datasets, "MAX_FILE_BYTES", 1024 * 1024)
    monkeypatch.setattr(raw_datasets, "MAX_TOTAL_BYTES", 100)
    assert_invalid(client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart"))
    assert not Dataset.objects.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.csv"))


def test_missing_demo_is_unavailable_without_exposing_server_path(client, raw_files, settings):
    (settings.ROOT_DIR / "data" / "demo" / "traffic.csv").unlink()
    response = client.post(DEMO_URL, {}, format="json")
    assert response.status_code == 503
    assert set(response.data["error"]) == {"code", "message", "fields"}
    assert str(settings.ROOT_DIR) not in str(response.data)
    assert not Dataset.objects.exists()


@pytest.mark.parametrize("url", [UPLOAD_URL, DEMO_URL])
def test_import_requires_auth_and_session_csrf(raw_files, settings, url):
    settings.REDORDA_REQUIRE_AUTH = True
    client = APIClient(enforce_csrf_checks=True)
    assert client.post(url).status_code == 403
    user = get_user_model().objects.create_user(username="session-importer")
    client.force_login(user)
    assert client.post(url).status_code == 403
    assert not Dataset.objects.exists()


def test_uploaded_demo_bytes_reuse_original_provenance(client, raw_files):
    uploaded = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert uploaded.status_code == 201
    demo = client.post(DEMO_URL, {}, format="json")
    assert demo.status_code == 200
    assert demo.data["id"] == uploaded.data["id"]
    assert demo.data["summary"]["source_kind"] == "upload"
    assert demo.data["summary"]["synthetic"] is None
    assert Dataset.objects.count() == 1


def test_real_bundled_demo_imports_without_fabricated_predictions(client, tmp_path, settings):
    settings.ROOT_DIR = Path(__file__).resolve().parents[2]
    settings.MEDIA_ROOT = tmp_path / "media"
    response = client.post(DEMO_URL, {}, format="json")
    assert response.status_code == 201, response.content
    assert response.data["customer_count"] == 14875
    assert response.data["summary"]["tariff_count"] == 21
    assert response.data["summary"]["baseline_arpu"] is None
    assert response.data["summary"]["file_rows"] == {
        "dict_tariff.csv": 21, "traffic.csv": 75736,
        "arpu_monthly.csv": 78798, "change_tariff.csv": 14823,
    }
    created = client.post("/api/v1/runs/", {"name": "Raw data draft"}, format="json")
    assert created.status_code == 201
    assert created.data["dataset_id"] == response.data["id"]


def test_storage_failure_is_sanitized_without_replacing_current(client, raw_files, settings):
    old = Dataset.objects.create(name="Old", checksum="b" * 64,
                                 source_dir="/old", customer_count=2)
    settings.MEDIA_ROOT.write_text("Not a directory")
    response = client.post(UPLOAD_URL, {"files": uploads(raw_files)}, format="multipart")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "dataset_import_unavailable"
    assert str(settings.MEDIA_ROOT) not in str(response.data)
    assert Dataset.objects.get().id == old.id


def test_upload_schema_accepts_binary_files_and_nullable_raw_statistics(client):
    schema = client.get("/api/schema/?format=json").json()
    upload = schema["components"]["schemas"]["DatasetUpload"]
    assert upload["properties"]["files"]["items"]["format"] == "binary"
    summary = schema["components"]["schemas"]["DatasetSummary"]
    assert summary["properties"]["baseline_arpu"]["nullable"] is True
    assert summary["properties"]["synthetic"]["nullable"] is True
