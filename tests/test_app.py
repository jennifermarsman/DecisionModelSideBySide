import pytest

import classifiers
from app import app
from scenarios import SCENARIOS, load_data


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


LABELS = [
    {"name": "Sales - New Purchase", "description": "Wants to buy"},
    {"name": "Technical Support", "description": "Needs help"},
    {"name": "Other", "description": ""},
]


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_scenarios_have_data_and_labels(key):
    scenario = SCENARIOS[key]
    items = load_data(scenario["data_file"])
    assert len(items) >= 20
    assert not any(i.startswith("#") for i in items)
    assert len(scenario["labels"]) >= 3


def test_config_and_data_endpoints(client):
    config = client.get("/api/config").get_json()
    assert set(config["scenarios"]) == {"customer", "expenses"}
    assert client.get("/api/data/expenses").get_json()["items"]
    assert client.get("/api/data/nope").status_code == 404


def test_match_label():
    assert classifiers.match_label("technical support.", LABELS) == "Technical Support"
    assert classifiers.match_label("Label: Other", LABELS) == "Other"


def test_label_keys_are_unique_identifiers():
    keys = classifiers.label_keys(LABELS + [{"name": "other!"}])
    assert keys == {
        "sales_new_purchase": "Sales - New Purchase",
        "technical_support": "Technical Support",
        "other": "Other",
        "other_2": "other!",
    }


def test_decision_request_and_response(monkeypatch):
    monkeypatch.setenv("DECISION_DEPLOYMENT", "my-decision")
    body, keys = classifiers.build_decision_request("My laptop won't boot", LABELS)
    assert body["model"] == "my-decision"
    assert body["state"] == "My laptop won't boot"
    question = body["questions"][classifiers.QUESTION_NAME]
    assert question["type"] == "choice"
    assert question["criteria"]["other"] == "Other"
    response = {"answers": {"category": {"type": "choice", "choice": "technical_support", "confidence": 0.9}}}
    assert classifiers.parse_decision_response(response, keys) == ("Technical Support", 0.9)


def test_classify_decision_calls_endpoint(monkeypatch, client):
    monkeypatch.setenv("DECISION_ENDPOINT", "https://example.services.ai.azure.com/")
    monkeypatch.setenv("DECISION_API_KEY", "test-key")
    monkeypatch.setenv("DECISION_DEPLOYMENT", "my-decision")
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"answers": {"category": {"choice": "sales_new_purchase", "confidence": 0.8}}}

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers))
        return FakeResponse()

    monkeypatch.setattr(classifiers._session, "post", fake_post)
    resp = client.post("/api/classify/decision", json={"text": "I want 10 licenses", "labels": LABELS})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["label"] == "Sales - New Purchase"
    assert data["model_ms"] >= 0
    assert calls[0][0] == "https://example.services.ai.azure.com/providers/microsoft/v1/systemone"
    assert calls[0][1]["api-key"] == "test-key"


def test_classify_validation_and_mock(monkeypatch, client):
    assert client.post("/api/classify/other", json={}).status_code == 404
    assert client.post("/api/classify/openai", json={"text": "hi", "labels": []}).status_code == 400
    monkeypatch.setenv("MOCK_MODELS", "true")
    monkeypatch.setattr(classifiers.time, "sleep", lambda s: None)
    data = client.post("/api/classify/openai", json={"text": "Needs help", "labels": LABELS}).get_json()
    assert data["label"] in {l["name"] for l in LABELS}
    assert "Respond with only the label name" in data["prompt"]


def test_missing_configuration_is_reported(monkeypatch, client):
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    monkeypatch.delenv("DECISION_ENDPOINT", raising=False)
    resp = client.post("/api/classify/decision", json={"text": "hi", "labels": LABELS})
    assert resp.status_code == 500
    assert "DECISION_ENDPOINT" in resp.get_json()["error"]
