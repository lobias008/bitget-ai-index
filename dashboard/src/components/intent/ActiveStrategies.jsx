import NeonPanel from "../ui/NeonPanel";
import StrategyCard from "./StrategyCard";

export default function ActiveStrategies({ t, lang, strategies, onToggle, onDelete }) {
  return (
    <NeonPanel className="p-5" accent="crimson">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-black text-white">{t.strategiesTitle}</h2>
        <span className="font-mono text-sm text-crimson">{strategies.length}/6</span>
      </div>

      {strategies.length === 0 ? (
        <div className="rounded-lg border border-dashed border-neon/25 p-6 text-sm text-steel">{t.strategyEmpty}</div>
      ) : (
        <div className="grid gap-3">
          {strategies.map((strategy) => (
            <StrategyCard
              key={strategy.id}
              strategy={strategy}
              t={t}
              lang={lang}
              onToggle={onToggle}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </NeonPanel>
  );
}
