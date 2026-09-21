import LanguageToggle from "../ui/LanguageToggle";
import GlowButton from "../ui/GlowButton";

export default function Header({ t, lang, setLang, onViralOpen }) {
  return (
    <header className="sticky top-0 z-30 border-b border-neon/20 bg-void/80 backdrop-blur-2xl">
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-md border border-neon/50 bg-neon/10 font-mono text-lg font-black text-neon shadow-neon">
              AX
            </div>
            <div>
              <h1 className="text-xl font-black text-white sm:text-2xl">{t.brand}</h1>
              <p className="max-w-2xl text-sm text-steel">{t.tagline}</p>
            </div>
          </div>
        </div>

        <nav className="flex flex-wrap items-center gap-3">
          <span className="hidden text-xs uppercase text-steel md:inline">{t.navIntent}</span>
          <span className="hidden text-xs uppercase text-steel md:inline">{t.navMarket}</span>
          <LanguageToggle lang={lang} setLang={setLang} />
          <GlowButton onClick={onViralOpen}>{t.viralOpen}</GlowButton>
        </nav>
      </div>
    </header>
  );
}
