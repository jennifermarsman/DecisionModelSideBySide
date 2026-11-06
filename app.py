"""Side-by-side classification demo: Azure OpenAI vs. Microsoft-Decision-1."""

import json
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

from classifiers import (  # noqa: E402
    CLASSIFIERS,
    build_decision_request,
    build_openai_messages,
    format_openai_prompt,
    missing_settings,
    mock_enabled,
)
from scenarios import SCENARIOS, load_data  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def config():
    return jsonify(
        {
            "mock": mock_enabled(),
            "openai_model": os.getenv("AZURE_OPENAI_DEPLOYMENT", "") or "Azure OpenAI",
            "decision_model": os.getenv("DECISION_DEPLOYMENT", "") or "Microsoft-Decision-1",
            "scenarios": {
                key: {"name": s["name"], "description": s["description"], "labels": s["labels"]}
                for key, s in SCENARIOS.items()
            },
        }
    )


def normalize_labels(labels):
    return [
        {"name": str(l.get("name", "")).strip(), "description": str(l.get("description", "")).strip()}
        for l in labels or []
        if isinstance(l, dict) and str(l.get("name", "")).strip()
    ]


@app.route("/api/data/<scenario>", methods=["GET", "POST"])
def data(scenario):
    if scenario not in SCENARIOS:
        return jsonify({"error": "Unknown scenario"}), 404
    items = load_data(SCENARIOS[scenario]["data_file"])
    if request.method == "GET":
        return jsonify({"items": items})
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "The request body must be a JSON object."}), 400
    if not isinstance(body.get("labels", []), list):
        return jsonify({"error": "'labels' must be an array."}), 400
    labels = normalize_labels(body.get("labels"))
    if not labels:
        return jsonify({"error": "At least one label is required."}), 400
    prompts = [
        {
            "openai": format_openai_prompt(build_openai_messages(text, labels)),
            "decision": json.dumps(build_decision_request(text, labels)[0], indent=2),
        }
        for text in items
    ]
    return jsonify({"items": items, "prompts": prompts})


@app.post("/api/classify/<model>")
def classify(model):
    if model not in CLASSIFIERS:
        return jsonify({"error": "Unknown model"}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "The request body must be a JSON object."}), 400
    if not isinstance(body.get("labels", []), list):
        return jsonify({"error": "'labels' must be an array."}), 400
    text = str(body.get("text", "")).strip()
    labels = normalize_labels(body.get("labels"))
    if not text or not labels:
        return jsonify({"error": "Both 'text' and at least one label are required."}), 400
    missing = missing_settings(model)
    if missing:
        return jsonify({"error": f"Missing configuration: set {', '.join(missing)} in your .env file."}), 500
    try:
        return jsonify(CLASSIFIERS[model](text, labels))
    except Exception as exc:  # details go to the server log, not the browser
        app.logger.exception("Classification failed")
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        detail = f" (HTTP {int(status)})" if isinstance(status, int) else ""
        return jsonify({"error": f"Model call failed{detail}. See the server console for details."}), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), threaded=True)
