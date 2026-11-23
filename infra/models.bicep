targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Globally unique Foundry account/subdomain. Use a new name if the old account is soft-deleted.')
param accountName string = 'decision-${uniqueString(resourceGroup().id)}'

// Snapshot of the two deployments used by the demo on November 19, 2026.
param llmDeploymentName string = 'gpt-6.1-sol'
param llmModelName string = 'gpt-6.1-sol'
param llmModelVersion string = '2026-09-29'
@minValue(1)
param llmCapacity int = 10
param decisionDeploymentName string = 'decision-1.1'
param decisionModelName string = 'Microsoft-Decision-1.1'
param decisionModelVersion string = '1'
@minValue(1)
param decisionCapacity int = 2160
param deploymentSku string = 'DataZoneStandard'

@allowed([
  'entra'
  'api-key'
])
param authMode string = 'entra'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    disableLocalAuth: authMode == 'entra'
    publicNetworkAccess: 'Enabled'
  }
}

resource llm 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: llmDeploymentName
  sku: {
    name: deploymentSku
    capacity: llmCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: llmModelName
      version: llmModelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource decision 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: decisionDeploymentName
  sku: {
    name: deploymentSku
    capacity: decisionCapacity
  }
  properties: {
    model: {
      format: 'Microsoft'
      name: decisionModelName
      version: decisionModelVersion
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [
    llm
  ]
}

output accountName string = account.name
output llmDeploymentName string = llm.name
output decisionDeploymentName string = decision.name
