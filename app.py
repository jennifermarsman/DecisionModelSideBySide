"""Side-by-side classification demo: Azure OpenAI vs. Microsoft-Decision-1."""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

from classifiers import CLASSIFIERS, mock_enabled  # noqa: E402
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


@app.get("/api/data/<scenario>")
def data(scenario):
    if scenario not in SCENARIOS:
        return jsonify({"error": "Unknown scenario"}), 404
    return jsonify({"items": load_data(SCENARIOS[scenario]["data_file"])})


@app.post("/api/classify/<model>")
def classify(model):
    if model not in CLASSIFIERS:
        return jsonify({"error": "Unknown model"}), 404
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    labels = [
        {"name": str(l.get("name", "")).strip(), "description": str(l.get("description", "")).strip()}
        for l in body.get("labels") or []
        if isinstance(l, dict) and str(l.get("name", "")).strip()
    ]
    if not text or not labels:
        return jsonify({"error": "Both 'text' and at least one label are required."}), 400
    try:
        return jsonify(CLASSIFIERS[model](text, labels))
    except Exception as exc:  # surface model/config errors in the UI
        app.logger.exception("Classification failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), threaded=True)
