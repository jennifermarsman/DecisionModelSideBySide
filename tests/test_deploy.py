import json
import sys

import pytest

from scripts import deploy
from scripts.deploy import configuration


def test_container_configuration_uses_managed_identity_and_ignores_old_keys():
    config = configuration(
        "centralus", "/environment", "/identity", "registry.azurecr.io",
        "registry.azurecr.io/demo:123",
        {
            "AZURE_OPENAI_API_KEY": "openai-secret",
            "DECISION_API_KEY": "decision-secret",
            "AZURE_CLIENT_ID": "app-client-id",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "DECISION_USD_PER_MILLION": "0.042",
            "MOCK_MODELS": "true",
            "UNRELATED_SECRET": "not-for-upload",
        },
    )
    properties = config["properties"]
    hosting = properties["configuration"]
    container = properties["template"]["containers"][0]
    env = {setting["name"]: setting for setting in container["env"]}
    assert "AZURE_OPENAI_API_KEY" not in env
    assert "DECISION_API_KEY" not in env
    assert hosting["secrets"] == []
    assert env["AZURE_CLIENT_ID"]["value"] == "app-client-id"
    assert env["DECISION_USD_PER_MILLION"]["value"] == "0.042"
    assert env["MOCK_MODELS"]["value"] == "false"
    assert "UNRELATED_SECRET" not in env
    assert hosting["registries"] == [{"server": "registry.azurecr.io", "identity": "/identity"}]
    assert hosting["ingress"]["allowInsecure"] is False
    assert properties["template"]["scale"] == {"minReplicas": 1, "maxReplicas": 1}


def test_model_settings_checks_both_deployments_without_reading_keys(monkeypatch):
    calls = []

    def fake_az(*args):
        calls.append(args)
        if args[2] == "show":
            return {
                "id": "/foundry", "kind": "AIServices",
                "properties": {"customSubDomainName": "restored", "disableLocalAuth": True},
            }
        assert args[2:4] == ("deployment", "show")
        return {}

    monkeypatch.setattr(deploy, "az", fake_az)
    settings, scope = deploy.model_settings("models", "account", "chat", "decision")
    assert scope == "/foundry"
    assert settings == {
        "AZURE_OPENAI_ENDPOINT": "https://restored.openai.azure.com/",
        "AZURE_OPENAI_DEPLOYMENT": "chat",
        "DECISION_ENDPOINT": "https://restored.services.ai.azure.com",
        "DECISION_DEPLOYMENT": "decision",
    }
    checked = [call[-1] for call in calls if call[2:4] == ("deployment", "show")]
    assert checked == ["chat", "decision"]


def test_model_account_arguments_must_be_paired(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "deploy.py", "--env-file", str(tmp_path / "missing.env"), "--model-account", "account",
    ])
    with pytest.raises(SystemExit, match="2"):
        deploy.main()


@pytest.mark.parametrize("old_secrets", [None, [], [{"name": "azure-openai-api-key"}, {"name": "decision-api-key"}]])
@pytest.mark.parametrize("auth_mode", ["entra", "api-key"])
def test_deploy_assigns_model_roles_and_manages_secrets(monkeypatch, tmp_path, capsys, old_secrets, auth_mode):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AZURE_CLIENT_ID=wrong-local-identity\nAZURE_OPENAI_API_KEY=obsolete\nDECISION_API_KEY=obsolete\n"
    )
    monkeypatch.setattr(sys, "argv", [
        "deploy.py", "--env-file", str(env_file),
        "--model-resource-group", "models", "--model-account", "restored",
        "--auth-mode", auth_mode,
    ])
    monkeypatch.setattr(deploy, "model_settings", lambda *args: ({
        "AZURE_OPENAI_ENDPOINT": "https://restored.openai.azure.com/",
        "AZURE_OPENAI_DEPLOYMENT": "chat",
        "DECISION_ENDPOINT": "https://restored.services.ai.azure.com",
        "DECISION_DEPLOYMENT": "decision",
    }, "/foundry"))
    calls = []

    def fake_az(*args, output=True):
        calls.append(args)
        if args[:2] == ("account", "show"):
            return {"id": "subscription"}
        if args[:2] == ("group", "list"):
            return []
        if args[:3] == ("deployment", "group", "create"):
            assert args[args.index("--template-file") + 1].endswith("infra/hosting.bicep")
            return {"properties": {"outputs": {
                "registry": {"value": {"id": "/registry", "loginServer": "demo.azurecr.io"}},
                "identity": {"value": {
                    "id": "/identity", "principalId": "principal", "clientId": "app-client-id",
                }},
                "environmentId": {"value": "/environment"},
            }}}
        if args[:3] == ("role", "assignment", "list"):
            return []
        if args[:2] == ("acr", "build"):
            context = deploy.Path(args[-1])
            assert (context / "Dockerfile").exists()
            assert not (context / ".env").exists()
            assert not (context / ".git").exists()
            assert not (context / "infra").exists()
        if args[:2] == ("containerapp", "create"):
            config_path = deploy.Path(args[args.index("--yaml") + 1])
            assert config_path.stat().st_mode & 0o777 == 0o600
            config = json.loads(config_path.read_text())
            assert config["properties"]["managedEnvironmentId"] == "/environment"
            assert config["properties"]["template"]["containers"][0]["image"].startswith(
                "demo.azurecr.io/demo:"
            )
            variables = config["properties"]["template"]["containers"][0]["env"]
            env = {v["name"]: v.get("value") for v in variables}
            assert env["MODEL_AUTH_MODE"] == auth_mode
            if auth_mode == "entra":
                assert env["AZURE_CLIENT_ID"] == "app-client-id"
                assert not any(name.endswith("_API_KEY") for name in env)
                assert config["properties"]["configuration"]["secrets"] == []
            else:
                assert "AZURE_CLIENT_ID" not in env
                assert {v["name"] for v in variables if v.get("secretRef")} == {
                    "AZURE_OPENAI_API_KEY", "DECISION_API_KEY",
                }
        if args[:2] == ("containerapp", "show"):
            return {"properties": {"latestRevisionName": "demo--new", "configuration": {
                "ingress": {"fqdn": "demo.example"}, "secrets": old_secrets,
            }}}
        if args[:3] == ("containerapp", "revision", "list"):
            return [{
                "name": "demo--new",
                "properties": {"active": True, "healthState": "Healthy", "template": {}},
            }]
        return None

    class HealthResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"status": "ok"}'

    monkeypatch.setattr(deploy, "az", fake_az)
    monkeypatch.setattr(deploy.urllib.request, "urlopen", lambda *args, **kwargs: HealthResponse())
    deploy.main()
    assert "Demo ready: https://demo.example" in capsys.readouterr().out
    grants = {
        (call[call.index("--scope") + 1], call[call.index("--role") + 1])
        for call in calls if call[:3] == ("role", "assignment", "create")
    }
    expected = {("/registry", "AcrPull")}
    if auth_mode == "entra":
        expected.update({
            ("/foundry", "Cognitive Services OpenAI User"),
            ("/foundry", "Cognitive Services User"),
        })
    assert grants == expected
    removals = [call for call in calls if call[:3] == ("containerapp", "secret", "remove")]
    assert bool(removals) == (bool(old_secrets) and auth_mode == "entra")
    if removals:
        assert removals[0][-2:] == ("azure-openai-api-key", "decision-api-key")
        assert calls.index(removals[0]) > next(
            index for index, call in enumerate(calls)
            if call[:3] == ("containerapp", "revision", "list")
        )


@pytest.mark.parametrize("pending", [
    "missing", "unhealthy", "inactive", "old-active", "new-reference", "scale-reference",
])
def test_secret_cleanup_waits_for_healthy_replacement_and_unused_secrets(monkeypatch, pending):
    secret_names = ["azure-openai-api-key", "decision-api-key"]
    secret_template = {"containers": [{"env": [{"secretRef": secret_names[0]}]}]}
    ready = {
        "name": "demo--new",
        "properties": {"active": True, "healthState": "Healthy", "template": {}},
    }
    old = {
        "name": "demo--old",
        "properties": {"active": False, "healthState": "Healthy", "template": secret_template},
    }
    waiting = json.loads(json.dumps([ready, old]))
    if pending == "missing":
        waiting = [old]
    elif pending == "unhealthy":
        waiting[0]["properties"]["healthState"] = "Unhealthy"
    elif pending == "inactive":
        waiting[0]["properties"]["active"] = False
    elif pending == "old-active":
        waiting[1]["properties"]["active"] = True
    elif pending == "new-reference":
        waiting[0]["properties"]["template"] = secret_template
    else:
        waiting[0]["properties"]["template"] = {
            "scale": {"rules": [{"custom": {"auth": [{"secretRef": secret_names[1]}]}}]},
        }
    # Unrelated secrets and inactive revisions must not block cleanup.
    ready["properties"]["template"] = {
        "containers": [{"env": [{"secretRef": "unrelated-secret"}]}],
    }
    snapshots = iter([waiting, [ready, old]])
    calls = []
    sleeps = []

    def fake_az(*args):
        calls.append(args)
        assert args == ("containerapp", "revision", "list", "-g", "group", "-n", "demo", "--all")
        return next(snapshots)

    monkeypatch.setattr(deploy, "az", fake_az)
    monkeypatch.setattr(deploy.time, "sleep", sleeps.append)
    deploy.wait_for_secret_cleanup("group", "demo", "demo--new", secret_names)
    assert len(calls) == 2
    assert sleeps == [5]


@pytest.mark.parametrize("health,template", [
    ("Unhealthy", {}),
    ("Healthy", {"initContainers": [{"env": [{"secretRef": "decision-api-key"}]}]}),
])
def test_secret_cleanup_times_out_without_deleting_secrets(monkeypatch, health, template):
    calls = []
    sleeps = []

    def fake_az(*args):
        calls.append(args)
        assert args[:3] == ("containerapp", "revision", "list")
        return [{
            "name": "demo--new",
            "properties": {"active": True, "healthState": health, "template": template},
        }]

    monkeypatch.setattr(deploy, "az", fake_az)
    monkeypatch.setattr(deploy.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="secrets were retained"):
        deploy.wait_for_secret_cleanup("group", "demo", "demo--new", ["decision-api-key"])
    assert len(calls) == len(sleeps) == 30


def test_existing_role_assignments_are_reused(monkeypatch):
    calls = []

    def fake_az(*args, **kwargs):
        calls.append(args)
        return [{"principalId": "principal"}]

    monkeypatch.setattr(deploy, "az", fake_az)
    deploy.ensure_role_assignment("/foundry", "Cognitive Services User", "principal")
    assert len(calls) == 1
    assert calls[0][:3] == ("role", "assignment", "list")


@pytest.mark.parametrize("disabled", [False, True])
def test_model_keys_are_read_only_in_explicit_key_mode(monkeypatch, disabled):
    calls = []

    def fake_az(*args):
        calls.append(args)
        if args[2] == "show":
            return {
                "id": "/foundry", "kind": "AIServices",
                "properties": {"customSubDomainName": "restored", "disableLocalAuth": disabled},
            }
        if args[2:4] == ("keys", "list"):
            return {"key1": "test-key"}
        return {}

    monkeypatch.setattr(deploy, "az", fake_az)
    if disabled:
        with pytest.raises(ValueError, match="disables API-key authentication"):
            deploy.model_settings("models", "account", "chat", "decision", "api-key")
        assert not any(call[2:4] == ("keys", "list") for call in calls)
    else:
        settings, _ = deploy.model_settings("models", "account", "chat", "decision", "api-key")
        assert settings["AZURE_OPENAI_API_KEY"] == settings["DECISION_API_KEY"] == "test-key"


def test_api_key_mode_passes_keys_only_as_secret_references():
    config = configuration(
        "centralus", "/environment", "/identity", "registry.azurecr.io", "image",
        {"MODEL_AUTH_MODE": "api-key", "AZURE_OPENAI_API_KEY": "chat-key", "DECISION_API_KEY": "decision-key"},
    )
    properties = config["properties"]
    env = {v["name"]: v for v in properties["template"]["containers"][0]["env"]}
    assert env["AZURE_OPENAI_API_KEY"] == {
        "name": "AZURE_OPENAI_API_KEY", "secretRef": "azure-openai-api-key",
    }
    assert env["DECISION_API_KEY"]["secretRef"] == "decision-api-key"
    assert properties["configuration"]["secrets"] == [
        {"name": "azure-openai-api-key", "value": "chat-key"},
        {"name": "decision-api-key", "value": "decision-key"},
    ]
