export default function SentimentMeter({ label, value, suffix = "", danger = false }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="uppercase text-steel">{label}</span>
        <span className={danger ? "font-mono text-crimson" : "font-mono text-neon"}>
          {value}
          {suffix}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full ${danger ? "bg-crimson shadow-crimson" : "bg-neon shadow-neon"}`}
          style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
        />
      </div>
    </div>
  );
}
