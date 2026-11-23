import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import classifiers
import app as app_module
from app import app
from scenarios import SCENARIOS, load_data


@pytest.fixture(autouse=True)
def default_authentication(monkeypatch):
    monkeypatch.delenv("MODEL_AUTH_MODE", raising=False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


LABELS = [
    {"name": "Sales - New Purchase", "description": "Wants to buy"},
    {"name": "Technical Support", "description": "Needs help"},
    {"name": "Other", "description": ""},
]


@pytest.mark.parametrize("model", ["openai", "decision"])
@pytest.mark.parametrize("route", ["classify", "run"])
def test_invalid_authentication_mode_returns_json(monkeypatch, client, model, route):
    monkeypatch.setenv("MODEL_AUTH_MODE", "typo")
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    monkeypatch.setitem(
        app_module.CLASSIFIERS, model,
        lambda *args: pytest.fail("Invalid configuration must not call the model"),
    )
    endpoint = f"/api/classify/{model}" if route == "classify" else f"/api/run/{model}/expenses"
    response = client.post(endpoint, json={"text": "Needs help", "labels": LABELS})
    assert response.status_code == 500
    assert response.is_json
    assert response.get_json() == {
        "error": "Invalid configuration: MODEL_AUTH_MODE must be 'entra' or 'api-key'.",
    }


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


@pytest.mark.parametrize("endpoint", ["/api/data/expenses", "/api/classify/openai", "/api/run/openai/expenses"])
@pytest.mark.parametrize("limit", ["count", "name", "description"])
def test_endpoints_reject_excessive_labels(client, endpoint, limit):
    labels = LABELS
    if limit == "count":
        labels = LABELS * 34
    elif limit == "name":
        labels = [{"name": "x" * (app_module.MAX_LABEL_NAME_LENGTH + 1)}]
    else:
        labels = [{"name": "valid", "description": "x" * (app_module.MAX_LABEL_DESCRIPTION_LENGTH + 1)}]
    body = {"labels": labels}
    if endpoint == "/api/classify/openai":
        body["text"] = "Example"
    response = client.post(endpoint, json=body)
    assert response.status_code == 400


def test_request_body_size_is_limited(client):
    body = '{"text":"' + ("x" * app_module.MAX_REQUEST_BYTES) + '","labels":[]}'
    response = client.post("/api/classify/openai", data=body, content_type="application/json")
    assert response.status_code == 413
    assert "must not exceed" in response.get_json()["error"]


def test_match_label():
    assert classifiers.match_label("technical support.", LABELS) == "Technical Support"
    assert classifiers.match_label("Label: Other", LABELS) == "Other"


def test_classify_openai_uses_model_default_temperature(monkeypatch, client):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "my-reasoning-deployment")
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    calls = []

    def fake_create(**kwargs):
        assert "temperature" not in kwargs
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Technical Support"))],
            model="gpt-6.1-sol-2026-09-29",
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=25,
                prompt_tokens_details=SimpleNamespace(cached_tokens=20, cache_write_tokens=0),
            ),
        )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(classifiers, "_openai_client", lambda: fake_client)
    resp = client.post("/api/classify/openai", json={"text": "Needs help", "labels": LABELS})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["label"] == "Technical Support"
    assert data["model_ms"] >= 0
    assert data["usage"]["input_tokens"] == 100
    assert data["usage"]["output_tokens"] == 25
    assert data["cost_usd"] == pytest.approx((80 * 2 + 20 * 0.1 + 25 * 10) / 1_000_000)
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
    monkeypatch.setattr(
        classifiers, "_entra_credential",
        lambda: SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="entra-token")),
    )
    monkeypatch.setenv("DECISION_DEPLOYMENT", "my-decision")
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "answers": {"category": {"choice": "sales_new_purchase", "confidence": 0.8}},
                "model": "microsoft-decision-1",
                "usage": {"input_tokens": 100, "output_tokens": 1},
            }

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers))
        return FakeResponse()

    monkeypatch.setattr(classifiers._session, "post", fake_post)
    resp = client.post("/api/classify/decision", json={"text": "I want 10 licenses", "labels": LABELS})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["label"] == "Sales - New Purchase"
    assert data["model_ms"] >= 0
    assert data["usage"] == {"input_tokens": 100, "output_tokens": 1}
    assert data["cost_usd"] == pytest.approx(101 * 0.042 / 1_000_000)
    assert calls[0][0] == "https://example.services.ai.azure.com/providers/microsoft/v1/systemone"
    assert calls[0][1]["Authorization"] == "Bearer entra-token"
    assert "api-key" not in calls[0][1]


def test_classify_validation_and_mock(monkeypatch, client):
    assert client.post("/api/classify/other", json={}).status_code == 404
    assert client.post("/api/classify/openai", json={"text": "hi", "labels": []}).status_code == 400
    monkeypatch.setenv("MOCK_MODELS", "true")
    monkeypatch.setattr(classifiers.time, "sleep", lambda s: None)
    data = client.post("/api/classify/openai", json={"text": "Needs help", "labels": LABELS}).get_json()
    assert data["label"] in {l["name"] for l in LABELS}
    assert "Respond with only the label name" in data["prompt"]
    assert data["cost_usd"] is None
    assert "Mock" in data["cost_unavailable_reason"]


def test_missing_configuration_is_reported(monkeypatch, client):
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    monkeypatch.delenv("DECISION_ENDPOINT", raising=False)
    resp = client.post("/api/classify/decision", json={"text": "hi", "labels": LABELS})
    assert resp.status_code == 500
    assert "DECISION_ENDPOINT" in resp.get_json()["error"]


def test_decision_refusal():
    keys = classifiers.label_keys(LABELS)
    assert classifiers.parse_decision_response({"answers": {"category": {"type": "refusal"}}}, keys) == ("(refused)", None)


def test_decision_always_uses_current_entra_token_even_with_legacy_key(monkeypatch):
    monkeypatch.setenv("DECISION_API_KEY", "obsolete-key")
    tokens = iter(["first-token", "refreshed-token"])

    class FakeCredential:
        def get_token(self, scope):
            assert scope == "https://cognitiveservices.azure.com/.default"
            return SimpleNamespace(token=next(tokens))

    monkeypatch.setattr(classifiers, "_entra_credential", lambda: FakeCredential())
    for token in ("first-token", "refreshed-token"):
        headers = classifiers._decision_headers()
        assert headers["Authorization"] == "Bearer " + token
        assert "api-key" not in headers


@pytest.mark.parametrize("client_id", ["", "managed-client-id"])
def test_credential_selects_managed_identity_or_local_developer(monkeypatch, client_id):
    import azure.identity

    monkeypatch.setenv("AZURE_CLIENT_ID", client_id)
    calls = []
    credential = object()

    def managed(**kwargs):
        calls.append(("managed", kwargs))
        return credential

    def local():
        calls.append(("local", {}))
        return credential

    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", managed)
    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", local)
    classifiers._entra_credential.cache_clear()
    try:
        assert classifiers._entra_credential() is credential
        assert classifiers._entra_credential() is credential
        assert calls == ([("managed", {"client_id": client_id})] if client_id else [("local", {})])
    finally:
        classifiers._entra_credential.cache_clear()


@pytest.mark.parametrize("auth_mode", ["entra", "api-key"])
def test_openai_authentication_requires_explicit_key_mode(monkeypatch, auth_mode):
    import azure.identity
    import httpx2 as httpx

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "obsolete-key")
    monkeypatch.setenv("MODEL_AUTH_MODE", auth_mode)
    monkeypatch.delenv("AZURE_OPENAI_AD_TOKEN", raising=False)
    tokens = iter(["first-token", "refreshed-token"])
    credential = object()
    monkeypatch.setattr(classifiers, "_entra_credential", lambda: credential)
    provider_calls = []

    def provider(actual_credential, scope):
        assert actual_credential is credential
        assert scope == "https://cognitiveservices.azure.com/.default"

        def token():
            value = next(tokens)
            provider_calls.append(value)
            return value

        return token

    monkeypatch.setattr(azure.identity, "get_bearer_token_provider", provider)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 1, "model": "chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Other"},
                         "finish_reason": "stop"}],
        })

    classifiers._openai_client.cache_clear()
    client = classifiers._openai_client()
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
            test_client = client.with_options(http_client=http_client)
            for _ in range(2):
                test_client.chat.completions.create(
                    model="chat", messages=[{"role": "user", "content": "hi"}],
                )
        if auth_mode == "entra":
            assert provider_calls == ["first-token", "refreshed-token"]
            assert [r.headers["Authorization"] for r in requests] == [
                "Bearer first-token", "Bearer refreshed-token",
            ]
            assert all("api-key" not in r.headers for r in requests)
        else:
            assert provider_calls == []
            assert [r.headers["api-key"] for r in requests] == ["obsolete-key", "obsolete-key"]
            assert all("Authorization" not in r.headers for r in requests)
    finally:
        client.close()
        classifiers._openai_client.cache_clear()


def test_decision_explicit_key_mode_does_not_acquire_token(monkeypatch):
    monkeypatch.setenv("MODEL_AUTH_MODE", "api-key")
    monkeypatch.setenv("DECISION_API_KEY", "test-key")
    monkeypatch.setattr(classifiers, "_entra_credential", lambda: pytest.fail("Unexpected token acquisition"))
    assert classifiers._decision_headers() == {
        "Content-Type": "application/json", "api-key": "test-key",
    }


@pytest.mark.parametrize("model,key", [("openai", "AZURE_OPENAI_API_KEY"), ("decision", "DECISION_API_KEY")])
def test_key_mode_reports_missing_key(monkeypatch, model, key):
    monkeypatch.setenv("MODEL_AUTH_MODE", "api-key")
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    monkeypatch.delenv(key, raising=False)
    assert key in classifiers.missing_settings(model)


def test_entra_failure_never_falls_back_to_key(monkeypatch):
    monkeypatch.setenv("DECISION_API_KEY", "obsolete-key")

    def fail():
        raise RuntimeError("Managed identity is unavailable")

    monkeypatch.setattr(classifiers, "_entra_credential", fail)
    with pytest.raises(RuntimeError, match="Managed identity is unavailable"):
        classifiers._decision_headers()


def test_invalid_authentication_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("MODEL_AUTH_MODE", "typo")
    with pytest.raises(ValueError, match="MODEL_AUTH_MODE"):
        classifiers._decision_headers()


@pytest.mark.parametrize("model", ["openai", "decision"])
def test_batch_executes_without_waiting_for_browser_and_reports_server_time(monkeypatch, client, model):
    monkeypatch.setenv("MOCK_MODELS", "true")
    monkeypatch.setattr(app_module, "load_data", lambda path: ["first", "second"])
    times = iter([10, 10.1, 10.25, 10.3])
    monkeypatch.setattr(app_module.time, "perf_counter", lambda: next(times))
    finished = threading.Event()
    calls = []

    def classify(text, labels):
        calls.append(text)
        if text == "second":
            finished.set()
        return {"label": labels[0]["name"], "model_ms": 5}

    monkeypatch.setitem(app_module.CLASSIFIERS, model, classify)
    response = client.post(
        f"/api/run/{model}/expenses", json={"labels": LABELS}, buffered=False,
    )
    # Do not consume any more of the response: server-side calls must still finish.
    assert finished.wait(timeout=2)
    events = [json.loads(line) for line in response.data.splitlines()]
    assert calls == ["first", "second"]
    assert [event["type"] for event in events] == ["calling", "result", "calling", "result", "done"]
    assert events[0]["prompt"]
    assert events[1]["text"] == "first"
    assert events[1]["total"] == 2
    assert events[1]["wall_ms"] == pytest.approx(100)
    assert events[3]["wall_ms"] == pytest.approx(250)
    assert events[-1]["wall_ms"] == pytest.approx(300)


def test_batch_failure_is_logged_and_never_reported_as_done(monkeypatch, client, caplog):
    monkeypatch.setenv("MOCK_MODELS", "true")

    def fail(text, labels):
        raise RuntimeError("private upstream details")

    monkeypatch.setitem(app_module.CLASSIFIERS, "openai", fail)
    response = client.post("/api/run/openai/expenses", json={"labels": LABELS})
    events = [json.loads(line) for line in response.data.splitlines()]
    assert [event["type"] for event in events] == ["calling", "error"]
    assert "private upstream details" not in response.get_data(as_text=True)
    assert "Batch classification failed" in caplog.text


def test_batch_disconnect_stops_after_inflight_call(monkeypatch, client):
    monkeypatch.setenv("MOCK_MODELS", "true")
    monkeypatch.setattr(app_module, "load_data", lambda path: ["first", "second"])
    release = threading.Event()
    threads = []
    calls = []
    real_thread = threading.Thread

    def create_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    def classify(text, labels):
        calls.append(text)
        assert release.wait(timeout=2)
        return {"label": "Other", "model_ms": 5}

    monkeypatch.setattr(app_module.threading, "Thread", create_thread)
    monkeypatch.setitem(app_module.CLASSIFIERS, "openai", classify)
    response = client.post(
        "/api/run/openai/expenses", json={"labels": LABELS}, buffered=False,
    )
    try:
        response.close()
    finally:
        release.set()
        threads[0].join(timeout=2)
    assert not threads[0].is_alive()
    assert calls == ["first"]


def test_batch_admission_preserves_health_capacity_and_releases_slot(monkeypatch, client):
    monkeypatch.setenv("MOCK_MODELS", "true")
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(app_module, "_batch_slots", slots)
    monkeypatch.setattr(app_module, "load_data", lambda path: ["first"])
    entered = threading.Event()
    release = threading.Event()

    def classify(text, labels):
        entered.set()
        assert release.wait(timeout=2)
        return {"label": labels[0]["name"], "model_ms": 5}

    monkeypatch.setitem(app_module.CLASSIFIERS, "openai", classify)
    dockerfile = Path(app_module.__file__).with_name("Dockerfile").read_text()
    command = json.loads(next(line[4:] for line in dockerfile.splitlines() if line.startswith("CMD ")))
    assert app_module.MAX_CONCURRENT_BATCHES < int(command[command.index("--threads") + 1])
    response = client.post(
        "/api/run/openai/expenses", json={"labels": LABELS}, buffered=False,
    )
    assert json.loads(next(response.response))["type"] == "calling"
    assert entered.wait(timeout=2)

    rejected = client.post("/api/run/openai/expenses", json={"labels": LABELS})
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "5"
    rejected = client.post("/api/classify/openai", json={"text": "first", "labels": LABELS})
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "5"
    assert client.get("/healthz").status_code == 200

    response.close()
    assert not slots.acquire(blocking=False)
    release.set()
    assert slots.acquire(timeout=2)
    slots.release()

    accepted = client.post(
        "/api/run/openai/expenses", json={"labels": LABELS}, buffered=False,
    )
    events = [json.loads(line) for line in accepted.response]
    accepted.close()
    assert events[-1]["type"] == "done"
    assert slots.acquire(timeout=2)
    slots.release()


@pytest.mark.parametrize("fails", [False, True])
def test_classify_shares_capacity_and_releases_on_completion(monkeypatch, client, fails):
    monkeypatch.setenv("MOCK_MODELS", "true")
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(app_module, "_batch_slots", slots)
    calls = []

    def classify(text, labels):
        calls.append(text)
        assert not slots.acquire(blocking=False)
        with app.test_client() as other_client:
            for endpoint in ("/api/classify/decision", "/api/run/decision/expenses"):
                rejected = other_client.post(endpoint, json={"text": text, "labels": labels})
                assert rejected.status_code == 429
                assert rejected.headers["Retry-After"] == "5"
            assert other_client.get("/healthz").status_code == 200
        if fails:
            raise RuntimeError("upstream failure")
        return {"label": "Other"}

    monkeypatch.setitem(app_module.CLASSIFIERS, "openai", classify)
    response = client.post("/api/classify/openai", json={"text": "first", "labels": LABELS})
    assert response.status_code == (502 if fails else 200)
    assert calls == ["first"]
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("drain", [False, True])
def test_finished_producer_holds_slot_until_stream_finishes(monkeypatch, client, fails, drain):
    monkeypatch.setenv("MOCK_MODELS", "true")
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(app_module, "_batch_slots", slots)
    monkeypatch.setattr(app_module, "load_data", lambda path: ["first"])
    threads = []
    real_thread = threading.Thread

    def create_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread

    def classify(text, labels):
        if fails:
            raise RuntimeError("upstream failure")
        return {"label": "Other"}

    monkeypatch.setattr(app_module.threading, "Thread", create_thread)
    monkeypatch.setitem(app_module.CLASSIFIERS, "openai", classify)
    response = client.post("/api/run/openai/expenses", json={"labels": LABELS}, buffered=False)
    try:
        threads[0].join(timeout=2)
        assert not threads[0].is_alive()
        assert not slots.acquire(blocking=False)
        for endpoint in ("/api/run/openai/expenses", "/api/classify/openai"):
            rejected = client.post(endpoint, json={"text": "first", "labels": LABELS})
            assert rejected.status_code == 429
        assert client.get("/healthz").status_code == 200
        if drain:
            events = [json.loads(line) for line in response.response]
            assert events[-1]["type"] == ("error" if fails else "done")
            assert slots.acquire(blocking=False)
            slots.release()
    finally:
        response.close()
    response.close()
    assert slots.acquire(blocking=False)
    assert not slots.acquire(blocking=False)
    slots.release()


def test_batch_close_before_iteration_releases_capacity(monkeypatch):
    monkeypatch.setenv("MOCK_MODELS", "true")
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(app_module, "_batch_slots", slots)
    monkeypatch.setattr(app_module, "load_data", lambda path: [])
    monkeypatch.setattr(
        app_module.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target),
    )
    with app.test_request_context("/api/run/openai/expenses", method="POST", json={"labels": LABELS}):
        response = app.full_dispatch_request()
    assert not slots.acquire(blocking=False)
    response.close()
    response.close()
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("stage", ["create", "start"])
def test_batch_thread_failure_releases_capacity(monkeypatch, client, stage):
    monkeypatch.setenv("MOCK_MODELS", "true")
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(app_module, "_batch_slots", slots)

    def fail(*args, **kwargs):
        raise RuntimeError("cannot start thread")

    if stage == "create":
        monkeypatch.setattr(app_module.threading, "Thread", fail)
    else:
        monkeypatch.setattr(app_module.threading.Thread, "start", fail)
    with pytest.raises(RuntimeError, match="cannot start thread"):
        client.post("/api/run/openai/expenses", json={"labels": LABELS})
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("body", [[], {}, {"labels": "invalid"}, {"labels": []}, {"labels": [{}]}])
def test_batch_validates_before_starting_stream(client, body):
    assert client.post("/api/run/openai/expenses", json=body).status_code == 400


def test_batch_checks_model_scenario_and_configuration(monkeypatch, client):
    assert client.post("/api/run/other/expenses", json={"labels": LABELS}).status_code == 404
    assert client.post("/api/run/openai/other", json={"labels": LABELS}).status_code == 404
    monkeypatch.delenv("MOCK_MODELS", raising=False)
    monkeypatch.delenv("DECISION_ENDPOINT", raising=False)
    response = client.post("/api/run/decision/expenses", json={"labels": LABELS})
    assert response.status_code == 500
    assert "DECISION_ENDPOINT" in response.get_json()["error"]
