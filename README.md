# Decision Model Side By Side
Side-by-side comparison of Microsoft Decision model vs. a traditional large language model (LLM)

A small web app that classifies the same sample data **in parallel** with:

- **Left:** an Azure OpenAI chat model (prompted to answer with a single label)
- **Right:** [Microsoft Decision model](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) in Microsoft Foundry (a typed `choice` decision)

For each side the UI shows the prompt/request being executed, plus a single row of **wall clock time**, **number of model calls**, **average time per model call** (times in seconds), **estimated cost (USD)**, and **reported tokens**. A scrolling list shows each item and the label it was assigned. When both finish, each panel compares its speed and estimated cost.

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

The Decision side sends one typed `choice` question per item, as described in [Deploy and use Microsoft-Decision](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision):

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

## Run in Azure Container Apps

The demo can run entirely in Azure; only the UI runs in your browser. The
deployment script builds the Linux image in Azure Container Registry (no local
Docker required) and deploys a **public HTTPS URL** in your selected region.
The existing model deployments are not moved or recreated.

With the virtual environment activated:

```bash
az login
az account set --subscription "<your-subscription-id>"
python scripts/deploy.py --location "<region>" --resource-group "<demo-resource-group>" --name "<demo-app-name>"
```

Configure `.env` first, including both model API keys. The hosting script uses
API keys for the models; laptop Azure CLI credentials are not copied into the
container. Optional pricing overrides are carried over, and mock mode is disabled.

Your Azure identity needs permission to create a resource group, Container
Registry, managed identity, and Container Apps environment/app, plus assign the
identity `AcrPull` on the registry (for example, subscription Owner). The
`Microsoft.App`, `Microsoft.ContainerRegistry`, and `Microsoft.ManagedIdentity`
resource providers must be registered. Azure CLI must support `az containerapp`;
if needed, install it with `az extension add --name containerapp`.

Choose a dedicated demo resource group and the same region as your Foundry
resource (see the benchmarking setup below). Re-run the same command to rebuild
and update the app, including changes to `.env`. The script prints the URL only
after `/healthz` responds successfully.

The container uses Gunicorn with eight threads and one warm replica (1 vCPU,
2 GiB), capped at one replica. This keeps both model streams concurrent and
avoids scale-from-zero startup during the demo. Azure resources incur ongoing
charges, including the idle replica and registry. The public URL permits anyone
to invoke the models; it is intended for a demo, not an authenticated service.

The build context contains only runtime code, static assets, sample data, and
requirements, never `.env` or Git history. API keys are stored as Container Apps
secrets; registry pulls use a managed identity, not an admin password.

View recent application logs:

```bash
az containerapp logs show -g "<demo-resource-group>" -n "<demo-app-name>" --tail 50
```

After the demo, delete **only this dedicated demo resource group** to stop its
hosting/registry charges (this does not delete the models in other groups):

```bash
az group delete --name "<demo-resource-group>"
```

## Set up a comparable benchmark

1. **Keep the app and model resource in the same region.** Choose an Azure region
   where both models support the deployment type below and you have enough quota.
   Create a Microsoft Foundry resource there, deploy both models into it, and
   pass that region to `scripts/deploy.py --location`. Keep the model resource in
   a separate resource group if you want to retain it after deleting the demo.
2. **Match deployment types.** Select **Data Zone Standard**
   (SKU `DataZoneStandard`) for both the chat model and Microsoft Decision model.
   Use the same data zone; do not compare one Global Standard deployment against
   one Data Zone Standard deployment and attribute the entire difference to the
   models. Confirm the type on each deployment's details page. Availability is
   model- and region-dependent; do not silently substitute another deployment type.
3. **Point both clients at that resource.** Set these values in your local `.env`,
   using the actual deployment names and your resource's endpoints and key:

   ```dotenv
   AZURE_OPENAI_ENDPOINT=https://<foundry-resource>.openai.azure.com/
   AZURE_OPENAI_API_KEY=<resource-key>
   AZURE_OPENAI_DEPLOYMENT=<chat-deployment-name>
   DECISION_ENDPOINT=https://<foundry-resource>.services.ai.azure.com
   DECISION_API_KEY=<resource-key>
   DECISION_DEPLOYMENT=<decision-deployment-name>
   MOCK_MODELS=false
   ```

   Use the OpenAI endpoint for the chat client and the Foundry services endpoint
   for Decision, not a project URL. Re-run the deployment script to apply the
   settings to the hosted app; editing `.env` alone does not update Azure.
4. **Keep the workload fixed.** Use the same scenario, sample data, labels, and
   label descriptions for each run. Keep one call at a time per model, as this
   app does by default, and avoid other users or workloads exercising the
   deployments during the benchmark. Allocate enough quota to avoid throttling.
5. **Warm up, then repeat.** Run one complete batch after deployment and discard
   it as warm-up. Then run several complete batches under the same conditions.
   Record the median and range of the displayed server wall-clock and average
   per-call times, rather than reporting only the fastest run. Record failures
   and throttling separately; do not compare an incomplete side against a complete
   one. Check label quality alongside speed and cost.
6. **Record the configuration and pricing assumptions.** Note model versions,
   deployment types, region/data zone, quota/capacity, sample count, and whether
   calls were warm or cold. Prompt caching, output/reasoning token counts, SDK
   retries, and service load can affect results. Use the optional price overrides
   in `.env.example` for your actual Data Zone Standard rates: the built-in LLM
   price snapshot assumes Global Standard and is not automatically changed by the
   deployment SKU.

**Same-region resources do not guarantee same-region inference.**
Data Zone Standard dynamically routes inference within its permitted data zone,
not necessarily within the resource's region or to the same datacenter for both
models. Matching regions and deployment types controls obvious differences in
the setup, but this remains an end-to-end API benchmark, not an isolated model
compute benchmark. See Microsoft's
[deployment type documentation](https://learn.microsoft.com/azure/ai-foundry/foundry-models/concepts/deployment-types).

## How timing is measured

- **Wall clock**: measured on the server from the start of each side's batch
  through its final completed response, including server/app overhead. The
  browser displays the latest server-recorded time rather than a local timer.
- **Model calls**: one call per data item.
- **Model time** (calculated but hidden in the UI): the sum of the time spent inside each model API call, measured on the server.
- **Avg per call**: model time ÷ model calls.

Both sides run at the same time, and each side processes its items one call at a
time **on the server**, streaming prompts/results to the browser. Browser request
latency and slow result delivery do not gate the next model call or enter the
recorded batch time. Stop disconnects the streams; an in-flight call can still
finish and be billed before the server observes the disconnect.

Both timings include the **server-to-model** network hop, response parsing,
and any SDK retries; these are not pure model inference timings. Hosting in Azure
removes the laptop-to-model hop. Use the same-region, matching-deployment-type
setup above to reduce avoidable network differences; data-zone routing and
model cold starts can still affect comparisons.

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
| Microsoft-Decision-1.1, user-provided rate | $0.042 | N/A | N/A | $0.042 |

Decision-1.1 uses the same $0.042 per million token rate as Decision-1, based on
user-provided pricing context dated **November 10, 2026**. It is identified separately
from the November 6 published-price snapshot in the UI.
Both `microsoft-decision-1.1` and `dev-microsoft-decision-1.1` response model
identifiers use this rate, including their dated variants.

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
