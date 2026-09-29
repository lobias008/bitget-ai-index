import Header from "./Header";
import StatusRail from "./StatusRail";

export default function Shell({ children, t, lang, setLang, registry, onViralOpen }) {
  return (
    <div className="min-h-screen">
      <div className="grid-floor" />
      <Header t={t} lang={lang} setLang={setLang} onViralOpen={onViralOpen} />
      <StatusRail t={t} registry={registry} />
      <main className="mx-auto w-full max-w-[1800px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
    </div>
  );
}

