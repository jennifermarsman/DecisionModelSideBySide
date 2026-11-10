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

## How cost is estimated

Each panel totals API-reported tokens and shows an estimated USD cost for completed
responses. Once both sides complete, the app compares their total costs and speeds.

The snapshot in `pricing.py` was verified on **November 6, 2026** against
[Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/)
and the [Azure Retail Prices API](https://prices.azure.com/api/retail/prices).
Rates are USD per **1 million tokens**:

| Model / pricing assumption | Input | Cached input | Cache writes | Output |
| --- | --- | --- | --- | --- |
| GPT-6.1 Sol, Global Standard, short context | $2.00 | $0.10 | $2.50 | $10.00 |
| Microsoft-Decision-1, standard token meter | $0.042 | N/A | N/A | $0.042 |

LLM cached-input and cache-write tokens are removed from ordinary input before
their separate rates are applied. Output usage already includes reasoning tokens;
these are not counted twice. Decision's single token rate is applied to reported
input plus output tokens. Prices are selected using the response's actual model
identifier, not an arbitrary deployment name.

These are **estimates, not billing totals**. Deployment type, region, long context,
and negotiated discounts can change prices. Failed, retried, or stopped requests
can incur charges not captured by completed responses. Prices are a dated snapshot,
not automatically refreshed; update them or set the optional USD-per-million
overrides in `.env.example` to match your deployment. For the LLM, set input and
output overrides together; also set cached-input/cache-write rates if those token
categories are used.

Mock runs, unknown models without overrides, and missing or invalid usage show
**Unavailable**, never a fabricated zero or partial cost comparison.

## Tests

```bash
python -m pytest
```

UI behavior tests (requires Node.js):

```bash
node --test tests/test_ui.cjs
```
