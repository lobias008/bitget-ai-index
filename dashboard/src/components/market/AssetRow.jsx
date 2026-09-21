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
      <div className="rounded-lg border border-neon/15 bg-black/40 p-3 text-center">
        <p className="text-[10px] uppercase text-steel">{t.price}</p>
        <p className="font-mono text-sm text-neon">{t.pricesPending}</p>
      </div>
    </div>
  );
}
