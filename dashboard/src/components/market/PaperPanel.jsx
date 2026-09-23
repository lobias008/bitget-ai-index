import NeonPanel from "../ui/NeonPanel";
import GlowButton from "../ui/GlowButton";
import { usePaperSummary } from "../../hooks/usePaperSummary";

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function fmtSigned(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return (number > 0 ? "+" : "") + number.toFixed(digits);
}

// Colour reflects performance. An absolute balance is (almost) always
// positive, so toning off it would paint every run green - including losers.
function toneOf(value) {
  const number = Number(value);
  if (value === null || value === undefined || Number.isNaN(number) || number === 0) return "white";
  return number > 0 ? "mint" : "crimson";
}

function classificationTone(classification) {
  if (classification === "actionable_paper") return "text-mint";
  if (classification === "blocked") return "text-crimson";
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

export default function PaperPanel({ t }) {
  const { status, summary, error, reload } = usePaperSummary();

  return (
    <NeonPanel className="p-5" accent="emerald">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h2 className="text-xl font-black text-white">{t.paperTitle}</h2>
        <span className="rounded-full border border-crimson/40 px-3 py-1 font-mono text-xs text-crimson">
          {t.paperBadge}
        </span>
      </div>

      {status !== "ready" && (
        <div className="space-y-3">
          <p className="rounded-lg border border-neon/15 bg-black/40 p-4 font-mono text-xs leading-relaxed text-steel">
            {status === "missing" && t.paperMissing}
            {status === "error" && `${t.paperError} (${error})`}
            {status === "loading" && t.paperLoading}
          </p>
          <div className="flex justify-center">
            <GlowButton type="button" onClick={reload}>
              {t.paperReload}
            </GlowButton>
          </div>
        </div>
      )}

      {status === "ready" && summary && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <StatCard
              label={t.paperEquity}
              value={`${fmtNumber(summary.equity)} USDT`}
              tone={toneOf(summary.return_pct)}
            />
            <StatCard
              label={t.paperRealized}
              value={fmtSigned(summary.realized_pnl)}
              tone={toneOf(summary.realized_pnl)}
            />
            <StatCard
              label={t.paperUnrealized}
              value={fmtSigned(summary.unrealized_pnl)}
              tone={toneOf(summary.unrealized_pnl)}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <StatCard
              label={t.paperReturn}
              value={`${fmtSigned(summary.return_pct)}%`}
              tone={toneOf(summary.return_pct)}
            />
            <StatCard label={t.paperClosedTrades} value={String(summary.closed_trades ?? 0)} />
            <StatCard
              label={t.paperWinRate}
              value={
                summary.win_rate_pct === null || summary.win_rate_pct === undefined
                  ? "-"
                  : `${fmtNumber(summary.win_rate_pct, 1)}%`
              }
            />
          </div>

          <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-[10px] uppercase text-steel">{t.paperOpenPositions}</p>
              <p
                className={`font-mono text-[10px] ${
                  summary.circuit_breaker && summary.circuit_breaker.locked
                    ? "text-crimson"
                    : "text-neon"
                }`}
              >
                {summary.circuit_breaker && summary.circuit_breaker.locked
                  ? t.paperCbLocked
                  : t.paperCbArmed}
              </p>
            </div>
            {(!summary.open_positions || summary.open_positions.length === 0) && (
              <p className="font-mono text-xs text-steel">{t.paperNoPositions}</p>
            )}
            <ul className="space-y-2">
              {(summary.open_positions || []).map((position) => (
                <li
                  key={position.symbol}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-neon/10 bg-black/50 px-3 py-2 font-mono text-xs"
                >
                  <span className="font-bold text-white">{position.symbol}</span>
                  <span className="text-steel">{position.state}</span>
                  <span className="text-steel">
                    {t.paperQty} {fmtNumber(position.quantity, 4)}
                  </span>
                  <span className="text-steel">
                    {t.paperEntry} {fmtNumber(position.entry_price, 4)}
                  </span>
                  <span
                    className={
                      Number(position.unrealized_pnl) >= 0 ? "text-mint" : "text-crimson"
                    }
                  >
                    {fmtSigned(position.unrealized_pnl)}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
            <p className="mb-2 text-[10px] uppercase text-steel">{t.paperRecentSignals}</p>
            {(!summary.recent_signals || summary.recent_signals.length === 0) && (
              <p className="font-mono text-xs text-steel">{t.paperNoSignals}</p>
            )}
            <ul className="space-y-1">
              {(summary.recent_signals || [])
                .slice(-6)
                .reverse()
                .map((signal, index) => (
                  <li
                    key={`${signal.timestamp_ms}-${signal.symbol}-${index}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-neon/10 bg-black/50 px-3 py-1.5 font-mono text-[11px]"
                  >
                    <span className="text-steel">{signal.timestamp}</span>
                    <span className="font-bold text-white">{signal.symbol}</span>
                    <span className={classificationTone(signal.classification)}>
                      {signal.classification}
                    </span>
                    <span className="truncate text-steel">
                      {(signal.reason_codes || []).join(", ") || "-"}
                    </span>
                  </li>
                ))}
            </ul>
          </div>

          <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-[10px] uppercase text-steel">{t.paperCoverageTitle}</p>
              {summary.replay_funding === "neutral" ? (
                <span className="rounded-full border border-crimson/40 px-2 py-0.5 font-mono text-[10px] text-crimson">
                  {t.paperFundingNeutral}
                </span>
              ) : (
                <span className="rounded-full border border-neon/30 px-2 py-0.5 font-mono text-[10px] text-neon">
                  {t.paperFundingBlock}
                </span>
              )}
            </div>
            {!summary.data_coverage && (
              <p className="font-mono text-xs text-steel">{t.paperCovMissing}</p>
            )}
            {summary.data_coverage && (
              <ul className="space-y-2">
                {(summary.data_coverage.instruments || []).map((inst) => (
                  <li
                    key={inst.symbol}
                    className="rounded-md border border-neon/10 bg-black/50 px-3 py-2 font-mono text-[11px]"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-bold text-white">{inst.symbol}</span>
                      <span
                        className={
                          inst.data_eligibility === "backtest_eligible" ? "text-mint" : "text-crimson"
                        }
                      >
                        {inst.data_eligibility === "backtest_eligible"
                          ? t.paperEligBacktest
                          : t.paperEligPending}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-steel">
                      <span>
                        {inst.daily_bars} / {summary.data_coverage.min_daily_bars} {t.paperCovDailyBars}
                      </span>
                      <span>
                        {inst.four_hour_bars} / {summary.data_coverage.min_four_hour_bars} {t.paperCovFourHourBars}
                      </span>
                      <span>
                        {t.paperCovSteps}: {inst.steps_with_data}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-steel">
                      <span
                        className={
                          inst.signal_outcome === "actionable_setup"
                            ? "text-mint"
                            : inst.signal_outcome === "gated"
                            ? "text-crimson"
                            : "text-neon"
                        }
                      >
                        {inst.signal_outcome === "actionable_setup" && t.paperOutcomeActionable}
                        {inst.signal_outcome === "no_actionable_setup" && t.paperOutcomeNoSetup}
                        {inst.signal_outcome === "gated" && t.paperOutcomeGated}
                        {inst.signal_outcome === "not_evaluated" && t.paperOutcomeNotEvaluated}
                      </span>
                      {inst.informational > 0 && (
                        <span>
                          {t.paperCovWatches}: {inst.informational}
                        </span>
                      )}
                      {inst.blocked > 0 && (
                        <span>
                          {Object.entries(inst.blocked_reason_counts || {})
                            .map((entry) => `${entry[0]} \u00d7 ${entry[1]}`)
                            .join(", ")}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {summary.data_source === "synthetic" && (
            <p className="rounded-md border border-crimson/30 bg-crimson/10 p-2 text-center font-mono text-[11px] text-crimson">
              {t.paperSyntheticWarning}
            </p>
          )}

          <p className="font-mono text-[11px] leading-relaxed text-steel">
            {t.paperLastUpdated}: {summary.last_updated} · {t.paperDataSource}:{" "}
            {summary.data_source} · {summary.strategy_version} · {summary.mode}
          </p>
          <p className="text-center font-mono text-[11px] text-steel">{t.paperNotLive}</p>
          <div className="flex justify-center">
            <GlowButton type="button" onClick={reload}>
              {t.paperReload}
            </GlowButton>
          </div>
        </div>
      )}
    </NeonPanel>
  );
}