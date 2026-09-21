export default function StatusRail({ t, macro }) {
  const items = [
    t.statusOnline,
    t.activeFeeds,
    t.latency,
    `${t.sentiment}: ${macro.sentiment}`,
    `${t.liquidity}: ${macro.liquidity}`,
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
