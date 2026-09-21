function localized(strategy, lang) {
  return strategy?.i18n?.[lang] || strategy;
}

export function createPayload(strategy, lang = "en") {
  if (!strategy) return "";
  const copy = localized(strategy, lang);

  return JSON.stringify(
    {
      agentId: strategy.id,
      protocol: "bitget.agentx.intent.v1",
      chain: "bitget-mainnet",
      execution: {
        mode: "SIMULATED_AUTONOMOUS",
        priority: "HIGH",
        gasBudget: "0.05 BGB",
      },
      strategy: {
        name: copy.title,
        sourceIntent: strategy.intent,
        trigger: copy.trigger,
        action: copy.action,
        riskScore: strategy.risk,
        confidence: strategy.confidence,
      },
    },
    null,
    2,
  );
}

export function createCli(strategy, lang = "en") {
  if (!strategy) return "";
  const copy = localized(strategy, lang);

  return `# Bitget bgc CLI - AgentX strategy deployment
bgc agentx deploy \\
  --agent-id "${strategy.id}" \\
  --strategy "${copy.title}" \\
  --intent "${strategy.intent}" \\
  --trigger "${copy.trigger}" \\
  --action "${copy.action}" \\
  --priority HIGH \\
  --risk-score ${strategy.risk} \\
  --confirm`;
}
