import NeonPanel from "../ui/NeonPanel";
import GlowButton from "../ui/GlowButton";
import { useAiSummary } from "../../hooks/useAiSummary";

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function outcomeTone(outcome) {
  if (outcome === "accepted") return "text-mint";
  if (outcome === "vetoed" || outcome === "provider_error" || outcome === "schema_invalid")
    return "text-crimson";
  return "text-neon";
}

function decisionTone(decision) {
  if (decision === "confirm") return "text-mint";
  if (decision === "reject") return "text-crimson";
  return "text-neon";
}

function StatCard({ label, value, tone = "white" }) {
  const toneClass =
    tone === "crimson" ? "text-crimson" : tone === "mint" ? "text-mint" : "text-white";
  return (
    <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
      <p className="text-[10px] uppercase text-steel">{label}</p>
      <p className={`font-mono text-lg font-bold ${toneClass}`}>{value}</p>
    </div>
  );
}

export default function AiPanel({ t }) {
  const { status, summary, error, reload } = useAiSummary();
  const ai = status === "ready" && summary ? summary.ai || null : null;

  return (
    <NeonPanel className="p-5" accent="emerald">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h2 className="text-xl font-black text-white">{t.aiTitle}</h2>
        <span className="rounded-full border border-crimson/40 px-3 py-1 font-mono text-xs text-crimson">
          {t.aiBadge}
        </span>
      </div>

      {status !== "ready" && (
        <div className="space-y-3">
          <p className="rounded-lg border border-neon/15 bg-black/40 p-4 font-mono text-xs leading-relaxed text-steel">
            {status === "missing" && t.aiMissing}
            {status === "error" && `${t.aiError} (${error})`}
            {status === "loading" && t.aiLoading}
          </p>
          <div className="flex justify-center">
            <GlowButton type="button" onClick={reload}>
              {t.aiReload}
            </GlowButton>
          </div>
        </div>
      )}

      {status === "ready" && summary && (
        <div className="space-y-4">
          {!ai && (
            <p className="rounded-lg border border-neon/15 bg-black/40 p-4 font-mono text-xs leading-relaxed text-steel">
              {t.aiMissing}
            </p>
          )}

          {ai && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-neon/40 px-3 py-1 font-mono text-[10px] text-neon">
                  {t.aiRoleVetoOnly}
                </span>
                {ai.synthetic ? (
                  <span className="rounded-full border border-crimson/50 px-3 py-1 font-mono text-[10px] text-crimson">
                    {t.aiSynthetic}
                  </span>
                ) : null}
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <StatCard label={t.aiProvider} value={String(ai.provider ?? "-")} />
                <StatCard label={t.aiModel} value={String(ai.model ?? "-")} />
                <StatCard
                  label={`${t.aiCallsUsed} / ${t.aiMaxCalls}`}
                  value={`${ai.calls_used ?? 0} / ${ai.max_calls ?? 0}`}
                  tone={(ai.calls_used ?? 0) >= (ai.max_calls ?? 0) ? "crimson" : "white"}
                />
              </div>

              <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-[10px] uppercase text-steel">{t.aiOutcomes}</p>
                  <p className="font-mono text-[10px] text-steel">
                    {t.aiWatchSample}: {ai.watch_sample ?? 0}
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {Object.entries(ai.outcomes || {}).map((entry) => (
                    <div
                      key={entry[0]}
                      className="rounded-md border border-neon/10 bg-black/50 px-2 py-1.5"
                    >
                      <p className={`font-mono text-[10px] ${outcomeTone(entry[0])}`}>{entry[0]}</p>
                      <p className="font-mono text-sm font-bold text-white">{entry[1]}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
                <p className="mb-2 text-[10px] uppercase text-steel">{t.aiRecentReviews}</p>
                {(!ai.recent_reviews || ai.recent_reviews.length === 0) && (
                  <p className="font-mono text-xs text-steel">{t.aiNoReviews}</p>
                )}
                <ul className="space-y-2">
                  {(ai.recent_reviews || []).map((review, index) => (
                    <li
                      key={`${review.time_ms}-${review.symbol}-${index}`}
                      className="rounded-md border border-neon/10 bg-black/50 px-3 py-2 font-mono text-[11px]"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-bold text-white">{review.symbol}</span>
                        <span className="text-steel">
                          {review.purpose === "entry_gate"
                            ? t.aiPurposeEntryGate
                            : t.aiPurposeWatchSample}
                        </span>
                        <span className={decisionTone(review.decision)}>
                          {t.aiDecision}: {review.decision ?? "-"}
                        </span>
                        <span className={outcomeTone(review.outcome)}>
                          {t.aiOutcome}: {review.outcome}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-steel">
                        <span>
                          {t.aiConfidence}: {fmtNumber(review.confidence, 2)}
                        </span>
                        <span>
                          {t.aiReasonCode}: {review.reason_code ?? "-"}
                        </span>
                        <span>{review.time ?? "-"}</span>
                      </div>
                      {review.reasoning ? (
                        <p className="mt-1 leading-relaxed text-steel">
                          {t.aiReasoning}: {review.reasoning}
                        </p>
                      ) : null}
                      {review.risk_notes && review.risk_notes.length > 0 ? (
                        <p className="mt-1 leading-relaxed text-steel">
                          {t.aiRiskNotes}: {review.risk_notes.join(" | ")}
                        </p>
                      ) : null}
                      {review.purpose === "entry_gate" && review.outcome === "accepted" ? (
                        <p className="mt-1 leading-relaxed text-neon">
                          {t.aiSimulatorOutcome}: {review.simulator_outcome ?? "-"} ·{" "}
                          {t.aiEntryPrice}: {review.entry_price ?? "-"} · {t.aiQuantity}:{" "}
                          {review.quantity ?? "-"}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
                <p className="mb-2 text-[10px] uppercase text-steel">{t.aiRecentFills}</p>
                {(!ai.recent_fills || ai.recent_fills.length === 0) && (
                  <p className="font-mono text-xs text-steel">{t.aiNoFills}</p>
                )}
                <ul className="space-y-1">
                  {(ai.recent_fills || []).map((fill, index) => (
                    <li
                      key={`${fill.time_ms}-${fill.symbol}-${index}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-neon/10 bg-black/50 px-3 py-1.5 font-mono text-[11px] text-steel"
                    >
                      <span className="font-bold text-white">{fill.symbol}</span>
                      <span>{fill.side}</span>
                      <span>
                        {t.aiEntryPrice}: {fmtNumber(fill.fill_price, 4)}
                      </span>
                      <span>
                        {t.aiQuantity}: {fmtNumber(fill.quantity, 6)}
                      </span>
                      <span>
                        {t.aiSimulatorOutcome}: {fill.reason ?? "fill"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <p className="rounded-md border border-neon/20 bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-steel">
                {ai.disclaimer || t.aiDisclaimer}
              </p>

              <p className="font-mono text-[11px] leading-relaxed text-steel">
                {t.aiLastUpdated}: {summary.last_updated} · {t.aiDataSource}:{" "}
                {summary.data_source} · {summary.strategy_version} · {summary.mode}
              </p>
              <p className="text-center font-mono text-[11px] text-steel">{t.aiNotLive}</p>
            </>
          )}

          <div className="flex justify-center">
            <GlowButton type="button" onClick={reload}>
              {t.aiReload}
            </GlowButton>
          </div>
        </div>
      )}
    </NeonPanel>
  );
}
