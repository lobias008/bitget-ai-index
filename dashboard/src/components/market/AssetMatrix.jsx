import NeonPanel from "../ui/NeonPanel";
import AssetRow from "./AssetRow";

export default function AssetMatrix({ t, assets }) {
  return (
    <NeonPanel className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-black text-white">{t.assetMatrix}</h2>
        <span className="rounded-full border border-neon/30 px-3 py-1 font-mono text-xs text-neon">STREAMING</span>
      </div>
      <div className="grid gap-3">
        {assets.map((asset) => (
          <AssetRow key={asset.symbol} asset={asset} t={t} />
        ))}
      </div>
    </NeonPanel>
  );
}
