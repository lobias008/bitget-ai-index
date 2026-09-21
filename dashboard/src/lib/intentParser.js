function hasAny(text, terms) {
  return terms.some((term) => text.includes(term));
}

export function parseIntent(input, lang) {
  const normalized = input.toLowerCase();
  const isZh = lang === "zh";
  const asset = hasAny(normalized, ["btc", "bitcoin", "比特币"]) ? "BTC" : "BGB";
  const macro = hasAny(normalized, ["cpi", "inflation", "通胀"]);
  const hedge = hasAny(normalized, ["hedge", "sell", "reject", "drops", "对冲", "卖出", "失败", "下跌"]);
  const buy = hasAny(normalized, ["buy", "dca", "买入"]);
  const volume = hasAny(normalized, ["volume", "massive", "成交量", "放量"]);
  const ath = hasAny(normalized, ["all-time high", "ath", "历史新高"]);
  const id = `agentx-${asset.toLowerCase()}-${Date.now().toString(36).slice(-5)}`;
  const numeric = normalized.match(/(\d+(?:\.\d+)?)\s*(k|m|%|万)?/i);
  const threshold = numeric ? `${numeric[1]}${numeric[2] || ""}` : ath ? "ATH" : asset === "BTC" ? "100K" : "1.85";

  const trigger = macro
    ? isZh
      ? `CPI 低于 ${threshold.includes("%") ? threshold : `${threshold}%`} 且市场情绪转强`
      : `CPI cools below ${threshold.includes("%") ? threshold : `${threshold}%`} with improving sentiment`
    : isZh
      ? `${asset}/USDT ${ath ? "突破历史新高" : `触达 ${threshold}`} ${volume ? "并出现放量确认" : "并通过流动性确认"}`
      : `${asset}/USDT ${ath ? "breaks all-time high" : `touches ${threshold}`} ${volume ? "with volume confirmation" : "with liquidity confirmation"}`;

  const action = hedge
    ? isZh
      ? `将 ${asset} 风险敞口自动对冲至 USDT，并发布执行摘要`
      : `Auto-hedge ${asset} exposure into USDT and publish an execution brief`
    : buy
      ? isZh
        ? `通过 Bitget 执行分批买入，并向创作者频道发送信号`
        : `Execute staged Bitget buy orders and broadcast a creator signal`
      : isZh
        ? "触发高优先级预警，生成 bgc CLI 载荷与传播日志"
        : "Trigger high-priority alert, generate bgc CLI payload, and draft a builder log";
  const triggerEn = macro
    ? `CPI cools below ${threshold.includes("%") ? threshold : `${threshold}%`} with improving sentiment`
    : `${asset}/USDT ${ath ? "breaks all-time high" : `touches ${threshold}`} ${volume ? "with volume confirmation" : "with liquidity confirmation"}`;
  const triggerZh = macro
    ? `CPI 低于 ${threshold.includes("%") ? threshold : `${threshold}%`} 且市场情绪转强`
    : `${asset}/USDT ${ath ? "突破历史新高" : `触达 ${threshold}`} ${volume ? "并出现放量确认" : "并通过流动性确认"}`;
  const actionEn = hedge
    ? `Auto-hedge ${asset} exposure into USDT and publish an execution brief`
    : buy
      ? `Execute staged Bitget buy orders and broadcast a creator signal`
      : "Trigger high-priority alert, generate bgc CLI payload, and draft a builder log";
  const actionZh = hedge
    ? `将 ${asset} 风险敞口自动对冲至 USDT，并发布执行摘要`
    : buy
      ? `通过 Bitget 执行分批买入，并向创作者频道发送信号`
      : "触发高优先级预警，生成 bgc CLI 载荷与传播日志";

  return {
    id,
    intent: input,
    title: isZh ? `${asset} 智能策略节点` : `${asset} Intelligence Node`,
    trigger,
    action,
    i18n: {
      en: {
        title: `${asset} Intelligence Node`,
        trigger: triggerEn,
        action: actionEn,
      },
      zh: {
        title: `${asset} 智能策略节点`,
        trigger: triggerZh,
        action: actionZh,
      },
    },
    confidence: Math.floor(88 + Math.random() * 9),
    risk: Math.floor(34 + Math.random() * 38),
    createdAt: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    status: "active",
  };
}
