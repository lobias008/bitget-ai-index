import { useState } from "react";
import GlowButton from "../ui/GlowButton";
import NeonPanel from "../ui/NeonPanel";
import PromptChips from "./PromptChips";

export default function CommandIntent({ t, prompts, onExecute }) {
  const [intent, setIntent] = useState("");

  function submit(event) {
    event.preventDefault();
    const created = onExecute(intent || prompts[0]);
    if (created) setIntent("");
  }

  return (
    <NeonPanel className="p-5 lg:p-6">
      <form onSubmit={submit} className="space-y-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs uppercase text-neon">{t.commandKicker}</p>
            <h2 className="mt-1 text-3xl font-black text-white sm:text-4xl">{t.commandTitle}</h2>
          </div>
          <span className="rounded-full border border-neon/30 bg-black/40 px-3 py-1 font-mono text-xs text-mint">
            {t.parserReady}
          </span>
        </div>

        <div className="rounded-lg border border-neon/30 bg-black/65 p-3">
          <textarea
            value={intent}
            onChange={(event) => setIntent(event.target.value)}
            placeholder={t.commandPlaceholder}
            className="min-h-28 w-full resize-none bg-transparent font-mono text-lg text-white outline-none placeholder:text-steel"
          />
          <div className="flex justify-end">
            <GlowButton type="submit">{t.execute}</GlowButton>
          </div>
        </div>

        <PromptChips t={t} prompts={prompts} onSelect={setIntent} />
      </form>
    </NeonPanel>
  );
}
