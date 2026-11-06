"""Classifiers for Azure OpenAI and Microsoft-Decision-1.

Each classifier returns a dict with the chosen label, the request that was sent
("prompt"), and the time spent inside the model call (model_ms).
"""

import functools
import json
import os
import random
import re
import time

import requests

DECISION_PATH = "/providers/microsoft/v1/systemone"
QUESTION_NAME = "category"

# Reuse connections across calls, like the OpenAI client does.
_session = requests.Session()


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _require(*names):
    missing = [n for n in names if not _env(n)]
    if missing:
        raise RuntimeError(f"Missing configuration: set {', '.join(missing)} in your .env file.")


def mock_enabled():
    return _env("MOCK_MODELS").lower() in ("1", "true", "yes")


def build_openai_messages(text, labels):
    label_lines = "\n".join(f"- {l['name']}: {l.get('description') or l['name']}" for l in labels)
    system = (
        "You are a classifier. Classify the user's text into exactly one of the following labels.\n"
        f"{label_lines}\n\n"
        "Respond with only the label name, exactly as written above, and nothing else."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]


def match_label(answer, labels):
    """Map a free-form model answer back to one of the label names."""
    answer = (answer or "").strip().strip("\"'.`").strip()
    names = [l["name"] for l in labels]
    for name in names:
        if answer.lower() == name.lower():
            return name
    for name in sorted(names, key=len, reverse=True):
        if name.lower() in answer.lower():
            return name
    return answer or "(no answer)"


def label_keys(labels):
    """Build unique, identifier-style keys for Decision 'choice' criteria, mapped to label names."""
    keys = {}
    for l in labels:
        base = re.sub(r"[^a-z0-9]+", "_", l["name"].lower()).strip("_") or "label"
        key, n = base, 2
        while key in keys:
            key, n = f"{base}_{n}", n + 1
        keys[key] = l["name"]
    return keys


def build_decision_request(text, labels):
    keys = label_keys(labels)
    descriptions = {l["name"]: l.get("description") or l["name"] for l in labels}
    return {
        "model": _env("DECISION_DEPLOYMENT"),
        "state": text,
        "questions": {
            QUESTION_NAME: {
                "type": "choice",
                "instructions": "Classify the text into the single best matching category.",
                "criteria": {k: descriptions[name] for k, name in keys.items()},
            }
        },
    }, keys


def _mock(text, labels):
    time.sleep(random.uniform(0.2, 0.6))
    words = set(re.findall(r"[a-z]+", text.lower()))
    best = max(labels, key=lambda l: len(words & set(re.findall(r"[a-z]+", (l["name"] + " " + l.get("description", "")).lower()))))
    return best["name"]


@functools.lru_cache(maxsize=1)
def _openai_client():
    from openai import AzureOpenAI

    _require("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")

    return AzureOpenAI(
        azure_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_env("AZURE_OPENAI_API_KEY"),
        api_version=_env("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


def classify_openai(text, labels):
    messages = build_openai_messages(text, labels)
    prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
    client = None if mock_enabled() else _openai_client()
    start = time.perf_counter()
    if client is None:
        label = _mock(text, labels)
    else:
        resp = client.chat.completions.create(
            model=_env("AZURE_OPENAI_DEPLOYMENT"),
            messages=messages,
            temperature=0,
        )
        label = match_label(resp.choices[0].message.content, labels)
    model_ms = (time.perf_counter() - start) * 1000
    return {"label": label, "prompt": prompt, "model_ms": model_ms}


def parse_decision_response(data, keys):
    answer = (data.get("answers") or {}).get(QUESTION_NAME) or {}
    choice = answer.get("choice")
    return keys.get(choice, choice or "(no answer)"), answer.get("confidence")


def classify_decision(text, labels):
    body, keys = build_decision_request(text, labels)
    prompt = json.dumps(body, indent=2)
    confidence = None
    start = time.perf_counter()
    if mock_enabled():
        label = _mock(text, labels)
    else:
        _require("DECISION_ENDPOINT", "DECISION_API_KEY", "DECISION_DEPLOYMENT")
        url = _env("DECISION_ENDPOINT").rstrip("/") + DECISION_PATH
        resp = _session.post(
            url,
            headers={"api-key": _env("DECISION_API_KEY"), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        label, confidence = parse_decision_response(resp.json(), keys)
    model_ms = (time.perf_counter() - start) * 1000
    return {"label": label, "prompt": prompt, "model_ms": model_ms, "confidence": confidence}


CLASSIFIERS = {"openai": classify_openai, "decision": classify_decision}
