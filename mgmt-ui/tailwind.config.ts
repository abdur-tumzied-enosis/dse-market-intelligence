import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ok: "#22c55e",
        warn: "#f59e0b",
        crit: "#ef4444",
        stale: "#6b7280",
      },
    },
  },
  plugins: [],
};
export default config;
