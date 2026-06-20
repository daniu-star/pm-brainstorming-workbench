import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
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
        // shadcn/ui 兼容
        DEFAULT: "12px",
        xl: "16px",
        "2xl": "20px",
      },
      colors: {
        // shadcn/ui 语义令牌（通过 CSS 变量驱动，支持暗色模式）
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
          // 保留角色配色（非 shadcn 语义，用于角色标识）
          cto: "#3b82f6",
          designer: "#a855f7",
          ops: "#6b8f5e",
          user: "#f97316",
          interviewer: "#e8614d",
          coach: "#e0a02f",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // 保留 warm 主题色（向后兼容现有组件）
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
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [typography, animate],
};
export default config;
