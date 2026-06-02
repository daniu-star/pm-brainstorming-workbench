import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      borderRadius: {
        sm: "6px",
        md: "12px",
        lg: "16px",
        full: "9999px",
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px" }],
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
        },
        surface: {
          DEFAULT: "#faf8f5",
          elevated: "#ffffff",
          overlay: "#f5f0ea",
        },
        accent: {
          cto: "#e07a2f",
          designer: "#a855f7",
          ops: "#6b8f5e",
          user: "#e07a2f",
          interviewer: "#e8614d",
          coach: "#e0a02f",
        },
      },
    },
  },
  plugins: [typography],
};
export default config;
