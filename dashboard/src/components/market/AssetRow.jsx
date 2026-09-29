export default function AssetRow({ instrument, t }) {
  const meta = instrument.verified_metadata;

  return (
    <div className="grid gap-3 rounded-lg border border-neon/15 bg-black/45 p-4 md:grid-cols-[1fr_1fr_1fr_1.3fr] md:items-center">
      <div>
        <p className="text-lg font-black text-white">{instrument.symbol}</p>
        <p className="text-xs uppercase text-steel">{instrument.display_name}</p>
      </div>
      <div>
        <p className="text-[10px] uppercase text-steel">{t.verification}</p>
        <p className={`font-mono text-sm font-bold ${instrument.verified ? "text-mint" : "text-crimson"}`}>
          {instrument.verified ? t.verifiedBadge : t.unverifiedBadge}
        </p>
        <p className="font-mono text-[10px] text-steel">{instrument.symbol_verification}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <p className="text-[10px] uppercase text-steel">{t.productType}</p>
          <p className="font-mono text-xs text-white">{meta?.product_type ?? t.unknown}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase text-steel">{t.tradingStatus}</p>
          <p className="font-mono text-xs text-white">{meta?.trading_status ?? t.notListed}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase text-steel">{t.execution}</p>
          <p className="font-mono text-xs text-mint">{instrument.execution_availability}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase text-steel">{t.riskProfileLabel}</p>
          <p className="font-mono text-xs text-white">{instrument.risk_profile}</p>
        </div>
      </div>
      <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
        <div className="text-center">
          <p className="text-[10px] uppercase text-steel">{t.price}</p>
          <p className="font-mono text-sm font-bold text-neon">
            {instrument.price != null
              ? Number(instrument.price).toLocaleString(undefined, {
                  maximumFractionDigits: instrument.verified_metadata?.price_precision ?? 8,
                })
              : t.pricesPending}
          </p>
        </div>

        {instrument.price != null && (
          <div className="mt-3 grid grid-cols-2 gap-2 border-t border-neon/10 pt-3 font-mono text-[10px]">
            <div>
              <p className="uppercase text-steel">24H</p>
              <p className={instrument.change_24h > 0 ? "text-mint" : instrument.change_24h < 0 ? "text-crimson" : "text-white"}>
                {instrument.change_24h != null
                  ? `${instrument.change_24h >= 0 ? "+" : ""}${(instrument.change_24h * 100).toFixed(2)}%`
                  : "--"}
              </p>
            </div>
            <div>
              <p className="uppercase text-steel">Direction</p>
              <p className={instrument.market_direction === "bullish" ? "text-mint" : instrument.market_direction === "bearish" ? "text-crimson" : "text-white"}>
                {instrument.market_direction ?? "unknown"}
              </p>
            </div>
            <div>
              <p className="uppercase text-steel">High / Low</p>
              <p className="text-white">
                {instrument.high_24h != null && instrument.low_24h != null
                  ? `${Number(instrument.high_24h).toLocaleString()} / ${Number(instrument.low_24h).toLocaleString()}`
                  : "--"}
              </p>
            </div>
            <div>
              <p className="uppercase text-steel">Mark</p>
              <p className="text-white">
                {instrument.mark_price != null ? Number(instrument.mark_price).toLocaleString() : "--"}
              </p>
            </div>
            <div className="col-span-2">
              <p className="uppercase text-steel">24H Quote Volume</p>
              <p className="text-white">
                {instrument.quote_volume_24h != null
                  ? Number(instrument.quote_volume_24h).toLocaleString(undefined, { maximumFractionDigits: 0 })
                  : "--"}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
