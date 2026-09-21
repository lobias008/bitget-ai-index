export default function TweetPreview({ strategy, lang }) {
  if (!strategy) {
    return lang === "zh"
      ? "执行一个 AgentX 意图后，这里会生成推文提示。"
      : "Execute an AgentX intent to generate a tweet prompt.";
  }
  const copy = strategy.i18n?.[lang] || strategy;

  if (lang === "zh") {
    return `刚用 Bitget AgentX 把一句自然语言变成链上交易 Agent：\n\n"${strategy.intent}"\n\n输出：\n- 触发条件：${copy.trigger}\n- 执行动作：${copy.action}\n- 置信度：${strategy.confidence}%\n\nWeb3 交易界面正在从按钮进化为语言。#Bitget #AgentX #Web3`;
  }

  return `Just turned a natural-language trading idea into an autonomous Bitget AgentX node:\n\n"${strategy.intent}"\n\nOutput:\n- Trigger: ${copy.trigger}\n- Action: ${copy.action}\n- Confidence: ${strategy.confidence}%\n\nThe Web3 trading interface is shifting from buttons to language. #Bitget #AgentX #Web3`;
}
