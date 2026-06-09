import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0f0f0f",
          1: "#1a1a1a",
          2: "#242424",
          3: "#2e2e2e",
        },
        accent: {
          DEFAULT: "#7eb8f7",
          muted: "#4a7fb5",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
