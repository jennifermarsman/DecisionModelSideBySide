from types import SimpleNamespace

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


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_request_previews_match_classification_prompts(monkeypatch, client, scenario):
    monkeypatch.setenv("MOCK_MODELS", "true")
    monkeypatch.setattr(classifiers.time, "sleep", lambda s: None)
    labels = LABELS + [{"name": " Other! ", "description": " A custom label "}]
    response = client.post(f"/api/data/{scenario}", json={"labels": labels})
    assert response.status_code == 200
    data = response.get_json()
    assert data["items"] == client.get(f"/api/data/{scenario}").get_json()["items"]
    assert len(data["prompts"]) == len(data["items"])
    for model in ("openai", "decision"):
        for index in (0, len(data["items"]) - 1):
            result = client.post(
                f"/api/classify/{model}", json={"text": data["items"][index], "labels": labels}
            )
            assert result.status_code == 200
            assert data["prompts"][index][model] == result.get_json()["prompt"]


@pytest.mark.parametrize("body", [[], {"labels": "invalid"}, {"labels": []}, {"labels": [{}]}])
def test_request_previews_validate_labels(client, body):
    assert client.post("/api/data/expenses", json=body).status_code == 400


def test_match_label():
    assert classifiers.match_label("technical support.", LABELS) == "Technical Support"
    assert classifiers.match_label("Label: Other", LABELS) == "Other"


def test_classify_openai_uses_model_default_temperature(monkeypatch, client):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-reasoning-deployment")
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    calls = []

    def fake_create(**kwargs):
        assert "temperature" not in kwargs
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Technical Support"))]
        )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(classifiers, "_openai_client", lambda: fake_client)
    resp = client.post("/api/classify/openai", json={"text": "Needs help", "labels": LABELS})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["label"] == "Technical Support"
    assert data["model_ms"] >= 0
    assert "Respond with only the label name" in data["prompt"]
    assert calls == [{
        "model": "my-reasoning-deployment",
        "messages": classifiers.build_openai_messages("Needs help", LABELS),
    }]


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


def test_decision_refusal():
    keys = classifiers.label_keys(LABELS)
    assert classifiers.parse_decision_response({"answers": {"category": {"type": "refusal"}}}, keys) == ("(refused)", None)


def test_decision_uses_entra_id_without_api_key(monkeypatch):
    monkeypatch.delenv("DECISION_API_KEY", raising=False)

    class FakeCredential:
        def get_token(self, scope):
            assert scope == "https://cognitiveservices.azure.com/.default"
            return type("Token", (), {"token": "entra-token"})()

    monkeypatch.setattr(classifiers, "_entra_credential", lambda: FakeCredential())
    headers = classifiers._decision_headers()
    assert headers["Authorization"] == "Bearer " + "entra-token"
    assert "api-key" not in headers
