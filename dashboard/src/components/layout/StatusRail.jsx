export default function StatusRail({ t, registry }) {
  const verifiedCount = registry.instruments.filter((instrument) => instrument.verified).length;
  const items = [
    `${t.executionMode}: ${registry.execution_mode}`,
    `${t.liveTrading}: ${t.disabled}`,
    `${t.instrumentsVerified}: ${verifiedCount}/${registry.instruments.length}`,
    `${t.sections}: ${registry.asset_classes.length}`,
    t.pricesPending,
  ];

  return (
    <div className="overflow-hidden border-y border-neon/15 bg-black/45 py-2">
      <div className="ticker-track flex w-max gap-8 whitespace-nowrap font-mono text-xs uppercase text-mint">
        {[...items, ...items].map((item, index) => (
          <span key={`${item}-${index}`} className="flex items-center gap-2">
            <span className="pulse-dot h-2 w-2 rounded-full bg-neon shadow-neon" />
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}
