import { clamp } from "./formatters";

export function nextAssets(assets) {
  return assets.map((asset) => {
    const isBgb = asset.symbol.startsWith("BGB");
    const delta = isBgb ? (Math.random() - 0.43) * 0.008 : (Math.random() - 0.5) * 140;
    const nextPrice = Number((asset.price + delta).toFixed(isBgb ? 4 : 2));
    const anchor = isBgb ? 1.75 : 97500;
    const nextHistory = [...asset.history.slice(-17), nextPrice];

    return {
      ...asset,
      price: nextPrice,
      change: Number((((nextPrice - anchor) / anchor) * 100).toFixed(2)),
      volume: Math.max(1, Math.round(asset.volume * (1 + (Math.random() - 0.48) * 0.025))),
      liquidity: clamp(asset.liquidity + (Math.random() - 0.46) * 2.4, 20, 99),
      volatility: clamp(asset.volatility + (Math.random() - 0.5) * 3, 10, 95),
      history: nextHistory,
    };
  });
}

export function nextMacro(macro) {
  const sentimentDelta = Math.random() > 0.65 ? (Math.random() > 0.42 ? 1 : -1) : 0;
  const liquidityDelta = (Math.random() - 0.46) * 0.18;
  const sentiment = clamp(macro.sentiment + sentimentDelta, 20, 98);
  const liquidity = Number((macro.liquidity + liquidityDelta).toFixed(2));

  return {
    ...macro,
    sentiment,
    liquidity,
    inflation: Number(clamp(macro.inflation + (Math.random() - 0.55) * 0.02, 2.1, 4.3).toFixed(2)),
    trend: sentiment > 62 && liquidity > 100 ? "risk-on" : "risk-off",
    history: [...macro.history.slice(-15), sentiment],
  };
}
