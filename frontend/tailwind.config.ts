import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Unified with globals.css --bg-primary/--bg-secondary/--bg-tertiary
        dark: {
          900: "#06090e",
          800: "#0a1018",
          700: "#111925",
          600: "#1b2534",
        },
        // Brand palette anchored on the landing-page cyan (#67e8f9)
        brand: {
          300: "#67e8f9",
          400: "#22d3ee",
          500: "#06b6d4",
          600: "#0891b2",
          900: "#164e63",
        },
        accent: {
          cto: "#3b82f6",
          designer: "#a855f7",
          ops: "#22c55e",
          user: "#f97316",
          interviewer: "#ef4444",
        },
      },
    },
  },
  plugins: [],
};
export default config;
