import type { Config } from "tailwindcss";

export default {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Match the typst PDFs and the original pricing page.
        brand: {
          DEFAULT: "#0a5482",
          light:   "#e8f2f9",
        },
        ink:    "#101014",
        muted:  "#6e6e72",
        rule:   "#d8d8db",
        canvas: "#f5f5f7",
        ok:     "#1a7f4b",
        ok_light: "#e6f4ec",
        warn:   "#c0392b",
        warn_light: "#fdf0ef",
      },
      fontFamily: {
        sans: ['Inter', '"SF Pro Text"', '"Helvetica Neue"', 'Helvetica', 'Arial', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config;
