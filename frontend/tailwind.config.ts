import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', '"Noto Sans SC"', '"PingFang SC"', '"Microsoft YaHei"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', '"SF Mono"', 'Consolas', 'monospace'],
      },
      borderRadius: {
        sm: "6px",
        md: "12px",
        lg: "16px",
        full: "9999px",
      },

      colors: {
        warm: {
          50: "#faf8f5",
          100: "#f5f0ea",
          200: "#e8e0d8",
          300: "#d4c8bb",
          400: "#b8a898",
          500: "#8b6f47",
          600: "#6b5535",
          700: "#4a3a24",
          800: "#2c2418",
          900: "#1a150e",
        },
        surface: {
          DEFAULT: "#faf8f5",
          elevated: "#ffffff",
          overlay: "#f5f0ea",
        },
        accent: {
          cto: "#3b82f6",
          designer: "#a855f7",
          ops: "#6b8f5e",
          user: "#f97316",
          interviewer: "#e8614d",
          coach: "#e0a02f",
        },
      },
    },
  },
  plugins: [typography],
};
export default config;
