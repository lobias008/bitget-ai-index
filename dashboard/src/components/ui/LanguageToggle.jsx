export default function LanguageToggle({ lang, setLang }) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-neon/25 bg-black/50 p-1">
      {[
        ["en", "EN"],
        ["zh", "中文"],
      ].map(([value, label]) => (
        <button
          key={value}
          type="button"
          onClick={() => setLang(value)}
          className={`rounded-full px-3 py-1 text-xs font-bold transition ${
            lang === value ? "bg-neon text-black shadow-neon" : "text-steel hover:text-mint"
          }`}
          aria-pressed={lang === value}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
