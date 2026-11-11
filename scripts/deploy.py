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
    args = parser.parse_args()
    settings = dotenv_values(args.env_file)
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
    registry = az(
        "acr", "create", "-g", group, "-n", registry_name,
        "--sku", "Basic", "--admin-enabled", "false",
    )
    identity = az("identity", "create", "-g", group, "-n", f"{args.name}-pull")
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
    environment = az(
        "containerapp", "env", "create", "-g", group, "-n", f"{args.name}-env",
        "-l", args.location, "--logs-destination", "none",
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
        args.location, environment["id"], identity["id"], registry["loginServer"],
        f"{registry['loginServer']}/{tag}", settings,
    )
    # Secrets go via a mode-0600 temporary file, not CLI arguments or build context.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
        json.dump(config, handle)
        handle.flush()
        az("containerapp", "create", "-g", group, "-n", args.name, "--yaml", handle.name, output=False)
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
