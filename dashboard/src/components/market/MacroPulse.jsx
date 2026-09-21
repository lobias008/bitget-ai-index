import NeonPanel from "../ui/NeonPanel";
import MetricBadge from "../ui/MetricBadge";
import Sparkline from "./Sparkline";
import SentimentMeter from "./SentimentMeter";

export default function MacroPulse({ t, macro }) {
  const riskOn = macro.trend === "risk-on";

  return (
    <NeonPanel className="p-5" accent={riskOn ? "emerald" : "crimson"}>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-black text-white">{t.macroPulse}</h2>
        <span className={`rounded-full border px-3 py-1 font-mono text-xs ${riskOn ? "border-neon/30 text-neon" : "border-crimson/30 text-crimson"}`}>
          {riskOn ? t.riskOn : t.riskOff}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricBadge label={t.cpi} value={`${macro.cpi.toFixed(1)}%`} />
        <MetricBadge label={t.inflation} value={`${macro.inflation.toFixed(2)}%`} tone={macro.inflation > 3 ? "crimson" : "emerald"} />
        <MetricBadge label={t.fedRate} value={`${macro.fedRate.toFixed(2)}%`} />
      </div>

      <div className="mt-5 space-y-4">
        <SentimentMeter label={t.sentiment} value={macro.sentiment} />
        <SentimentMeter label={t.liquidity} value={macro.liquidity.toFixed(1)} />
      </div>

      <div className="mt-5 rounded-lg border border-neon/15 bg-black/40 p-3">
        <Sparkline values={macro.history} tone={riskOn ? "emerald" : "crimson"} />
      </div>
    </NeonPanel>
  );
}
