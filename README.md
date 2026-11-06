# DecisionModelSideBySide
Side-by-side comparison of Microsoft Decision vs. a traditional LLM.

A small web app that classifies the same sample data **in parallel** with:

- **Left:** an Azure OpenAI chat model (prompted to answer with a single label)
- **Right:** [Microsoft-Decision-1](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) in Microsoft Foundry (a typed `choice` decision)

For each side the UI shows the prompt/request being executed, the **wall clock time**, the **number of model calls**, the total **model time**, and the **average time per model call** (all in seconds), plus a scrolling list of each item and the label it was assigned. When both finish, each panel shows how much faster or slower it was.

## Scenarios

Switch between two built-in scenarios using the **Scenario** dropdown. Each has its own default labels (defined in [`scenarios.py`](scenarios.py)) and its own sample data file:

| Scenario | Data file | Default labels |
| --- | --- | --- |
| Customer questions & complaints | [`data/customer/data.config`](data/customer/data.config) | Sales - New Purchase, Sales - Product Comparison, Billing & Licensing, Technical Support, Account & Access, Feedback & Complaints, Other |
| Expense report classification | [`data/expenses/data.config`](data/expenses/data.config) | Lodging, Airfare, Ground Transportation, Car Rental, Meals & Entertainment, Office Supplies & Equipment, Software & Subscriptions, Conferences & Training, Communications, Other |

Labels can be added and removed in the UI before a run; **Reset to defaults** (or switching scenarios) restores the defaults.
`data.config` files contain one item per line; blank lines and lines starting with `#` are ignored, so you can edit them freely.

## Setup

Prerequisites: Python 3.10+, an Azure OpenAI chat deployment, and a Microsoft-Decision-1 deployment in a Microsoft Foundry resource.

The Decision side sends one typed `choice` question per item, as described in [Deploy and use Microsoft-Decision-1](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision):

```json
POST <DECISION_ENDPOINT>/providers/microsoft/v1/systemone
{
  "model": "<DECISION_DEPLOYMENT>",
  "state": "<item text>",
  "questions": {
    "category": {
      "type": "choice",
      "instructions": "Classify the text into the single best matching category.",
      "criteria": { "<label_key>": "<label description>", "...": "..." }
    }
  }
}
```

The response's `answers.category.choice` is mapped back to the label name.

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy the sample settings and fill in your values:

   ```bash
   cp .env.example .env             # Windows: copy .env.example .env
   ```

   | Setting | Description |
   | --- | --- |
   | `AZURE_OPENAI_ENDPOINT` | Your Azure OpenAI endpoint, e.g. `https://<resource>.openai.azure.com/` |
   | `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
   | `AZURE_OPENAI_DEPLOYMENT` | Name of your chat model deployment (e.g. `gpt-4o-mini`) |
   | `AZURE_OPENAI_API_VERSION` | API version (default `2024-10-21`) |
   | `DECISION_ENDPOINT` | Your Foundry resource endpoint; the app calls `<endpoint>/providers/microsoft/v1/systemone` |
   | `DECISION_API_KEY` | Foundry resource API key. Optional: leave empty to use Microsoft Entra ID instead (e.g. run `az login`; your identity needs a role such as *Cognitive Services User* on the resource) |
   | `DECISION_DEPLOYMENT` | Name of your Microsoft-Decision-1 deployment (the deployment name, not the model name) |
   | `MOCK_MODELS` | Set to `true` to try the UI without any Azure resources (simulated classifiers and latency) |
   | `PORT` | Port to listen on (default `5000`) |

3. Run the app and open <http://127.0.0.1:5000>:

   ```bash
   python app.py
   ```

4. Pick a scenario, adjust the labels if you like, and press **Run**.

## How timing is measured

- **Wall clock**: measured in the browser from the start of a side's run until its last result arrives (includes network and app overhead).
- **Model calls**: one call per data item.
- **Model time**: the sum of the time spent inside each model API call, measured on the server.
- **Avg per call**: model time ÷ model calls.

Both sides run at the same time, and each side processes its items one call at a time.

## Tests

```bash
python -m pytest
```
