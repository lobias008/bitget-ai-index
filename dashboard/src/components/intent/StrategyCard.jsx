import GlowButton from "../ui/GlowButton";
import MetricBadge from "../ui/MetricBadge";

export default function StrategyCard({ strategy, t, lang, onToggle, onDelete }) {
  const active = strategy.status === "active";
  const copy = strategy.i18n?.[lang] || strategy;

  return (
    <article className="rounded-lg border border-neon/20 bg-black/55 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-bold text-white">{copy.title}</h3>
          <p className="mt-1 font-mono text-xs text-steel">{strategy.id}</p>
        </div>
        <span
          className={`rounded-full border px-2 py-1 text-[10px] font-bold uppercase ${
            active ? "border-neon/40 text-neon" : "border-crimson/40 text-crimson"
          }`}
        >
          {active ? t.active : t.paused}
        </span>
      </div>

      <div className="mt-4 space-y-3 text-sm">
        <div>
          <p className="text-xs uppercase text-steel">{t.trigger}</p>
          <p className="mt-1 text-mint">{copy.trigger}</p>
        </div>
        <div>
          <p className="text-xs uppercase text-steel">{t.action}</p>
          <p className="mt-1 text-white">{copy.action}</p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2">
        <MetricBadge label={t.confidence} value={`${strategy.confidence}%`} />
        <MetricBadge label={t.risk} value={strategy.risk} tone={strategy.risk > 55 ? "crimson" : "emerald"} />
        <MetricBadge label={t.deployed} value={strategy.createdAt} />
      </div>

      <div className="mt-4 flex gap-2">
        <GlowButton className="flex-1 px-3" onClick={() => onToggle(strategy.id)}>
          {active ? t.pause : t.resume}
        </GlowButton>
        <GlowButton className="flex-1 px-3" variant="danger" onClick={() => onDelete(strategy.id)}>
          {t.terminate}
        </GlowButton>
      </div>
    </article>
  );
}
