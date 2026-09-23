from decimal import Decimal

import pytest
from apps.campaigns.models import CampaignRun, Dataset
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset():
    return Dataset.objects.create(name="Test", checksum="a" * 64, source_dir="/unused",
                                  customer_count=12, summary={})


def test_no_dataset_is_an_explicit_empty_state():
    client = APIClient()
    assert client.get("/api/v1/datasets/current/").status_code == 404
    response = client.post("/api/v1/runs/", {"name": "October"}, format="json")
    assert response.status_code == 409
    assert response.data["error"]["code"] == "dataset_required"
    assert not CampaignRun.objects.exists()


def test_create_and_retrieve_persisted_draft(dataset):
    client = APIClient()
    response = client.post("/api/v1/runs/", {"name": "October", "budget": "50000.50"},
                           format="json")
    assert response.status_code == 201, response.data
    run = CampaignRun.objects.get(pk=response.data["id"])
    assert run.dataset == dataset
    assert run.budget == Decimal("50000.50")
    assert run.status == "draft"
    retrieved = client.get(f"/api/v1/runs/{run.id}/")
    assert retrieved.data["dataset_id"] == str(dataset.id)
    assert retrieved.data["budget"] == "50000.50"
    assert client.get("/api/v1/runs/").data["count"] == 1


@pytest.mark.parametrize("field,value", [
    ("budget", "100000.01"), ("budget", "0"), ("max_contacts", 15001),
    ("max_pilots", 21), ("max_pilots", 0), ("seed", -1), ("strategy", "openai"),
])
def test_invalid_run_does_not_get_persisted(dataset, field, value):
    response = APIClient().post("/api/v1/runs/", {"name": "Invalid", field: value}, format="json")
    assert response.status_code == 400
    assert field in response.data["error"]["fields"]
    assert not CampaignRun.objects.exists()


def test_metadata_does_not_advertise_unimplemented_features():
    response = APIClient().get("/api/v1/meta/")
    assert response.data["features"]["run_execution"] is False
    assert response.data["limits"]["contacts"] == 15000
