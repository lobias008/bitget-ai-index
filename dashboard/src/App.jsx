import { useState } from "react";
import Shell from "./components/layout/Shell";
import CommandIntent from "./components/intent/CommandIntent";
import ActiveStrategies from "./components/intent/ActiveStrategies";
import CodeExportBox from "./components/intent/CodeExportBox";
import AssetMatrix from "./components/market/AssetMatrix";
import MacroPulse from "./components/market/MacroPulse";
import ViralDrawer from "./components/viral/ViralDrawer";
import { useBilingual } from "./hooks/useBilingual";
import { useIntentStrategies } from "./hooks/useIntentStrategies";
import registry from "./data/instruments.json";

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { lang, setLang, t, prompts } = useBilingual();
  const { strategies, latestStrategy, executeIntent, toggleStrategy, deleteStrategy } = useIntentStrategies(lang);

  return (
    <Shell t={t} lang={lang} setLang={setLang} registry={registry} onViralOpen={() => setDrawerOpen(true)}>
      <div className="grid gap-5 xl:grid-cols-[1.35fr_0.9fr]">
        <div className="space-y-5">
          <CommandIntent t={t} prompts={prompts} onExecute={executeIntent} />
          <CodeExportBox t={t} lang={lang} strategy={latestStrategy} />
          <AssetMatrix t={t} registry={registry} />
        </div>
        <div className="space-y-5">
          <MacroPulse t={t} registry={registry} />
          <ActiveStrategies
            t={t}
            lang={lang}
            strategies={strategies}
            onToggle={toggleStrategy}
            onDelete={deleteStrategy}
          />
        </div>
      </div>
      <ViralDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        t={t}
        lang={lang}
        strategy={latestStrategy}
      />
    </Shell>
  );
}
