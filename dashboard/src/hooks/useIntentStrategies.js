import { useMemo, useState } from "react";
import { parseIntent } from "../lib/intentParser";

export function useIntentStrategies(lang) {
  const [strategies, setStrategies] = useState([]);

  function executeIntent(intent) {
    const trimmed = intent.trim();
    if (!trimmed) return null;
    const strategy = parseIntent(trimmed, lang);
    setStrategies((current) => [strategy, ...current].slice(0, 6));
    return strategy;
  }

  function toggleStrategy(id) {
    setStrategies((current) =>
      current.map((strategy) =>
        strategy.id === id
          ? { ...strategy, status: strategy.status === "active" ? "paused" : "active" }
          : strategy,
      ),
    );
  }

  function deleteStrategy(id) {
    setStrategies((current) => current.filter((strategy) => strategy.id !== id));
  }

  const latestStrategy = useMemo(() => strategies[0] || null, [strategies]);

  return { strategies, latestStrategy, executeIntent, toggleStrategy, deleteStrategy };
}
