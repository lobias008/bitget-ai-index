import NeonPanel from "../ui/NeonPanel";
import AssetRow from "./AssetRow";

export default function AssetMatrix({ t, registry }) {
  return (
    <NeonPanel className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-black text-white">{t.assetMatrix}</h2>
        <span className="rounded-full border border-neon/30 px-3 py-1 font-mono text-xs text-neon">{t.registrySynced}</span>
      </div>
      <div className="space-y-5">
        {registry.asset_classes.map((section) => {
          const instruments = section.symbols
            .map((symbol) => registry.instruments.find((instrument) => instrument.symbol === symbol))
            .filter(Boolean);
          return (
            <div key={section.name}>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-bold uppercase tracking-widest text-steel">
                  {section.platform_section}
                </h3>
                {section.pending_verification ? (
                  <span className="rounded-full border border-crimson/30 px-3 py-0.5 font-mono text-[10px] uppercase text-crimson">
                    {t.pendingVerification}
                  </span>
                ) : (
                  <span className="rounded-full border border-neon/30 px-3 py-0.5 font-mono text-[10px] uppercase text-neon">
                    {section.symbol_count} {t.instrumentsUnit}
                  </span>
                )}
              </div>
              {section.pending_verification ? (
                <p className="rounded-lg border border-neon/15 bg-black/45 p-4 text-xs leading-relaxed text-steel">
                  {t.pendingVerificationBody}
                </p>
              ) : (
                <div className="grid gap-3">
                  {instruments.map((instrument) => (
                    <AssetRow key={instrument.symbol} instrument={instrument} t={t} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </NeonPanel>
  );
}
