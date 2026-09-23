import { useCallback, useEffect, useState } from "react";

/**
 * Loads `dashboard/public/ai-summary.json`, written by `npm run ai:review`.
 *
 * Deliberately identical in shape to usePaperSummary: one-shot fetch with a
 * manual reload only, no polling timers, and a missing file is a first-class
 * "missing" state (the panel then explains how to generate a run). Nothing is
 * ever fabricated as a fallback. The summary is a static snapshot that always
 * carries `last_updated`; the UI presents it as PAPER / SIMULATED with a
 * veto-only AI reviewer, never as live data or as a real model opinion when
 * the run was synthetic.
 */
const SUMMARY_PATH = "ai-summary.json";

export function useAiSummary() {
  const [state, setState] = useState({ status: "loading", summary: null, error: null });

  const reload = useCallback(() => {
    let cancelled = false;
    fetch(SUMMARY_PATH, { cache: "no-store" })
      .then(async (response) => {
        if (response.status === 404) {
          const missing = new Error("ai-summary.json not found");
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
