import { useEffect, useState } from "react";
import { initialAssets, initialMacro } from "../data/mockAssets";
import { nextAssets, nextMacro } from "../lib/mockStream";

export function useMockMarketStream() {
  const [assets, setAssets] = useState(initialAssets);
  const [macro, setMacro] = useState(initialMacro);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setAssets((current) => nextAssets(current));
      setMacro((current) => nextMacro(current));
    }, 1200);

    return () => window.clearInterval(timer);
  }, []);

  return { assets, macro };
}
