targetScope = 'resourceGroup'

param location string = resourceGroup().location
param appName string = 'decision-sidebyside'
param registryName string

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-pull'
  location: location
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${appName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: null
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output registry object = {
  id: registry.id
  loginServer: registry.properties.loginServer
}
output identity object = {
  id: identity.id
  principalId: identity.properties.principalId
}
output environmentId string = environment.id
