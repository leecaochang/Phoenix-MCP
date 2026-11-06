/** The api functions the Settings windows call; mock `../api` with these (see settings_harness). */
export const SETTINGS_API = [
  "getSettings", "patchSettings", "getInfo", "getConversations", "listAccessKeys", "getAgentCliProviders", "getAgentCliModels",
  "getAiTaskPreferred", "setAiTaskPreferred", "clearAiTaskPreferred", "getAiEvaluatePreferred", "setAiEvaluatePreferred", "clearAiEvaluatePreferred", "getDecisionProviders", "getRoutingStatistics", "resetRoutingStatistics",
  "wipe", "createVoiceAgentPipeline", "deleteVoiceAgentPipeline", "setVoiceAgentPreferred", "refreshAgentCliProvider", "probeAgentCliCapabilities",
  "deleteAgentCliProvider", "setAgentCliProviderModel", "setAgentCliProviderAuto", "probeAgentCliProvider", "createAgentCliProvider", "getAudit",
] as const;
