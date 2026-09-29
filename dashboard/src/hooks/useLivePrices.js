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

          const change24h = Number(ticker?.change24h);
          const high24h = Number(ticker?.high24h);
          const low24h = Number(ticker?.low24h);
          const quoteVolume = Number(ticker?.quoteVolume);
          const markPrice = Number(ticker?.markPrice);

          return {
            symbol,
            price,
            change24h: Number.isFinite(change24h) ? change24h : null,
            high24h: Number.isFinite(high24h) ? high24h : null,
            low24h: Number.isFinite(low24h) ? low24h : null,
            quoteVolume: Number.isFinite(quoteVolume) ? quoteVolume : null,
            markPrice: Number.isFinite(markPrice) ? markPrice : null,
            direction:
              Number.isFinite(change24h)
                ? change24h > 0
                  ? "bullish"
                  : change24h < 0
                    ? "bearish"
                    : "flat"
                : "unknown",
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
        change_24h: prices[instrument.symbol]?.change24h ?? null,
        high_24h: prices[instrument.symbol]?.high24h ?? null,
        low_24h: prices[instrument.symbol]?.low24h ?? null,
        quote_volume_24h: prices[instrument.symbol]?.quoteVolume ?? null,
        mark_price: prices[instrument.symbol]?.markPrice ?? null,
        market_direction: prices[instrument.symbol]?.direction ?? "unknown",
        price_updated_at:
          prices[instrument.symbol]?.updatedAt ?? null,
      })),
    }),
    [registry, prices]
  );
}
