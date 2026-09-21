import { formatCompact, formatPrice } from "../../lib/formatters";
import Sparkline from "./Sparkline";

export default function AssetRow({ asset, t }) {
  const positive = asset.change >= 0;

  return (
    <div className="grid gap-3 rounded-lg border border-neon/15 bg-black/45 p-4 md:grid-cols-[1fr_1fr_1fr_1.3fr] md:items-center">
      <div>
        <p className="text-lg font-black text-white">{asset.symbol}</p>
        <p className="text-xs uppercase text-steel">{t.volume}: {formatCompact(asset.volume)}</p>
      </div>
      <div>
        <p className="text-xs uppercase text-steel">{t.price}</p>
        <p className="font-mono text-2xl font-bold text-mint">{formatPrice(asset.price)}</p>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <p className="text-[10px] uppercase text-steel">{t.change}</p>
          <p className={`font-mono font-bold ${positive ? "text-neon" : "text-crimson"}`}>
            {positive ? "+" : ""}
            {asset.change}%
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase text-steel">{t.liquidity}</p>
          <p className="font-mono text-mint">{asset.liquidity.toFixed(0)}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase text-steel">{t.volatility}</p>
          <p className="font-mono text-white">{asset.volatility.toFixed(0)}</p>
        </div>
      </div>
      <Sparkline values={asset.history} tone={positive ? "emerald" : "crimson"} />
    </div>
  );
}
