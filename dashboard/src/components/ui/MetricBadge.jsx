export default function MetricBadge({ label, value, tone = "emerald" }) {
  const toneClass = tone === "crimson" ? "text-crimson border-crimson/30" : "text-neon border-neon/30";

  return (
    <div className={`rounded-md border bg-black/35 px-3 py-2 ${toneClass}`}>
      <p className="text-[10px] uppercase text-steel">{label}</p>
      <p className="mt-1 font-mono text-lg font-bold">{value}</p>
    </div>
  );
}
