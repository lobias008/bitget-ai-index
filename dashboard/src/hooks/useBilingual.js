import { useMemo, useState } from "react";
import { prompts, translations } from "../data/translations";

export function useBilingual() {
  const [lang, setLang] = useState("en");

  return useMemo(
    () => ({
      lang,
      setLang,
      t: translations[lang],
      prompts: prompts[lang],
    }),
    [lang],
  );
}
