export default function NeonPanel({ children, className = "", accent = "emerald" }) {
  const accentClass =
    accent === "crimson" ? "border-crimson/35 shadow-crimson" : "border-neon/25 shadow-neon";

  return (
    <section
      className={`scanline relative overflow-hidden rounded-lg border bg-black/45 backdrop-blur-xl ${accentClass} ${className}`}
    >
      {children}
    </section>
  );
}
