import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#16161a",
          2: "#3a3a44",
          3: "#7a7a88",
        },
        paper: {
          DEFAULT: "#faf9f6",
          2: "#f0efe9",
        },
        accent: {
          DEFAULT: "#1e40af",
          2: "#2563eb",
        },
        long: {
          DEFAULT: "#15803d",
          bg: "#dcfce7",
        },
        short: {
          DEFAULT: "#dc2626",
          bg: "#fee2e2",
        },
        warn: {
          DEFAULT: "#b45309",
          bg: "#fef3c7",
        },
      },
      borderRadius: {
        DEFAULT: "6px",
      },
      fontFamily: {
        sans: [
          "Pretendard Variable",
          "Pretendard",
          "-apple-system",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
