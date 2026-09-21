export function formatPrice(value) {
  if (value > 1000) {
    return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  }
  return `$${value.toFixed(4)}`;
}

export function formatCompact(value) {
  return Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}
