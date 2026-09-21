export default function GlowButton({ children, className = "", variant = "primary", ...props }) {
  const styles =
    variant === "danger"
      ? "border-crimson/70 bg-crimson/15 text-white shadow-crimson hover:bg-crimson/25"
      : "border-neon/70 bg-neon/15 text-mint shadow-neon hover:bg-neon/25";

  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-bold uppercase tracking-normal transition ${styles} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
