export default function Sparkline({ values, tone = "emerald" }) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 140;
      const y = 44 - ((value - min) / range) * 36;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const color = tone === "crimson" ? "#ff254d" : "#00f58a";

  return (
    <svg viewBox="0 0 140 48" className="h-12 w-full overflow-visible" role="img" aria-label="sparkline">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="spark-path"
      />
      <circle cx="140" cy={points.split(" ").at(-1)?.split(",")[1] || 24} r="3" fill={color} />
    </svg>
  );
}
