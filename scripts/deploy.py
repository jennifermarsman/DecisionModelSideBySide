"""Build in Azure and deploy the public demo. Run with the project's virtualenv."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = (
    "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION", "DECISION_ENDPOINT", "DECISION_API_KEY",
    "DECISION_DEPLOYMENT", "AZURE_OPENAI_INPUT_USD_PER_MILLION",
    "AZURE_OPENAI_OUTPUT_USD_PER_MILLION", "AZURE_OPENAI_CACHED_INPUT_USD_PER_MILLION",
    "AZURE_OPENAI_CACHE_WRITE_USD_PER_MILLION", "DECISION_USD_PER_MILLION",
)


def az(*args, output=True):
    command = ["az", *args, "--only-show-errors", "-o", "json" if output else "none"]
    result = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout) if output and result.stdout.strip() else None


def model_settings(resource_group, account_name, llm_deployment, decision_deployment):
    account = az("cognitiveservices", "account", "show", "-g", resource_group, "-n", account_name)
    if account["kind"] != "AIServices":
        raise ValueError("Both models must be deployed in an AIServices Foundry account")
    if account["properties"].get("publicNetworkAccess") == "Disabled":
        raise ValueError("This hosting setup requires a model account with public network access enabled")
    domain = account["properties"]["customSubDomainName"]
    for deployment in (llm_deployment, decision_deployment):
        az(
            "cognitiveservices", "account", "deployment", "show",
            "-g", resource_group, "-n", account_name, "--deployment-name", deployment,
        )
    key = az(
        "cognitiveservices", "account", "keys", "list",
        "-g", resource_group, "-n", account_name,
    )["key1"]
    return {
        "AZURE_OPENAI_ENDPOINT": f"https://{domain}.openai.azure.com/",
        "AZURE_OPENAI_API_KEY": key,
        "AZURE_OPENAI_DEPLOYMENT": llm_deployment,
        "DECISION_ENDPOINT": f"https://{domain}.services.ai.azure.com",
        "DECISION_API_KEY": key,
        "DECISION_DEPLOYMENT": decision_deployment,
    }


def configuration(location, environment, identity, registry, image, settings):
    secrets = []
    variables = [{"name": "MOCK_MODELS", "value": "false"}]
    for name in SETTINGS:
        value = settings.get(name)
        if not value:
            continue
        if name.endswith("_API_KEY"):
            secret_name = name.lower().replace("_", "-")
            secrets.append({"name": secret_name, "value": value})
            variables.append({"name": name, "secretRef": secret_name})
        else:
            variables.append({"name": name, "value": value})
    return {
        "location": location,
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {identity: {}}},
        "properties": {
            "managedEnvironmentId": environment,
            "configuration": {
                "activeRevisionsMode": "Single",
                "secrets": secrets,
                "ingress": {
                    "external": True, "targetPort": 8000, "transport": "auto",
                    "allowInsecure": False,
                },
                "registries": [{"server": registry, "identity": identity}],
            },
            "template": {
                "containers": [{
                    "name": "demo", "image": image, "env": variables,
                    "resources": {"cpu": 1.0, "memory": "2Gi"},
                    "probes": [
                        {
                            "type": kind, "httpGet": {"path": "/healthz", "port": 8000},
                            "initialDelaySeconds": 10, "periodSeconds": 10,
                            "timeoutSeconds": 3, "failureThreshold": 6,
                        }
                        for kind in ("Liveness", "Readiness")
                    ],
                }],
                "scale": {"minReplicas": 1, "maxReplicas": 1},
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", default="centralus")
    parser.add_argument("--resource-group", default="rg-decision-sidebyside-centralus")
    parser.add_argument("--name", default="decision-sidebyside")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--model-resource-group")
    parser.add_argument("--model-account")
    parser.add_argument("--llm-deployment", default="gpt-6.1-sol")
    parser.add_argument("--decision-deployment", default="decision-1.1")
    args = parser.parse_args()
    settings = dotenv_values(args.env_file)
    if bool(args.model_resource_group) != bool(args.model_account):
        parser.error("--model-resource-group and --model-account must be supplied together")
    if args.model_account:
        settings.update(model_settings(
            args.model_resource_group, args.model_account,
            args.llm_deployment, args.decision_deployment,
        ))
    required = (
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT",
        "DECISION_ENDPOINT", "DECISION_API_KEY", "DECISION_DEPLOYMENT",
    )
    missing = [key for key in required if not settings.get(key)]
    if missing:
        parser.error("Configure these settings before deploying: " + ", ".join(missing))
    subscription = az("account", "show")["id"]
    suffix = hashlib.sha256(f"{subscription}/{args.resource_group}".encode()).hexdigest()[:10]
    registry_name = f"decisionsbs{suffix}"
    group = args.resource_group
    print(f"Deploying {args.name} in {args.location} ({group})", flush=True)
    groups = az("group", "list", "--query", f"[?name=='{group}'].name")
    if not groups:
        az("group", "create", "-n", group, "-l", args.location, output=False)
    infrastructure = az(
        "deployment", "group", "create", "-g", group, "-n", f"{args.name}-hosting",
        "--template-file", str(ROOT / "infra" / "hosting.bicep"),
        "--parameters", f"location={args.location}", f"appName={args.name}",
        f"registryName={registry_name}",
    )
    outputs = infrastructure["properties"]["outputs"]
    registry = outputs["registry"]["value"]
    identity = outputs["identity"]["value"]
    environment_id = outputs["environmentId"]["value"]
    assignments = az(
        "role", "assignment", "list", "--scope", registry["id"], "--role", "AcrPull",
        "--fill-principal-name", "false",
        "--query", f"[?principalId=='{identity['principalId']}']",
    )
    if not assignments:
        az(
            "role", "assignment", "create", "--assignee-object-id", identity["principalId"],
            "--assignee-principal-type", "ServicePrincipal", "--role", "AcrPull",
            "--scope", registry["id"], output=False,
        )
    tag = f"demo:{time.time_ns()}"
    print("Building the container in Azure (no local Docker required)...", flush=True)
    # Stage only runtime files: .env, Git history, and local caches never leave the machine.
    with tempfile.TemporaryDirectory(prefix="decision-build-") as directory:
        context = Path(directory)
        for name in (
            "Dockerfile", ".dockerignore", "requirements.txt", "app.py",
            "classifiers.py", "pricing.py", "scenarios.py",
        ):
            shutil.copy2(ROOT / name, context / name)
        for name in ("static", "data"):
            shutil.copytree(ROOT / name, context / name)
        az("acr", "build", "-r", registry_name, "-t", tag, str(context), output=False)
    config = configuration(
        args.location, environment_id, identity["id"], registry["loginServer"],
        f"{registry['loginServer']}/{tag}", settings,
    )
    # Secrets go via a mode-0600 temporary file, not CLI arguments or build context.
    with tempfile.TemporaryDirectory(prefix="decision-config-") as directory:
        config_path = Path(directory) / "config.json"
        with config_path.open("w") as handle:
            config_path.chmod(0o600)
            json.dump(config, handle)
        az("containerapp", "create", "-g", group, "-n", args.name, "--yaml", str(config_path), output=False)
    deployed = az("containerapp", "show", "-g", group, "-n", args.name)
    url = "https://" + deployed["properties"]["configuration"]["ingress"]["fqdn"]
    last_error = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=10) as response:
                if json.load(response) != {"status": "ok"}:
                    raise RuntimeError("Unexpected health response")
            print(f"Demo ready: {url}", flush=True)
            print(f"Resource group: {group}", flush=True)
            return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(5)
    raise RuntimeError(f"Deployment did not become healthy: {url}") from last_error


if __name__ == "__main__":
    main()
