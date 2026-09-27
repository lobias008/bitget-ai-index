import { useEffect, useMemo, useState } from "react";

const API = "https://api.bitget.com/api/v2/mix/market/ticker";
const REFRESH_MS = 10000;

export function useLivePrices(registry) {
  const [prices, setPrices] = useState({});

  const symbols = useMemo(
    () =>
      (registry.instruments || [])
        .filter(
          (instrument) =>
            instrument.verified &&
            instrument.verified_metadata?.market_data_available
        )
        .map((instrument) => instrument.symbol),
    [registry]
  );

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      const results = await Promise.allSettled(
        symbols.map(async (symbol) => {
          const url =
            `${API}?symbol=${encodeURIComponent(symbol)}` +
            `&productType=USDT-FUTURES`;

          const response = await fetch(url);

          if (!response.ok) {
            throw new Error(`${symbol}: HTTP ${response.status}`);
          }

          const payload = await response.json();

          if (payload.code !== "00000" || !Array.isArray(payload.data)) {
            throw new Error(`${symbol}: invalid Bitget response`);
          }

          const ticker = payload.data[0];
          const price = Number(ticker?.lastPr);

          if (!Number.isFinite(price)) {
            throw new Error(`${symbol}: invalid price`);
          }

          return {
            symbol,
            price,
            updatedAt: Number(ticker.ts || Date.now()),
          };
        })
      );

      if (cancelled) return;

      setPrices((current) => {
        const next = { ...current };

        for (const result of results) {
          if (result.status === "fulfilled") {
            next[result.value.symbol] = result.value;
          }
        }

        return next;
      });
    }

    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [symbols]);

  return useMemo(
    () => ({
      ...registry,
      live_prices: true,
      instruments: (registry.instruments || []).map((instrument) => ({
        ...instrument,
        price: prices[instrument.symbol]?.price ?? null,
        price_updated_at:
          prices[instrument.symbol]?.updatedAt ?? null,
      })),
    }),
    [registry, prices]
  );
}
