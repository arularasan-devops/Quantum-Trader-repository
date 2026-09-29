import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        panel: "#0d1117",
        panel2: "#131a24",
        line: "#1e2733",
        ink: "#e6edf3",
        muted: "#8b98a9",
        buy: "#16c784",
        wait: "#f4c025",
        hold: "#2f81f7",
        exit: "#f85149",
        notrade: "#f0883e",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
