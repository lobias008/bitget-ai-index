import { useMemo, useState } from "react";
import NeonPanel from "../ui/NeonPanel";
import { createCli, createPayload } from "../../lib/exportTemplates";

export default function CodeExportBox({ t, lang, strategy }) {
  const [tab, setTab] = useState("cli");
  const cli = useMemo(() => createCli(strategy, lang), [strategy, lang]);
  const payload = useMemo(() => createPayload(strategy, lang), [strategy, lang]);
  const value = tab === "cli" ? cli : payload;

  async function copyValue() {
    if (value && navigator.clipboard) {
      await navigator.clipboard.writeText(value);
    }
  }

  return (
    <NeonPanel className="p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-black text-white">{tab === "cli" ? t.codeTitle : t.payloadTitle}</h2>
        <div className="flex gap-2">
          <button
            className={`rounded-md px-3 py-1 text-xs font-bold ${tab === "cli" ? "bg-neon text-black" : "bg-white/5 text-steel"}`}
            onClick={() => setTab("cli")}
          >
            CLI
          </button>
          <button
            className={`rounded-md px-3 py-1 text-xs font-bold ${tab === "json" ? "bg-neon text-black" : "bg-white/5 text-steel"}`}
            onClick={() => setTab("json")}
          >
            JSON
          </button>
          <button className="rounded-md border border-neon/30 px-3 py-1 text-xs text-mint" onClick={copyValue}>
            {t.copy}
          </button>
        </div>
      </div>
      <pre className="max-h-96 overflow-auto rounded-lg border border-neon/20 bg-black/70 p-4 font-mono text-xs leading-relaxed text-mint">
        {value || t.codeEmpty}
      </pre>
    </NeonPanel>
  );
}
