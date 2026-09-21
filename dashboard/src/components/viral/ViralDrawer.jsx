import { useMemo, useState } from "react";
import GlowButton from "../ui/GlowButton";
import TweetPreview from "./TweetPreview";
import MarkdownLog from "./MarkdownLog";

export default function ViralDrawer({ open, onClose, t, lang, strategy }) {
  const [tab, setTab] = useState("tweet");
  const content = useMemo(() => {
    return tab === "tweet" ? TweetPreview({ strategy, lang }) : MarkdownLog({ strategy, lang });
  }, [tab, strategy, lang]);

  async function copyContent() {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(content);
    }
  }

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/60 transition ${open ? "opacity-100" : "pointer-events-none opacity-0"}`}
        onClick={onClose}
      />
      <aside
        className={`drawer-shadow fixed right-0 top-0 z-50 h-full w-full max-w-xl border-l border-neon/25 bg-void/95 p-5 transition duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-mono text-xs uppercase text-neon">{t.navViral}</p>
            <h2 className="mt-1 text-2xl font-black text-white">{t.viralTitle}</h2>
            <p className="mt-2 text-sm text-steel">{t.viralSubtitle}</p>
          </div>
          <button className="rounded-md border border-white/15 px-3 py-2 text-sm text-steel" onClick={onClose}>
            {t.close}
          </button>
        </div>

        <div className="mt-6 flex gap-2">
          <button
            onClick={() => setTab("tweet")}
            className={`rounded-md px-3 py-2 text-sm font-bold ${tab === "tweet" ? "bg-neon text-black" : "bg-white/5 text-steel"}`}
          >
            {t.tweetPrompt}
          </button>
          <button
            onClick={() => setTab("log")}
            className={`rounded-md px-3 py-2 text-sm font-bold ${tab === "log" ? "bg-neon text-black" : "bg-white/5 text-steel"}`}
          >
            {t.markdownLog}
          </button>
        </div>

        <pre className="mt-4 h-[calc(100vh-220px)] overflow-auto whitespace-pre-wrap rounded-lg border border-neon/20 bg-black/70 p-4 font-mono text-sm leading-relaxed text-mint">
          {content}
        </pre>

        <div className="mt-4 flex justify-end">
          <GlowButton onClick={copyContent}>{t.copy}</GlowButton>
        </div>
      </aside>
    </>
  );
}
