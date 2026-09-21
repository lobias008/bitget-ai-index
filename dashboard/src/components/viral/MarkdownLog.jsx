export default function MarkdownLog({ strategy, lang }) {
  if (!strategy) {
    return lang === "zh" ? "## AgentX 构建日志\n\n等待第一个策略。" : "## AgentX Build Log\n\nWaiting for the first strategy.";
  }
  const copy = strategy.i18n?.[lang] || strategy;

  if (lang === "zh") {
    return `## AgentX 构建日志：${copy.title}\n\n### 原始意图\n${strategy.intent}\n\n### Agent 解析\n- 节点 ID：${strategy.id}\n- 触发条件：${copy.trigger}\n- 执行动作：${copy.action}\n- 风险评分：${strategy.risk}\n- 置信度：${strategy.confidence}%\n\n### Demo 亮点\n自然语言输入、策略节点、宏观数据与 bgc CLI 载荷在一个高性能交易驾驶舱中闭环。`;
  }

  return `## AgentX Build Log: ${copy.title}\n\n### Raw Intent\n${strategy.intent}\n\n### Agent Parse\n- Node ID: ${strategy.id}\n- Trigger: ${copy.trigger}\n- Action: ${copy.action}\n- Risk Score: ${strategy.risk}\n- Confidence: ${strategy.confidence}%\n\n### Demo Hook\nNatural-language input, strategy nodes, macro data, and bgc CLI payloads complete the loop inside one high-performance trading cockpit.`;
}
