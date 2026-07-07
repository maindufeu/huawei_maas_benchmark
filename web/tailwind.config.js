/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#0b0f17",
          800: "#111827",
          700: "#1a2233",
          600: "#243044",
          500: "#334155",
        },
        accent: "#38bdf8",
      },
    },
  },
  plugins: [],
};
