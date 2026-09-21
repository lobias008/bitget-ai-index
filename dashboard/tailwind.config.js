/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        void: "#020403",
        carbon: "#07100d",
        obsidian: "#0a0f0c",
        neon: "#00f58a",
        mint: "#8dffc8",
        crimson: "#ff254d",
        ember: "#ff6b4a",
        steel: "#8fa39a",
      },
      boxShadow: {
        neon: "0 0 22px rgba(0, 245, 138, 0.35)",
        crimson: "0 0 22px rgba(255, 37, 77, 0.28)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "SFMono-Regular", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
