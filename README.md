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

### Deployment

Run these commands from the repository root in **Bash**, using Azure public
cloud. Nothing below requires local Docker. These files recreate the demo:

- [`infra/hosting.bicep`](infra/hosting.bicep): Basic Container Registry,
  user-assigned identity, and Consumption Container Apps environment without a
  Log Analytics workspace.
- [`infra/models.bicep`](infra/models.bicep): Foundry account and the two model
  deployments used by the live demo on **November 19, 2026**: `gpt-6.1-sol`
  version `2026-09-29`, capacity `10`, and `Microsoft-Decision-1.1` version `1`,
  capacity `2160`; both `DataZoneStandard`.
- [`scripts/deploy.py`](scripts/deploy.py): applies the hosting template,
  grants registry pull access, builds the current checkout in Azure, and
  creates/updates the app, its secrets, HTTPS ingress, probes, and replica settings.

Capacity values are Azure deployment SKU units, not raw tokens per minute.
Recreation requires model access, regional availability, and sufficient quota.
If a pinned version is retired, update the model parameters explicitly; the
template does not silently substitute another model or deployment type.
The model template restores only these two models, not unrelated deployments,
Foundry projects, or other resources in an existing account.

**1. Prepare the tools and subscription.** Install
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) and Python
3.10+ first, then run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
az login
az account set --subscription "<your-subscription-id>"
LOCATION=centralus
az extension add --name containerapp --upgrade
az bicep install
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.ContainerRegistry --wait
az provider register --namespace Microsoft.ManagedIdentity --wait
az provider register --namespace Microsoft.CognitiveServices --wait
```

Use an identity with resource creation and role-assignment permissions
(for example, subscription Owner). When using `--model-account`, it also needs
permission to read that account's keys. Deploying a new model may require accepting
its terms/access requirements in Foundry first.

`LOCATION` defaults to `centralus`. To use another region, set
`LOCATION="<region>"` in the same shell before continuing. Use the same region
as your Foundry account when using existing models.

**2a. Create the models only if you need them.** Skip this step if using
an existing Foundry account and deployments.

Replace `<demo-resource-group>` with your dedicated hosting group name and
`<model-resource-group>` with your separate models group name throughout these
examples.

```bash
az group create --name "<model-resource-group>" --location "$LOCATION"
az deployment group create \
  --resource-group "<model-resource-group>" \
  --name decision-models \
  --template-file infra/models.bicep \
  --parameters location="$LOCATION"
MODEL_RESOURCE_GROUP="<model-resource-group>"
MODEL_ACCOUNT=$(az deployment group show \
  --resource-group "$MODEL_RESOURCE_GROUP" --name decision-models \
  --query properties.outputs.accountName.value --output tsv)
```

The template generates a unique account name for this resource group. If that
name is unavailable or still soft-deleted after a previous teardown, choose a
new globally unique name by adding `accountName=<new-unique-name>` to the
deployment's `--parameters`. On every subsequent deployment, pass the same
`accountName` and any other overrides again; omitted parameters use their defaults.
Other model settings can be overridden on the same command, for example
`llmCapacity=10 decisionCapacity=2160`.

**If using existing models instead of deploying new ones**, set these 
variables rather than running step 2. Replace `<model-resource-name>` with your
existing Foundry account name:

```bash
MODEL_RESOURCE_GROUP="<model-resource-group>"
MODEL_ACCOUNT="<model-resource-name>"
```

**3. Rebuild and deploy the app.** In the same shell:

```bash
python scripts/deploy.py \
  --location "$LOCATION" \
  --resource-group "<demo-resource-group>" \
  --name decision-sidebyside \
  --model-resource-group "$MODEL_RESOURCE_GROUP" \
  --model-account "$MODEL_ACCOUNT" \
  --llm-deployment gpt-6.1-sol \
  --decision-deployment decision-1.1
```

This works without a `.env`: the script checks both model deployments, retrieves
the account's current endpoints/key, and passes the key as Container Apps
secrets without printing it or saving it to the repository. For accurate cost
comparisons with the Data Zone Standard deployments, configure your region's
pricing overrides in `.env` (see [How cost is estimated](#how-cost-is-estimated));
otherwise the app uses Global Standard rates. Any `.env` API-version overrides
are also used; model account arguments override its model endpoints, keys, and
deployment names. Adjust
`--llm-deployment` / `--decision-deployment` if you customized deployment names.
Use the same hosting group and app name on subsequent runs. The script prints
the public URL after the health check succeeds. A rebuilt app may have a
different URL after deletion; no custom domain or DNS is restored.

### Deploy using manually configured model settings

The demo can run entirely in Azure; only the UI runs in your browser. The
deployment script builds the Linux image in Azure Container Registry (no local
Docker required) and deploys a **public HTTPS URL** in your selected region.
The existing model deployments are not moved or recreated.

With the virtual environment activated, set `LOCATION` (default: `centralus`).
For another region, replace the assignment with `LOCATION="<region>"`, using
the same region as your Foundry account:

```bash
az login
az account set --subscription "<your-subscription-id>"
LOCATION=centralus
python scripts/deploy.py --location "$LOCATION" --resource-group "<demo-resource-group>" --name "<demo-app-name>"
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

### Tear down to avoid ongoing hosting charges

Delete **only the dedicated demo hosting group**. This removes the warm app,
environment, registry/images, and pull identity, but not models in other groups.
Stopping the app alone does not remove all hosting resources.

```bash
az group delete --name "<demo-resource-group>"
```

If you created the **dedicated models group in step 2** and no longer need it,
delete that too:

```bash
az group delete --name "<model-resource-group>"
```

These commands prompt for confirmation. Replace the placeholders with your actual
dedicated group names. **Do not delete a shared models resource group wholesale**:
it may also contain model deployments beyond this demo. Only run the model-group
deletion command for a dedicated group created for this demo. If you retained a
shared account, manage its unused deployments separately.
Azure may retain soft-deleted Foundry accounts; follow the
[recovery/purge guidance](https://learn.microsoft.com/azure/ai-services/recover-purge-resources)
if you need to reuse a deleted name.

Before teardown, keep this checkout and any `.env` pricing overrides in a secure
local location. Do not commit keys. Deleted registry images are rebuilt from the
checkout, and newly created model accounts have new keys. To restore, repeat
steps 1–3 (or skip model creation if those resources remain). Azure deployment
operations are not transactional: if one fails partway, inspect the target
groups, fix the reported error, then rerun or delete the dedicated groups.

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

The snapshot in `pricing.py` was verified on **November 17, 2026** against
[Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/)
and the [Azure Retail Prices API](https://prices.azure.com/api/retail/prices).
Rates are USD per **1 million tokens**:

| Model / pricing assumption | Input | Cached input | Cache writes | Output |
| --- | --- | --- | --- | --- |
| GPT-6.1 Sol, Global Standard, short context | $2.00 | $0.10 | $2.50 | $10.00 |
| Microsoft-Decision-1, Global Standard | $0.042 | N/A | N/A | $0.042 |
| Microsoft-Decision-1.1, Global Standard | $0.042 | N/A | N/A | $0.042 |

The Retail Prices API publishes Decision-1.1 under product **Microsoft Decision
Models**, SKU **Decision 1.1 Gl**, meter **Decision 1.1 Gl 1M Tokens**.  
The Decision-1 meter is **Microsoft Decision 1 1M Tokens**.
GPT-6.1 Sol's short-context Global meters use SKU names starting with
**6.1-sol ShortCo** and ending with **Std Gl**, under **Azure OpenAI GPT6**.

To reproduce the lookup, query the API for
[Decision meters](https://prices.azure.com/api/retail/prices?$filter=productName%20eq%20%27Microsoft%20Decision%20Models%27)
or [GPT-6.1 meters in East US](https://prices.azure.com/api/retail/prices?$filter=contains%28skuName%2C%20%276.1%27%29%20and%20armRegionName%20eq%20%27eastus%27).
Select USD Consumption meters with unit `1M`, matching your SKU and region.

**Data Zone Standard is not priced at the Global defaults.** For the comparable
benchmark setup above, configure overrides using your region's published rates.
These reference rates were also checked on November 17, 2026; they are not
automatically selected by the app:

| Model / pricing assumption | Input | Cached input | Cache writes | Output |
| --- | --- | --- | --- | --- |
| GPT-6.1 Sol, Data Zone Standard, short context, East US | $2.20 | $0.11 | $2.75 | $11.00 |
| Microsoft-Decision-1.1, Data Zone Standard, East US | $0.046 | N/A | N/A | $0.046 |
| Microsoft-Decision-1.1, Data Zone Standard, West Europe | $0.050 | N/A | N/A | $0.050 |

Decision-1.1 Data Zone uses SKU **Decision 1.1 DZ**. Set
`DECISION_USD_PER_MILLION` for Decision and all four
`AZURE_OPENAI_*_USD_PER_MILLION` overrides for the LLM's applicable token categories.

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
