import json
import sys

import pytest

from scripts import deploy
from scripts.deploy import configuration


def test_container_configuration_uses_secrets_and_warm_https_hosting():
    config = configuration(
        "centralus", "/environment", "/identity", "registry.azurecr.io",
        "registry.azurecr.io/demo:123",
        {
            "AZURE_OPENAI_API_KEY": "openai-secret",
            "DECISION_API_KEY": "decision-secret",
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
    assert env["AZURE_OPENAI_API_KEY"] == {
        "name": "AZURE_OPENAI_API_KEY", "secretRef": "azure-openai-api-key",
    }
    assert env["DECISION_API_KEY"]["secretRef"] == "decision-api-key"
    assert hosting["secrets"] == [
        {"name": "azure-openai-api-key", "value": "openai-secret"},
        {"name": "decision-api-key", "value": "decision-secret"},
    ]
    assert env["DECISION_USD_PER_MILLION"]["value"] == "0.042"
    assert env["MOCK_MODELS"]["value"] == "false"
    assert "UNRELATED_SECRET" not in env
    assert hosting["registries"] == [{"server": "registry.azurecr.io", "identity": "/identity"}]
    assert hosting["ingress"]["allowInsecure"] is False
    assert properties["template"]["scale"] == {"minReplicas": 1, "maxReplicas": 1}


def test_model_settings_reads_current_key_and_checks_both_deployments(monkeypatch):
    calls = []

    def fake_az(*args):
        calls.append(args)
        if args[2:4] == ("keys", "list"):
            return {"key1": "replacement-key"}
        if args[2] == "show":
            return {"kind": "AIServices", "properties": {"customSubDomainName": "restored"}}
        return {}

    monkeypatch.setattr(deploy, "az", fake_az)
    settings = deploy.model_settings("models", "account", "chat", "decision")
    assert settings == {
        "AZURE_OPENAI_ENDPOINT": "https://restored.openai.azure.com/",
        "AZURE_OPENAI_API_KEY": "replacement-key",
        "AZURE_OPENAI_DEPLOYMENT": "chat",
        "DECISION_ENDPOINT": "https://restored.services.ai.azure.com",
        "DECISION_API_KEY": "replacement-key",
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


def test_deploy_uses_bicep_outputs_and_stages_only_runtime_files(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", [
        "deploy.py", "--env-file", str(tmp_path / "missing.env"),
        "--model-resource-group", "models", "--model-account", "restored",
    ])
    monkeypatch.setattr(deploy, "model_settings", lambda *args: {
        "AZURE_OPENAI_ENDPOINT": "https://restored.openai.azure.com/",
        "AZURE_OPENAI_API_KEY": "test-key",
        "AZURE_OPENAI_DEPLOYMENT": "chat",
        "DECISION_ENDPOINT": "https://restored.services.ai.azure.com",
        "DECISION_API_KEY": "test-key",
        "DECISION_DEPLOYMENT": "decision",
    })
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
                "identity": {"value": {"id": "/identity", "principalId": "principal"}},
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
        if args[:2] == ("containerapp", "show"):
            return {"properties": {"configuration": {"ingress": {"fqdn": "demo.example"}}}}
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
    assert any(call[:3] == ("role", "assignment", "create") for call in calls)
