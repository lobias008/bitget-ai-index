import NeonPanel from "../ui/NeonPanel";

export default function MacroPulse({ t, registry }) {
  const verifiedCount = registry.instruments.filter((instrument) => instrument.verified).length;

  return (
    <NeonPanel className="p-5" accent="emerald">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-black text-white">{t.macroPulse}</h2>
        <span className="rounded-full border border-neon/30 px-3 py-1 font-mono text-xs text-neon">{t.signalOnly}</span>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
          <p className="text-[10px] uppercase text-steel">{t.sections}</p>
          <p className="font-mono text-xl font-bold text-white">{registry.asset_classes.length}</p>
        </div>
        <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
          <p className="text-[10px] uppercase text-steel">{t.instrumentsVerified}</p>
          <p className="font-mono text-xl font-bold text-mint">
            {verifiedCount}/{registry.instruments.length}
          </p>
        </div>
        <div className="rounded-lg border border-neon/15 bg-black/40 p-3">
          <p className="text-[10px] uppercase text-steel">{t.executionMode}</p>
          <p className="font-mono text-xl font-bold text-neon">{registry.execution_mode}</p>
        </div>
      </div>

      <p className="mt-5 rounded-lg border border-neon/15 bg-black/40 p-4 text-center font-mono text-xs leading-relaxed text-steel">
        {t.macroPending}
      </p>
    </NeonPanel>
  );
}
