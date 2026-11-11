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
