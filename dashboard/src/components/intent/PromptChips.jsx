export default function PromptChips({ t, prompts, onSelect }) {
  return (
    <div>
      <p className="mb-3 text-xs font-bold uppercase text-steel">{t.promptSuggestions}</p>
      <div className="flex flex-wrap gap-2">
        {prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onSelect(prompt)}
            className="rounded-full border border-neon/25 bg-neon/10 px-3 py-2 text-left text-xs text-mint transition hover:border-neon/70 hover:bg-neon/20"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}
