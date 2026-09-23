import { useCallback, useEffect, useState } from "react";

/**
 * Loads the generated paper-trading summary - the output of
 * `npm run paper:simulate`, exported to dashboard/public/paper-summary.json.
 *
 * One-shot fetch with a manual reload only: no polling timers, and a missing
 * file is a first-class "missing" state (the panel then explains how to
 * generate a run). Nothing is ever fabricated as a fallback. The summary is
 * a static snapshot that always carries `last_updated`; the UI presents it
 * as PAPER / SIMULATED, never as live data.
 */
const SUMMARY_PATH = "paper-summary.json";

export function usePaperSummary() {
  const [state, setState] = useState({ status: "loading", summary: null, error: null });

  const reload = useCallback(() => {
    let cancelled = false;
    fetch(SUMMARY_PATH, { cache: "no-store" })
      .then(async (response) => {
        if (response.status === 404) {
          const missing = new Error("paper-summary.json not found");
          missing.isMissing = true;
          throw missing;
        }
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then((summary) => {
        if (!cancelled) setState({ status: "ready", summary, error: null });
      })
      .catch((error) => {
        if (cancelled) return;
        setState({
          status: error && error.isMissing ? "missing" : "error",
          summary: null,
          error: error && error.message ? error.message : String(error),
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => reload(), [reload]);

  return { ...state, reload };
}